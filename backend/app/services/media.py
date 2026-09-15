"""Media upload/resolve service.

- asset_id = ``asset_`` + sha256(original uploaded bytes) (content-addressed).
- Real MIME sniff + Pillow decode; reject non-images and oversize uploads.
- Store bytes under {data_dir}/media/{asset_id}.{ext}. The URI in MediaRef is
  a resolvable API path; identity never depends on the URI.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import MediaAsset
from aurora_evidence.contract.canonical import sha256_hex
from aurora_evidence.contract.ids import asset_id_for

_settings = get_settings()

# Pillow format -> (media_type, extension)
_FORMAT_MAP = {
    "PNG": ("image/png", "png"),
    "JPEG": ("image/jpeg", "jpg"),
    "WEBP": ("image/webp", "webp"),
}


class MediaError(ValueError):
    pass


@dataclass
class StoredMedia:
    asset_id: str
    sha256: str
    media_type: str
    width: int
    height: int
    byte_size: int
    uri: str


def _apply_exif_orientation(img: Image.Image) -> Image.Image:
    try:
        from PIL import ImageOps

        return ImageOps.exif_transpose(img)
    except Exception:
        return img


def store_upload(session: Session, raw: bytes) -> StoredMedia:
    max_bytes = _settings.max_upload_mb * 1024 * 1024
    if len(raw) == 0:
        raise MediaError("empty upload")
    if len(raw) > max_bytes:
        raise MediaError(f"upload exceeds {_settings.max_upload_mb} MB")

    try:
        with Image.open(io.BytesIO(raw)) as probe:
            probe.verify()  # integrity check
        with Image.open(io.BytesIO(raw)) as img:
            fmt = (img.format or "").upper()
            if fmt not in _FORMAT_MAP:
                raise MediaError(f"unsupported image format: {fmt or 'unknown'}")
            oriented = _apply_exif_orientation(img)
            width, height = oriented.size
    except (UnidentifiedImageError, OSError) as exc:
        raise MediaError("file is not a valid image") from exc

    media_type, ext = _FORMAT_MAP[fmt]
    digest = sha256_hex(raw)
    asset_id = asset_id_for(digest)
    uri = f"/api/v1/media/{asset_id}"

    existing = session.get(MediaAsset, asset_id)
    if existing is None:
        _settings.media_dir.mkdir(parents=True, exist_ok=True)
        path = _settings.media_dir / f"{asset_id}.{ext}"
        path.write_bytes(raw)
        session.add(
            MediaAsset(
                asset_id=asset_id,
                sha256=digest,
                media_type=media_type,
                width=width,
                height=height,
                byte_size=len(raw),
                stored_path=str(path),
            )
        )
        session.flush()
    else:
        width, height, media_type = existing.width, existing.height, existing.media_type

    return StoredMedia(
        asset_id=asset_id,
        sha256=digest,
        media_type=media_type,
        width=width,
        height=height,
        byte_size=len(raw),
        uri=uri,
    )


def resolve_asset(session: Session, asset_id: str) -> MediaAsset | None:
    return session.get(MediaAsset, asset_id)


def find_by_sha256(session: Session, digest: str) -> MediaAsset | None:
    return session.scalar(select(MediaAsset).where(MediaAsset.sha256 == digest))
