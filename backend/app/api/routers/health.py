"""``GET /health`` (liveness) and ``GET /ready`` (readiness + capabilities).

The two endpoints answer different questions and must not be conflated:

``/health``
    "Is this process up?" — a fixed, cheap response.

``/ready``
    "Can it do useful work, and which capabilities are actually available?"
    Contract rules:
      * a failing *local* dependency (database, worker, local corpus) -> 503
        ``not_ready``;
      * an unconfigured *optional* provider -> 200 ``degraded``, with that
        capability reported as ``unconfigured``.

Degraded is the normal state for a fresh install: the local BM25 path works
while external providers have no credentials. That is deliberately visible
rather than hidden behind a green light.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import Settings
from app.api.deps import settings_dep
from app.db import get_session
from app.services.capabilities import all_capabilities, local_corpus_ready
from aurora_evidence.version import SCHEMA_VERSION, SERVICE_NAME

router = APIRouter(tags=["health"])

#: Statuses that mean "this capability cannot be called right now" but that are
#: acceptable for optional providers.
_OPTIONAL_NOT_OK = {"unconfigured", "unsupported", "disabled", "rate_limited", "failed"}


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": SERVICE_NAME, "schema_version": SCHEMA_VERSION}


@router.get("/ready")
def ready(
    response: Response,
    session: Session = Depends(get_session),
    settings: Settings = Depends(settings_dep),
) -> dict:
    from app.api.main import get_worker  # local import avoids a circular import

    # --- mandatory local dependencies ---
    database_ok = True
    database_message = None
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - report, don't crash readiness
        database_ok = False
        database_message = str(exc)[:200]

    worker = get_worker()
    worker_ok = bool(worker and worker.alive)

    corpus_ok, corpus_message = local_corpus_ready(settings)

    mode = settings.mode_default
    capabilities = all_capabilities(settings, mode=mode)

    optional_degraded = any(
        entry["status"] in _OPTIONAL_NOT_OK
        for entry in capabilities
        if entry["capability"] != "local_corpus_search"
    )

    if not (database_ok and worker_ok and corpus_ok):
        status = "not_ready"
        response.status_code = 503
    elif optional_degraded:
        # Optional providers missing: usable, but say so plainly.
        status = "degraded"
    else:
        status = "ready"

    return {
        "status": status,
        "service": SERVICE_NAME,
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "database": {"status": "ok" if database_ok else "failed", "message": database_message},
        "worker": {
            "status": "ok" if worker_ok else "failed",
            "message": None if worker_ok else "job worker tidak berjalan",
        },
        "local_corpus": {
            "status": "ok" if corpus_ok else "failed",
            "message": corpus_message,
            "path": str(settings.corpus_path),
        },
        "capabilities": capabilities,
        "notes": [
            "Provider opsional tanpa kredensial dilaporkan unconfigured; tidak ada "
            "fallback fixture diam-diam.",
            "Skor AI detector adalah indikasi asal konten, bukan label hoaks dan "
            "bukan stance faktual.",
        ],
    }
