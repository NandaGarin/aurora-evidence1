"""Minimal HTTP client for detector adapters, built on urllib.

Why the standard library instead of a client package: the detector adapters have
to run wherever the backend runs, and keeping them dependency-free means the
error-mapping logic below is testable against a local stub server with no
installation step.

Responsibilities
----------------
* Map transport and HTTP failures onto the contract's signal statuses, so a
  vendor outage becomes ``unavailable``/``failed`` rather than a crash or, worse,
  a silent "human-written" answer.
* Honour ``429`` + ``Retry-After``.
* Never log credentials. Headers are redacted before they reach diagnostics.
* Cap response size so a hostile or broken endpoint cannot exhaust memory.
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

from aurora_evidence.forensics.interfaces import DetectorError

#: Header names whose values must never be logged or echoed into diagnostics.
_SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "x-api-key",
        "apikey",
        "api-key",
        "x-auth-token",
        "cookie",
        "proxy-authorization",
        "x-sightengine-secret",
        "x-copyleaks-key",
    }
)

#: Request/response body keys that commonly carry secrets.
_SENSITIVE_BODY_KEYS = frozenset(
    {"api_secret", "api_key", "apikey", "secret", "token", "password", "access_token"}
)

DEFAULT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Copy headers with sensitive values replaced by a marker."""
    return {
        key: ("<redacted>" if key.lower() in _SENSITIVE_HEADERS else value)
        for key, value in headers.items()
    }


def redact_payload(payload: Any) -> Any:
    """Recursively redact secret-looking keys for safe diagnostics/audit logs."""
    if isinstance(payload, dict):
        return {
            key: (
                "<redacted>"
                if key.lower() in _SENSITIVE_BODY_KEYS
                else redact_payload(value)
            )
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [redact_payload(item) for item in payload]
    return payload


@dataclass
class HttpResponse:
    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)
    elapsed_ms: int = 0
    request_id: str = ""

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            # A malformed body is a vendor-side problem; surface it as such
            # instead of letting a parse error escape as an unexpected crash.
            raise DetectorError(
                "PROVIDER_MALFORMED_RESPONSE",
                f"provider returned a body that is not valid JSON: {exc}",
                status="failed",
            ) from exc


def request(
    url: str,
    *,
    method: str = "POST",
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 45.0,
    max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> HttpResponse:
    """Perform one HTTP request, mapping failures to :class:`DetectorError`.

    Detector endpoints come from server configuration, not user input, so this
    intentionally does not run the SSRF allowlist used for fetching evidence
    URLs — mixing the two would let an operator's configured vendor host be
    blocked by rules meant for untrusted content. User-supplied URLs go through
    ``aurora_evidence.security.ssrf`` instead.
    """
    if not url.lower().startswith(("http://", "https://")):
        raise DetectorError(
            "PROVIDER_CONFIG_INVALID",
            "detector endpoint must be an http(s) URL",
            status="unavailable",
        )

    correlation_id = str(uuid.uuid4())
    prepared = dict(headers or {})
    prepared.setdefault("Accept", "application/json")
    prepared.setdefault("User-Agent", "aurora-evidence/1.0 (+research)")

    http_request = urllib.request.Request(
        url, data=body, headers=prepared, method=method.upper()
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(http_request, timeout=timeout) as response:
            payload = response.read(max_bytes + 1)
            if len(payload) > max_bytes:
                raise DetectorError(
                    "PROVIDER_RESPONSE_TOO_LARGE",
                    f"response exceeded {max_bytes} bytes",
                    status="failed",
                )
            return HttpResponse(
                status=response.status,
                body=payload,
                headers={key.lower(): value for key, value in response.headers.items()},
                elapsed_ms=int((time.monotonic() - started) * 1000),
                request_id=correlation_id,
            )
    except urllib.error.HTTPError as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        detail = exc.read(4096).decode("utf-8", errors="replace")
        raise _map_http_error(exc.code, detail, exc.headers, elapsed) from exc
    except socket.timeout as exc:
        raise DetectorError(
            "PROVIDER_TIMEOUT",
            f"provider did not respond within {timeout}s",
            status="unavailable",
        ) from exc
    except urllib.error.URLError as exc:
        # DNS failure, refused connection, TLS problem: the service is not
        # reachable. That is explicitly not evidence about the content.
        raise DetectorError(
            "PROVIDER_UNREACHABLE",
            f"could not reach provider: {exc.reason}",
            status="unavailable",
        ) from exc


def _map_http_error(
    code: int, detail: str, headers: Any, elapsed_ms: int
) -> DetectorError:
    """Translate HTTP status codes into contract signal statuses."""
    snippet = detail.strip()[:200]
    if code in (401, 403):
        return DetectorError(
            "PROVIDER_UNAUTHORIZED",
            f"provider rejected the credentials (HTTP {code})",
            status="unavailable",
        )
    if code == 402 or "quota" in snippet.lower() or "insufficient" in snippet.lower():
        return DetectorError(
            "PROVIDER_QUOTA_EXCEEDED",
            f"provider reports the quota is exhausted (HTTP {code})",
            status="unavailable",
        )
    if code == 429:
        retry_after = None
        try:
            retry_after = headers.get("Retry-After") if headers else None
        except Exception:  # pragma: no cover - defensive
            retry_after = None
        return DetectorError(
            "PROVIDER_RATE_LIMITED",
            "provider rate limit reached"
            + (f"; Retry-After={retry_after}" if retry_after else ""),
            status="unavailable",
        )
    if code in (413, 415):
        return DetectorError(
            "PROVIDER_INPUT_REJECTED",
            f"provider rejected the input format or size (HTTP {code}): {snippet}",
            status="unsupported",
        )
    if code == 422 or code == 400:
        return DetectorError(
            "PROVIDER_INPUT_REJECTED",
            f"provider rejected the request (HTTP {code}): {snippet}",
            status="unsupported",
        )
    if 500 <= code < 600:
        return DetectorError(
            "PROVIDER_SERVER_ERROR",
            f"provider server error (HTTP {code})",
            status="unavailable",
        )
    return DetectorError(
        "PROVIDER_HTTP_ERROR",
        f"unexpected provider response (HTTP {code}): {snippet}",
        status="failed",
    )


def select_path(payload: Any, path: str) -> Any:
    """Read a dotted path such as ``type.ai_generated`` or ``documents.0.score``.

    Vendor response shapes are declared in configuration rather than hardcoded,
    so the core never learns a specific vendor's field names. Returns ``None``
    when any segment is missing, which the caller reports as a missing field
    rather than as a zero score.
    """
    current = payload
    for segment in path.split("."):
        if isinstance(current, dict):
            if segment not in current:
                return None
            current = current[segment]
        elif isinstance(current, list):
            try:
                index = int(segment)
            except ValueError:
                return None
            if not -len(current) <= index < len(current):
                return None
            current = current[index]
        else:
            return None
    return current
