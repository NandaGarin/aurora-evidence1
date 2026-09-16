"""Job polling and run history.

``GET /api/v1/jobs/{job_id}`` returns the contract polling shape:

    {job_id, status, result, error}

with ``status`` in queued|running|succeeded|partial|failed, ``result`` an
AuroraBundle or null, and ``error`` a ``{code,message,retryable}`` object or
null. Two rules the contract is strict about, enforced here:

* A failure is never dressed up as a success — a failed job returns
  ``result: null`` plus a populated ``error``.
* ``partial`` keeps its result, because a partial retrieval is usable as long as
  the warnings inside it stay visible.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_auth
from app.db import get_session
from app.errors import not_found
from app.models import Job
from app.services.retrieval_service import list_runs

router = APIRouter(prefix="/api/v1", tags=["jobs"], dependencies=[Depends(require_auth)])


@router.get("/jobs/{job_id}")
def get_job(job_id: str, session: Session = Depends(get_session)) -> dict:
    job = session.get(Job, job_id)
    if job is None:
        raise not_found(f"job {job_id} tidak ditemukan")

    # Only a genuinely completed run may carry a result.
    result = job.result_json if job.status in ("succeeded", "partial") else None
    error = job.error_json if job.status == "failed" else None

    return {
        "job_id": job.job_id,
        "status": job.status,
        "case_id": job.case_id,
        "claim_revision": job.claim_revision,
        "attempts": job.attempts,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "result": result,
        "error": error,
    }


@router.get("/runs")
def get_runs(
    case_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> dict:
    """Persisted run history so the UI survives a restart."""
    return {"runs": list_runs(session, case_id=case_id, limit=limit)}
