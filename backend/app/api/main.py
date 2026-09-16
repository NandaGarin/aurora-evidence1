"""FastAPI application factory and lifecycle.

Wiring decisions worth stating:

* **Public-mode config is validated at startup**, not on first request. An
  instance exposed beyond localhost without a strong token should refuse to
  boot rather than serve unauthenticated traffic.
* **TrustedHost and exact-origin CORS** are always installed. Wildcard origins
  are rejected by config validation, so a browser origin must be named.
* **The worker starts with the app** so ``uv run uvicorn`` alone gives a working
  system. It can also be run as a separate process; job state lives in the
  database, so both arrangements behave identically.
* **Every error leaves as the contract envelope** ``{"error": {...}}``. HTTP
  status describes the request, never a factual conclusion about a claim.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.routers import health, jobs, media, retrieve
from app.config import get_settings
from app.errors import ApiError
from aurora_evidence.version import SCHEMA_VERSION, SERVICE_NAME

logger = logging.getLogger("aurora_evidence.api")

_worker = None


def get_worker():
    """Current worker instance (None when not started). Used by ``/ready``."""
    return _worker


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _worker
    settings = get_settings()
    settings.ensure_dirs()
    # Fail fast on an unsafe public configuration.
    settings.validate_public()

    from app.workers.worker import JobWorker

    _worker = JobWorker()
    _worker.start()
    logger.info(
        "%s ready on %s:%s (mode default=%s, public=%s)",
        SERVICE_NAME,
        settings.host,
        settings.port,
        settings.mode_default,
        settings.public,
    )
    try:
        yield
    finally:
        if _worker is not None:
            _worker.stop()
            _worker = None


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AURORA Evidence — Development 2",
        version=SCHEMA_VERSION,
        description=(
            "Retrieval & reranking bukti multisumber + AI detector gambar/teks. "
            "Menghasilkan bagian `retrieval` dari AuroraBundle v1.0.0. "
            "Modul ini TIDAK menetapkan verdict faktual; skor AI detector adalah "
            "indikasi asal konten, bukan label hoaks."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,  # exact origins; wildcard is rejected in config
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "Idempotency-Key"],
    )

    app.include_router(health.router)
    app.include_router(media.router)
    app.include_router(jobs.router)
    app.include_router(retrieve.router)

    @app.exception_handler(ApiError)
    async def _api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
        # ApiError already carries the contract envelope in .detail.
        return JSONResponse(status_code=exc.status_code, content=exc.detail)

    @app.exception_handler(RequestValidationError)
    async def _request_validation_handler(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # A missing/!malformed header or body shape: 422 with the contract envelope.
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Request tidak valid",
                    "retryable": False,
                    "details": {"errors": exc.errors()[:20]},
                }
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
        # Log the detail server-side; return a non-sensitive envelope.
        logger.exception("unhandled error")
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "Terjadi kesalahan internal",
                    "retryable": False,
                    "details": {},
                }
            },
        )

    return app


app = create_app()
