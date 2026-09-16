"""``POST /api/v1/retrieve`` — queue a retrieval run for an AuroraBundle.

Contract behaviour implemented here:

* Returns **202** with ``{job_id, case_id, claim_revision, status:"queued"}``.
  202 means "accepted", never "finished" — the caller polls the job.
* ``Idempotency-Key`` is mandatory. Same key + same payload returns the *same*
  job; same key + different payload is **409**. The payload is hashed with JCS
  (RFC 8785) *before* any server-side modification, so a client that re-sends
  the identical snapshot with keys in a different order still matches.
* Unsupported ``schema_version`` -> **422** ``SCHEMA_VERSION_UNSUPPORTED``.
* Contract violations -> **422** listing every violation at once.
* Same case/revision with different claim text or image -> **409**.

The actual work happens in the worker; this handler only validates, records
identity, and enqueues.
"""

from __future__ import annotations

from typing import Any

from aurora_evidence.contract.canonical import canonical_sha256
from aurora_evidence.contract.ids import new_job_id
from aurora_evidence.version import SCHEMA_VERSION
from fastapi import APIRouter, Body, Depends, Header
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_auth, validate_idempotency_key, violations_to_details
from app.config import get_settings
from app.db import get_session
from app.errors import ApiError, idempotency_conflict, revision_conflict, schema_version_unsupported
from app.models import Job
from app.services.retrieval_service import (
    RevisionConflict,
    upsert_case_revision,
    validate_input_bundle,
)

router = APIRouter(prefix="/api/v1", tags=["retrieval"], dependencies=[Depends(require_auth)])

OWNER = "local"
ENDPOINT = "retrieve"
PENDING_STATUSES = ("queued", "running")


@router.post("/retrieve", status_code=202)
def create_retrieval_job(
    payload: dict[str, Any] = Body(...),
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    session: Session = Depends(get_session),
) -> dict:
    settings = get_settings()
    key = validate_idempotency_key(idempotency_key)

    # 1. Version gate first: reporting shape errors against the wrong schema
    #    would only mislead the caller.
    version = payload.get("schema_version")
    if version != SCHEMA_VERSION:
        raise schema_version_unsupported(str(version))

    # 2. Full contract validation, reporting every violation at once.
    violations = validate_input_bundle(payload)
    if violations:
        raise ApiError(
            422,
            "BUNDLE_CONTRACT_VIOLATION",
            "Bundle tidak memenuhi kontrak AURORA v1.0.0",
            details=violations_to_details(list(violations)),
        )

    # 3. Idempotency replay, keyed on the canonical (JCS) payload hash.
    payload_hash = canonical_sha256(payload)
    existing = session.scalar(
        select(Job).where(
            Job.owner == OWNER, Job.endpoint == ENDPOINT, Job.idempotency_key == key
        )
    )
    if existing is not None:
        if existing.payload_hash != payload_hash:
            raise idempotency_conflict(
                "Idempotency-Key ini sudah dipakai dengan payload berbeda; "
                "gunakan key baru atau kirim ulang snapshot request yang sama"
            )
        return {
            "job_id": existing.job_id,
            "case_id": existing.case_id,
            "claim_revision": existing.claim_revision,
            "status": existing.status,
        }

    # 4. Backpressure: refuse politely instead of unbounded queue growth.
    pending = session.scalar(
        select(func.count()).select_from(Job).where(Job.status.in_(PENDING_STATUSES))
    )
    if pending is not None and pending >= settings.max_pending:
        raise ApiError(
            429,
            "TOO_MANY_PENDING_JOBS",
            f"Ada {pending} job tertunda (batas {settings.max_pending}); coba lagi nanti",
            retryable=True,
            details={"pending": pending, "max_pending": settings.max_pending},
        )

    # 5. Pin case/revision identity now so history is correct even if the run
    #    fails later.
    try:
        upsert_case_revision(session, payload)
    except RevisionConflict as exc:
        raise revision_conflict(str(exc)) from exc

    job = Job(
        job_id=new_job_id(),
        owner=OWNER,
        endpoint=ENDPOINT,
        idempotency_key=key,
        payload_hash=payload_hash,
        case_id=payload["case_id"],
        claim_revision=int(payload["claim_revision"]),
        status="queued",
        request_json=payload,
    )
    session.add(job)
    session.commit()

    return {
        "job_id": job.job_id,
        "case_id": job.case_id,
        "claim_revision": job.claim_revision,
        "status": job.status,
    }


@router.post("/validate-bundle")
def validate_bundle_endpoint(payload: dict[str, Any] = Body(...)) -> dict:
    """Validate a bundle without queueing work.

    Useful for Dev 1/Dev 3 to check an exchange file against the *same*
    validator this service uses on import, so contract problems surface before a
    handoff rather than after.
    """
    version = payload.get("schema_version")
    if version != SCHEMA_VERSION:
        raise schema_version_unsupported(str(version))
    violations = validate_input_bundle(payload)
    return {
        "valid": not violations,
        "schema_version": SCHEMA_VERSION,
        **violations_to_details(list(violations)),
    }
