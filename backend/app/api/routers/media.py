"""Media upload/resolve endpoints.

``POST /api/v1/media`` returns a contract ``MediaRef`` whose ``asset_id`` and
``sha256`` are derived from the *original uploaded bytes*. That matters for
forensics: an image re-encoded on the way to a vendor is no longer the same
subject, so identity is pinned to the bytes we received, and the transport URI
is explicitly not part of identity.

``GET /api/v1/media/{asset_id}`` resolves a stored asset. Only assets this
service stored can be resolved — the path is never taken from user input, which
rules out traversal through this endpoint.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import require_auth
from app.config import get_settings
from app.db import get_session
from app.errors import ApiError, not_found
from app.services.media import MediaError, resolve_asset, store_upload

router = APIRouter(prefix="/api/v1", tags=["media"], dependencies=[Depends(require_auth)])


@router.post("/media", status_code=201)
async def upload_media(
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> dict:
    settings = get_settings()
    max_bytes = settings.max_upload_mb * 1024 * 1024

    # Read with a hard cap: never buffer an unbounded upload into memory.
    raw = await file.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ApiError(
            413,
            "UPLOAD_TOO_LARGE",
            f"Ukuran unggahan melebihi {settings.max_upload_mb} MB",
            details={"max_upload_mb": settings.max_upload_mb},
        )

    try:
        stored = store_upload(session, raw)
    except MediaError as exc:
        # Declared content-type is untrusted; the real check is decoding it.
        raise ApiError(422, "INVALID_MEDIA", str(exc)) from exc

    session.commit()
    return {
        "asset_id": stored.asset_id,
        "sha256": stored.sha256,
        "media_type": stored.media_type,
        "width": stored.width,
        "height": stored.height,
        "uri": stored.uri,
    }


@router.get("/media/{asset_id}")
def get_media(asset_id: str, session: Session = Depends(get_session)) -> FileResponse:
    asset = resolve_asset(session, asset_id)
    if asset is None:
        raise not_found(f"asset {asset_id} tidak ditemukan")
    path = Path(asset.stored_path)
    if not path.exists():
        # Row present but bytes gone: report honestly instead of a 500.
        raise ApiError(
            410,
            "ASSET_BYTES_MISSING",
            "Metadata asset ada tetapi berkasnya tidak tersedia lagi di penyimpanan",
        )
    return FileResponse(path, media_type=asset.media_type)
