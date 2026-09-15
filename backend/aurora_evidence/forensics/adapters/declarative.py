"""Config-driven HTTP detector adapters.

Why declarative instead of one hand-written class per vendor
-----------------------------------------------------------
The assignment requires that providers be swappable "without changing business
logic", and that the core must not recognise a specific vendor's response
fields. It also forbids inventing endpoint URLs or response schemas: those must
be verified against official documentation at implementation time.

So the vendor-specific part — endpoint, HTTP method, auth style, how the payload
is uploaded, and which JSON path holds the score/label/model version — is
*declared in configuration* (``configs/providers.*.toml``) and validated here.
Adding a provider means adding a config block and confirming it against the
vendor's docs; it does not mean touching this file.

This also keeps the code honest about verification state: a profile carries
``verified = false`` until someone has checked it against the official docs and
recorded the date in ``docs/providers.md``. An unverified profile still runs, but
its capability message and every emitted signal say the mapping is unconfirmed.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from aurora_evidence.forensics.adapters import httpclient
from aurora_evidence.forensics.interfaces import (
    DetectionOutcome,
    DetectorCapability,
    DetectorError,
    ImageDetectionRequest,
    RawScale,
    TextDetectionRequest,
)

AuthStyle = Literal["none", "bearer", "header", "form_fields", "basic"]
UploadStyle = Literal["multipart", "base64_json", "json_text", "form_text"]


@dataclass
class DeclarativeDetectorConfig:
    """Everything needed to call one detector endpoint, from configuration."""

    provider: str
    modality: Literal["image", "text"]
    endpoint: str
    #: Which JSON path in the response holds the score.
    score_path: str
    scale_min: float
    scale_max: float
    higher_means_ai: bool
    method: str = "POST"
    auth_style: AuthStyle = "none"
    #: Header name for ``auth_style="header"``.
    auth_header: str = "Authorization"
    #: Form/JSON field names for ``auth_style="form_fields"`` (e.g. user+secret).
    auth_fields: tuple[str, ...] = ()
    upload_style: UploadStyle = "multipart"
    #: Field name carrying the payload (image bytes or text).
    payload_field: str = "media"
    extra_fields: dict[str, str] = field(default_factory=dict)
    label_path: str | None = None
    model_version_path: str | None = None
    #: Optional async flow: submit then poll this path for a job id.
    job_id_path: str | None = None
    poll_endpoint: str | None = None
    poll_status_path: str | None = None
    poll_done_values: tuple[str, ...] = ()
    poll_interval_seconds: float = 2.0
    poll_max_attempts: int = 10
    languages: tuple[str, ...] = ()
    min_characters: int | None = None
    max_characters: int | None = None
    min_pixels: int | None = None
    max_bytes: int | None = None
    timeout_seconds: float = 45.0
    rate_limit: str | None = None
    required_config: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    calibration_status: str = "unknown"
    #: False until the endpoint/schema has been checked against official docs.
    verified: bool = False
    docs_url: str | None = None
    docs_checked_on: str | None = None

    def scale(self) -> RawScale:
        return RawScale(
            min=self.scale_min, max=self.scale_max, higher_means_ai=self.higher_means_ai
        )

    @classmethod
    def from_mapping(cls, provider: str, data: Mapping[str, Any]) -> "DeclarativeDetectorConfig":
        """Build a config from a parsed TOML/JSON table, validating required keys."""
        missing = [
            key
            for key in ("modality", "endpoint", "score_path", "scale")
            if key not in data
        ]
        if missing:
            raise ValueError(
                f"detector profile {provider!r} is missing required key(s): {missing}"
            )
        scale = data["scale"]
        for key in ("min", "max", "higher_means_ai"):
            if key not in scale:
                raise ValueError(
                    f"detector profile {provider!r} scale is missing {key!r}; a score "
                    "without a documented range and direction cannot be normalized"
                )
        auth = data.get("auth", {}) or {}
        poll = data.get("poll", {}) or {}
        return cls(
            provider=provider,
            modality=data["modality"],
            endpoint=data["endpoint"],
            score_path=data["score_path"],
            scale_min=float(scale["min"]),
            scale_max=float(scale["max"]),
            higher_means_ai=bool(scale["higher_means_ai"]),
            method=data.get("method", "POST"),
            auth_style=auth.get("style", "none"),
            auth_header=auth.get("header", "Authorization"),
            auth_fields=tuple(auth.get("fields", ()) or ()),
            upload_style=data.get("upload_style", "multipart"),
            payload_field=data.get("payload_field", "media"),
            extra_fields=dict(data.get("extra_fields", {}) or {}),
            label_path=data.get("label_path"),
            model_version_path=data.get("model_version_path"),
            job_id_path=poll.get("job_id_path"),
            poll_endpoint=poll.get("endpoint"),
            poll_status_path=poll.get("status_path"),
            poll_done_values=tuple(poll.get("done_values", ()) or ()),
            poll_interval_seconds=float(poll.get("interval_seconds", 2.0)),
            poll_max_attempts=int(poll.get("max_attempts", 10)),
            languages=tuple(data.get("languages", ()) or ()),
            min_characters=data.get("min_characters"),
            max_characters=data.get("max_characters"),
            min_pixels=data.get("min_pixels"),
            max_bytes=data.get("max_bytes"),
            timeout_seconds=float(data.get("timeout_seconds", 45.0)),
            rate_limit=data.get("rate_limit"),
            required_config=tuple(data.get("required_config", ()) or ()),
            limitations=tuple(data.get("limitations", ()) or ()),
            calibration_status=data.get("calibration_status", "unknown"),
            verified=bool(data.get("verified", False)),
            docs_url=data.get("docs_url"),
            docs_checked_on=data.get("docs_checked_on"),
        )


def _multipart_body(
    fields: Mapping[str, str], file_field: str | None, file_name: str, file_bytes: bytes | None
) -> tuple[bytes, str]:
    """Encode a multipart/form-data body without a third-party dependency."""
    boundary = f"----aurora{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode()
        )
    if file_field and file_bytes is not None:
        content_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        parts.append(
            (
                f"--{boundary}\r\nContent-Disposition: form-data; "
                f'name="{file_field}"; filename="{file_name}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode()
        )
        parts.append(file_bytes)
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class _DeclarativeBase:
    """Shared auth/response handling for both modalities."""

    def __init__(
        self,
        config: DeclarativeDetectorConfig,
        credentials: Mapping[str, str] | None = None,
    ) -> None:
        self.config = config
        self._credentials = dict(credentials or {})

    # -- capability ---------------------------------------------------------
    def _missing_config(self) -> list[str]:
        return [
            key
            for key in self.config.required_config
            if not (self._credentials.get(key) or "").strip()
        ]

    def capability(self) -> DetectorCapability:
        config = self.config
        missing = self._missing_config()
        limitations = list(config.limitations)
        if not config.verified:
            limitations.append(
                "Pemetaan endpoint/respons provider ini BELUM diverifikasi terhadap "
                "dokumentasi resmi. Verifikasi dan catat tanggalnya di docs/providers.md "
                "sebelum memakai hasilnya."
            )
        if missing:
            # No credentials -> unconfigured. Never a silent fixture fallback.
            return DetectorCapability(
                provider=config.provider,
                modality=config.modality,
                capability=f"{config.modality}_ai_detection",
                status="unconfigured",
                message=(
                    "Konfigurasi belum lengkap: "
                    + ", ".join(missing)
                    + ". Provider tidak dipanggil dan tidak ada fallback fixture."
                ),
                languages=config.languages,
                min_characters=config.min_characters,
                max_characters=config.max_characters,
                min_pixels=config.min_pixels,
                max_bytes=config.max_bytes,
                upload_method=config.upload_style,
                timeout_seconds=config.timeout_seconds,
                rate_limit=config.rate_limit,
                required_config=config.required_config,
                limitations=tuple(limitations),
                docs_url=config.docs_url,
                docs_checked_on=config.docs_checked_on,
            )
        return DetectorCapability(
            provider=config.provider,
            modality=config.modality,
            capability=f"{config.modality}_ai_detection",
            status="ok",
            message=(
                "Terkonfigurasi."
                + ("" if config.verified else " Skema respons belum diverifikasi.")
            ),
            languages=config.languages,
            min_characters=config.min_characters,
            max_characters=config.max_characters,
            min_pixels=config.min_pixels,
            max_bytes=config.max_bytes,
            upload_method=config.upload_style,
            timeout_seconds=config.timeout_seconds,
            rate_limit=config.rate_limit,
            required_config=config.required_config,
            limitations=tuple(limitations),
            docs_url=config.docs_url,
            docs_checked_on=config.docs_checked_on,
        )

    # -- auth ---------------------------------------------------------------
    def _auth(self) -> tuple[dict[str, str], dict[str, str]]:
        """Return ``(headers, form_fields)`` carrying credentials."""
        config = self.config
        headers: dict[str, str] = {}
        fields: dict[str, str] = {}
        if config.auth_style == "bearer":
            key = config.required_config[0] if config.required_config else ""
            headers["Authorization"] = f"Bearer {self._credentials.get(key, '')}"
        elif config.auth_style == "header":
            key = config.required_config[0] if config.required_config else ""
            headers[config.auth_header] = self._credentials.get(key, "")
        elif config.auth_style == "basic":
            user_key, secret_key = (config.auth_fields + ("", ""))[:2]
            token = base64.b64encode(
                f"{self._credentials.get(user_key,'')}:{self._credentials.get(secret_key,'')}".encode()
            ).decode()
            headers["Authorization"] = f"Basic {token}"
        elif config.auth_style == "form_fields":
            # Some vendors expect credentials as ordinary form/JSON fields.
            for name in config.auth_fields:
                fields[name] = self._credentials.get(name, "")
        return headers, fields

    # -- response -----------------------------------------------------------
    def _outcome_from_payload(self, payload: Any, elapsed_ms: int) -> DetectionOutcome:
        config = self.config
        raw_score = httpclient.select_path(payload, config.score_path)
        limitations = list(config.limitations)
        if not config.verified:
            limitations.append(
                "Pemetaan respons provider belum diverifikasi terhadap dokumentasi resmi."
            )

        if raw_score is None:
            # A schema change must be visible, not silently scored as 0.
            return DetectionOutcome(
                status="failed",
                error_code="PROVIDER_FIELD_MISSING",
                limitations=limitations
                + [
                    f"Field skor {config.score_path!r} tidak ada pada respons; "
                    "kemungkinan skema provider berubah."
                ],
                diagnostics={"elapsed_ms": elapsed_ms},
                calibration_status=config.calibration_status,
            )
        if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
            return DetectionOutcome(
                status="failed",
                error_code="PROVIDER_FIELD_TYPE",
                limitations=limitations
                + [f"Field skor {config.score_path!r} bukan angka: {type(raw_score).__name__}."],
                diagnostics={"elapsed_ms": elapsed_ms},
                calibration_status=config.calibration_status,
            )

        label = (
            httpclient.select_path(payload, config.label_path)
            if config.label_path
            else None
        )
        model_version = (
            httpclient.select_path(payload, config.model_version_path)
            if config.model_version_path
            else None
        )
        return DetectionOutcome(
            status="ok",
            raw_score=float(raw_score),
            raw_scale=config.scale(),
            raw_label=str(label) if label is not None else None,
            model_version=str(model_version) if model_version is not None else None,
            limitations=limitations,
            diagnostics={"elapsed_ms": elapsed_ms},
            calibration_status=config.calibration_status,
        )

    def _error_outcome(self, exc: DetectorError) -> DetectionOutcome:
        limitations = list(self.config.limitations)
        return DetectionOutcome(
            status=exc.status,
            error_code=exc.code,
            limitations=limitations + [str(exc)],
            calibration_status=self.config.calibration_status,
        )

    def _post(self, body: bytes, content_type: str, extra_headers: dict[str, str]) -> Any:
        headers = {"Content-Type": content_type, **extra_headers}
        response = httpclient.request(
            self.config.endpoint,
            method=self.config.method,
            headers=headers,
            body=body,
            timeout=self.config.timeout_seconds,
        )
        return response.json(), response.elapsed_ms


class DeclarativeImageDetector(_DeclarativeBase):
    """Image AI detector driven entirely by configuration."""

    def detect(self, request: ImageDetectionRequest) -> DetectionOutcome:
        config = self.config
        missing = self._missing_config()
        if missing:
            return DetectionOutcome(
                status="unavailable",
                error_code="PROVIDER_UNCONFIGURED",
                limitations=list(config.limitations)
                + [f"Kredensial belum diisi: {', '.join(missing)}."],
                calibration_status="not_applicable",
            )
        if config.min_pixels and (request.width < config.min_pixels or request.height < config.min_pixels):
            return DetectionOutcome(
                status="unsupported",
                error_code="IMAGE_TOO_SMALL",
                limitations=list(config.limitations)
                + [
                    f"Gambar {request.width}x{request.height} di bawah batas provider "
                    f"{config.min_pixels}px."
                ],
                calibration_status=config.calibration_status,
            )
        if config.max_bytes and len(request.data) > config.max_bytes:
            return DetectionOutcome(
                status="unsupported",
                error_code="IMAGE_TOO_LARGE",
                limitations=list(config.limitations)
                + [f"Ukuran {len(request.data)} byte melebihi batas {config.max_bytes}."],
                calibration_status=config.calibration_status,
            )

        auth_headers, auth_fields = self._auth()
        extension = mimetypes.guess_extension(request.media_type) or ".bin"
        try:
            if config.upload_style == "multipart":
                body, content_type = _multipart_body(
                    {**config.extra_fields, **auth_fields},
                    config.payload_field,
                    f"{request.asset_id}{extension}",
                    request.data,
                )
            elif config.upload_style == "base64_json":
                payload = {
                    **config.extra_fields,
                    **auth_fields,
                    config.payload_field: base64.b64encode(request.data).decode(),
                }
                body, content_type = json.dumps(payload).encode(), "application/json"
            else:
                # 'url_reference' style is intentionally unsupported: publishing a
                # private upload to a public URL just to satisfy a vendor would
                # leak user data. Declare the capability unavailable instead.
                return DetectionOutcome(
                    status="unavailable",
                    error_code="UPLOAD_STYLE_UNSUPPORTED",
                    limitations=list(config.limitations)
                    + [
                        f"upload_style={config.upload_style!r} tidak didukung untuk "
                        "gambar privat; unggahan pengguna tidak dipublikasikan otomatis."
                    ],
                    calibration_status="not_applicable",
                )
            payload, elapsed = self._post(body, content_type, auth_headers)
        except DetectorError as exc:
            return self._error_outcome(exc)
        outcome = self._outcome_from_payload(payload, elapsed)
        outcome.diagnostics.update(
            {
                "preprocessing": request.preprocessing,
                "bytes_modified": request.bytes_modified,
                "asset_id": request.asset_id,
            }
        )
        return outcome


class DeclarativeTextDetector(_DeclarativeBase):
    """Text AI detector driven entirely by configuration."""

    def detect(self, request: TextDetectionRequest) -> DetectionOutcome:
        config = self.config
        missing = self._missing_config()
        if missing:
            return DetectionOutcome(
                status="unavailable",
                error_code="PROVIDER_UNCONFIGURED",
                limitations=list(config.limitations)
                + [f"Kredensial belum diisi: {', '.join(missing)}."],
                calibration_status="not_applicable",
            )

        stripped = request.text.strip()
        if config.min_characters and len(stripped) < config.min_characters:
            return DetectionOutcome(
                status="unsupported",
                error_code="TEXT_TOO_SHORT",
                applicable_language=request.language,
                limitations=list(config.limitations)
                + [
                    f"Teks {len(stripped)} karakter di bawah batas provider "
                    f"{config.min_characters}. Skor null bukan bukti teks manusia."
                ],
                calibration_status=config.calibration_status,
            )
        if config.max_characters and len(stripped) > config.max_characters:
            return DetectionOutcome(
                status="unsupported",
                error_code="TEXT_TOO_LONG",
                applicable_language=request.language,
                limitations=list(config.limitations)
                + [f"Teks {len(stripped)} karakter melebihi batas {config.max_characters}."],
                calibration_status=config.calibration_status,
            )
        # Undocumented language support is 'unverified', not 'supported'.
        if config.languages:
            language = (request.language or "und").split("-")[0].lower()
            if language not in {tag.split("-")[0].lower() for tag in config.languages}:
                return DetectionOutcome(
                    status="unsupported",
                    error_code="LANGUAGE_NOT_SUPPORTED",
                    applicable_language=request.language,
                    limitations=list(config.limitations)
                    + [
                        f"Provider mendokumentasikan dukungan untuk {list(config.languages)}; "
                        f"bahasa {request.language!r} tidak termasuk."
                    ],
                    calibration_status=config.calibration_status,
                )

        auth_headers, auth_fields = self._auth()
        try:
            if config.upload_style == "form_text":
                body, content_type = _multipart_body(
                    {**config.extra_fields, **auth_fields, config.payload_field: request.text},
                    None,
                    "",
                    None,
                )
            else:
                payload = {
                    **config.extra_fields,
                    **auth_fields,
                    config.payload_field: request.text,
                }
                body, content_type = json.dumps(payload).encode(), "application/json"
            response_payload, elapsed = self._post(body, content_type, auth_headers)
        except DetectorError as exc:
            return self._error_outcome(exc)

        outcome = self._outcome_from_payload(response_payload, elapsed)
        outcome.applicable_language = request.language
        outcome.diagnostics.update(
            {"characters": len(stripped), "target_kind": request.target_kind}
        )
        return outcome
