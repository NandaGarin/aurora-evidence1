"""Shared FastAPI dependencies: authentication, idempotency, error shaping."""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import Header

from app.config import Settings, get_settings
from app.errors import ApiError, unauthorized

#: The contract asks for a bounded idempotency key. 200 matches the column
#: width, so a longer key is rejected with a clear 422 instead of surfacing as a
#: database error.
MAX_IDEMPOTENCY_KEY_LENGTH = 200


def settings_dep() -> Settings:
    return get_settings()


def require_auth(authorization: str | None = Header(default=None)) -> None:
    """Enforce bearer-token auth when the instance is exposed beyond localhost.

    Local (non-public) mode stays open so the demo works with zero setup; public
    mode requires a 32+ character token, which is validated at startup. The
    comparison is constant-time to avoid leaking the token through timing.
    """
    settings = get_settings()
    if not settings.public:
        return
    expected = settings.api_token
    if not expected:  # startup validation should have caught this
        raise ApiError(500, "SERVER_MISCONFIGURED", "API token is not configured")
    provided = ""
    if authorization and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()
    if not provided or not secrets.compare_digest(provided, expected):
        raise unauthorized()


def validate_idempotency_key(key: str) -> str:
    """Reject empty/oversized keys before they reach the database."""
    cleaned = key.strip()
    if not cleaned:
        raise ApiError(422, "VALIDATION_ERROR", "Idempotency-Key tidak boleh kosong")
    if len(cleaned) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            f"Idempotency-Key melebihi {MAX_IDEMPOTENCY_KEY_LENGTH} karakter",
            details={"max_length": MAX_IDEMPOTENCY_KEY_LENGTH, "got": len(cleaned)},
        )
    return cleaned


def violations_to_details(violations: list[Any], limit: int = 25) -> dict[str, Any]:
    """Render contract violations as non-sensitive, actionable error details."""
    items = []
    for violation in violations[:limit]:
        items.append(
            {
                "code": getattr(violation, "code", "CONTRACT_VIOLATION"),
                "path": getattr(violation, "path", "") or "<root>",
                "message": getattr(violation, "message", str(violation)),
            }
        )
    payload: dict[str, Any] = {"violations": items, "violation_count": len(violations)}
    if len(violations) > limit:
        payload["truncated"] = True
    return payload
