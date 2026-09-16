"""Persistent job worker: lease, timeout, retry, crash recovery.

Why a worker at all: retrieval can take tens of seconds (a 45 s budget is the
documented default), which must not happen inside an HTTP request. The contract
therefore models processing endpoints as async jobs — 202 plus polling.

Why *persistent*: a plain background task disappears on restart with no trace,
which the assignment explicitly rules out. Every state transition here is a
database write, so a job's fate is always inspectable after a crash:

    queued -> running -> succeeded | partial | failed
                      \\-> queued (retry, while attempts < MAX_ATTEMPTS)

Leasing is what makes recovery safe. A worker claims a job by writing
``lease_expires_at``; if the process dies mid-run the lease simply expires and
:func:`recover_stale_jobs` returns the job to ``queued``. Claiming is a
conditional UPDATE, so two workers cannot take the same job even though SQLite
offers no row locks.

Deliberate simplicity: one thread, one machine, SQLite — matching the
"single simple implementation for one machine" guidance. The concurrency limit
is one job at a time; that is a documented bound, not an accident.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.config import get_settings
from app.db import session_scope
from app.models import Job
from app.services.retrieval_service import (
    RetrievalInputError,
    RevisionConflict,
    run_retrieval,
)

logger = logging.getLogger("aurora_evidence.worker")

#: Attempts include the first try. Retrying a deterministic failure forever
#: would just burn CPU, so the cap is low and the last error is preserved.
MAX_ATTEMPTS = 3

#: Idle sleep between polls. Short enough to feel immediate in the UI, long
#: enough not to spin the CPU on an empty queue.
POLL_INTERVAL_SECONDS = 0.5

TERMINAL_STATUSES = {"succeeded", "partial", "failed"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: datetime | None) -> datetime | None:
    """SQLite may return naive datetimes; compare in UTC regardless."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def recover_stale_jobs() -> int:
    """Return jobs whose lease expired to ``queued``. Run this at startup.

    A job found ``running`` with an expired lease means the previous process
    died while holding it. Returning it to the queue is safe because results are
    only written in the final transaction — a half-finished run left nothing
    behind.
    """
    now = _utcnow()
    recovered = 0
    with session_scope() as session:
        stale = session.scalars(
            select(Job).where(Job.status == "running")
        ).all()
        for job in stale:
            expires = _as_aware(job.lease_expires_at)
            if expires is not None and expires > now:
                continue  # still held by a live worker
            if job.attempts >= MAX_ATTEMPTS:
                job.status = "failed"
                job.error_json = {
                    "code": "WORKER_LEASE_EXPIRED",
                    "message": (
                        "Lease pekerjaan kedaluwarsa dan batas percobaan tercapai; "
                        "job ditandai failed."
                    ),
                    "retryable": False,
                }
            else:
                job.status = "queued"
                job.lease_expires_at = None
            recovered += 1
    if recovered:
        logger.info("recovered %d stale job(s) after restart", recovered)
    return recovered


def _claim_next_job(worker_id: str, lease_seconds: int) -> str | None:
    """Atomically claim one queued job. Returns its ``job_id`` or ``None``.

    The UPDATE is guarded by ``status == "queued"``, so if another worker won the
    race our row count is zero and we simply look for the next candidate.
    """
    lease_until = _utcnow() + timedelta(seconds=lease_seconds)
    with session_scope() as session:
        candidate = session.scalar(
            select(Job).where(Job.status == "queued").order_by(Job.created_at.asc()).limit(1)
        )
        if candidate is None:
            return None
        result = session.execute(
            update(Job)
            .where(Job.job_id == candidate.job_id, Job.status == "queued")
            .values(
                status="running",
                lease_expires_at=lease_until,
                attempts=Job.attempts + 1,
                updated_at=_utcnow(),
            )
        )
        if result.rowcount != 1:
            return None  # lost the race
        logger.info("worker %s claimed job %s", worker_id, candidate.job_id)
        return candidate.job_id


def _process_job(job_id: str) -> None:
    """Run one claimed job to a terminal state (or back to ``queued``)."""
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None or job.status != "running":
            return
        payload = dict(job.request_json or {})
        payload_hash = job.payload_hash
        attempts = job.attempts

    try:
        with session_scope() as session:
            bundle, run_status = run_retrieval(
                session, bundle=payload, payload_hash=payload_hash
            )
            job = session.get(Job, job_id)
            if job is None:
                return
            # Contract: a partial run is a real result, reported as such.
            job.status = "succeeded" if run_status == "completed" else run_status
            if job.status not in TERMINAL_STATUSES:
                job.status = "partial"
            job.result_json = bundle
            job.error_json = None
            job.lease_expires_at = None
            job.updated_at = _utcnow()
        logger.info("job %s finished with status %s", job_id, run_status)

    except (RetrievalInputError, RevisionConflict) as exc:
        # Deterministic, caller-caused problems: retrying cannot help.
        code = (
            "REVISION_CONFLICT"
            if isinstance(exc, RevisionConflict)
            else "BUNDLE_CONTRACT_VIOLATION"
        )
        details = getattr(exc, "violations", []) or []
        _finalize_failure(
            job_id,
            code=code,
            message=str(exc),
            retryable=False,
            details={"violations": [str(v) for v in details[:20]]} if details else None,
        )

    except Exception as exc:  # noqa: BLE001 - worker must never die silently
        logger.exception("job %s raised", job_id)
        if attempts < MAX_ATTEMPTS:
            _requeue(job_id, reason=str(exc))
        else:
            _finalize_failure(
                job_id,
                code="WORKER_ERROR",
                message=f"Job gagal setelah {attempts} percobaan: {exc}",
                retryable=False,
            )


def _requeue(job_id: str, *, reason: str) -> None:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        job.status = "queued"
        job.lease_expires_at = None
        job.error_json = {
            "code": "WORKER_RETRY",
            "message": f"Percobaan gagal, dijadwalkan ulang: {reason}"[:500],
            "retryable": True,
        }
        job.updated_at = _utcnow()
    logger.warning("job %s requeued: %s", job_id, reason)


def _finalize_failure(
    job_id: str,
    *,
    code: str,
    message: str,
    retryable: bool,
    details: dict | None = None,
) -> None:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        job.status = "failed"
        job.result_json = None  # never present a failure as a result
        job.error_json = {
            "code": code,
            "message": message[:1000],
            "retryable": retryable,
            **({"details": details} if details else {}),
        }
        job.lease_expires_at = None
        job.updated_at = _utcnow()
    logger.error("job %s failed [%s]: %s", job_id, code, message)


class JobWorker:
    """Background worker thread, started with the API process by default."""

    def __init__(self, *, worker_id: str = "worker-1", lease_seconds: int | None = None) -> None:
        settings = get_settings()
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds or settings.job_timeout
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        recover_stale_jobs()
        self._thread = threading.Thread(target=self._loop, name=self.worker_id, daemon=True)
        self._thread.start()
        logger.info("worker %s started (lease %ss)", self.worker_id, self.lease_seconds)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                job_id = _claim_next_job(self.worker_id, self.lease_seconds)
                if job_id is None:
                    self._stop.wait(POLL_INTERVAL_SECONDS)
                    continue
                _process_job(job_id)
            except Exception:  # noqa: BLE001 - keep the loop alive
                logger.exception("worker loop error")
                self._stop.wait(POLL_INTERVAL_SECONDS)


def run_forever(worker_id: str = "worker-standalone") -> None:
    """Entry point for running the worker as its own process.

    Useful when you want the API to stay responsive under load, or to prove that
    job state really is shared through the database rather than process memory:

        python -m app.workers.worker
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
    settings = get_settings()
    recover_stale_jobs()
    logger.info("standalone worker %s polling (lease %ss)", worker_id, settings.job_timeout)
    try:
        while True:
            job_id = _claim_next_job(worker_id, settings.job_timeout)
            if job_id is None:
                time.sleep(POLL_INTERVAL_SECONDS)
                continue
            _process_job(job_id)
    except KeyboardInterrupt:
        logger.info("standalone worker stopped")


if __name__ == "__main__":
    run_forever()
