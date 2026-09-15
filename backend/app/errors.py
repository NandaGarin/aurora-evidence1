"""Typed API errors mapped to the contract error envelope.

Error body: {"error": {"code", "message", "retryable", "details"}}.
- INPUT_ANALYSIS_REQUIRED / SCHEMA_VERSION_UNSUPPORTED -> 422
- revision/atom/idempotency conflict -> 409
HTTP status is never conflated with a factual conclusion.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException


class ApiError(HTTPException):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.retryable = retryable
        self.details = details or {}
        super().__init__(
            status_code=status_code,
            detail={
                "error": {
                    "code": code,
                    "message": message,
                    "retryable": retryable,
                    "details": self.details,
                }
            },
        )


def schema_version_unsupported(got: str) -> ApiError:
    return ApiError(
        422,
        "SCHEMA_VERSION_UNSUPPORTED",
        f"schema_version {got!r} is not supported; expected 1.0.0",
        details={"supported": ["1.0.0"], "got": got},
    )


def validation_error(message: str, details: dict[str, Any] | None = None) -> ApiError:
    return ApiError(422, "VALIDATION_ERROR", message, details=details)


def idempotency_conflict(message: str) -> ApiError:
    return ApiError(409, "IDEMPOTENCY_CONFLICT", message, retryable=False)


def revision_conflict(message: str) -> ApiError:
    return ApiError(409, "REVISION_CONFLICT", message, retryable=False)


def not_found(message: str) -> ApiError:
    return ApiError(404, "NOT_FOUND", message)


def unauthorized(message: str = "missing or invalid API token") -> ApiError:
    return ApiError(401, "UNAUTHORIZED", message)
