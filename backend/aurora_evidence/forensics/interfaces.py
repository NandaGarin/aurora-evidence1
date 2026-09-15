"""Typed interfaces for swappable AI detectors.

The user has not chosen a provider and explicitly wants providers to be
replaceable, so the core must never know vendor response fields. Everything
vendor-specific lives in an adapter that returns :class:`DetectionOutcome`; the
core then hands that to ``normalization.build_signal`` to produce a
contract-valid ``ForensicSignal``.

Two ideas keep this honest:

``DetectorCapability``
    A provider declares up front what it can do — modality, languages, input
    size bounds, upload method, timeout, rate limit, which config keys it needs,
    and its current status. ``GET /ready`` and the UI read this instead of
    guessing from whether an API key happens to be set.

``DetectionOutcome``
    The adapter reports what the vendor actually said: the raw score, the scale
    that score lives on, the vendor's own label, and the model version. It does
    NOT decide the final assessment — that mapping is centralized so a careless
    adapter cannot produce an accusation the contract forbids.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

Modality = Literal["image", "text"]
TargetKind = Literal["claim_text", "image", "ocr_text"]

#: Provider-level status, mirroring the contract's provider_status enum.
ProviderStatus = Literal[
    "ok", "disabled", "unconfigured", "rate_limited", "failed", "unsupported"
]

#: Signal-level status, mirroring ForensicSignal.status.
SignalStatus = Literal["ok", "inconclusive", "unsupported", "unavailable", "failed"]


@dataclass(frozen=True)
class RawScale:
    """The scale a vendor's raw score lives on.

    ``higher_means_ai`` is mandatory because a number is meaningless without its
    direction: some vendors return "probability AI", others "probability human".
    Without this, normalizing to ``ai_generated_score`` would be a guess, and the
    contract only permits filling that field when the direction is known.
    """

    min: float
    max: float
    higher_means_ai: bool

    def __post_init__(self) -> None:
        if not self.min < self.max:
            raise ValueError(f"raw scale requires min < max, got {self.min} >= {self.max}")

    def as_dict(self) -> dict[str, float | bool]:
        return {"min": self.min, "max": self.max, "higher_means_ai": self.higher_means_ai}


@dataclass(frozen=True)
class DetectorCapability:
    """What a detector supports, and whether it can be called right now."""

    provider: str
    modality: Modality
    capability: str  # "image_ai_detection" | "text_ai_detection"
    status: ProviderStatus
    message: str | None = None
    model_version: str | None = None
    #: BCP 47 tags the vendor documents support for. Empty means undocumented,
    #: which is treated as "unverified", not "supports everything".
    languages: tuple[str, ...] = ()
    min_characters: int | None = None
    max_characters: int | None = None
    min_pixels: int | None = None
    max_bytes: int | None = None
    upload_method: str | None = None
    timeout_seconds: float | None = None
    rate_limit: str | None = None
    #: Config/env keys this provider needs. Names only — never values.
    required_config: tuple[str, ...] = ()
    #: Documented caveats surfaced in the UI and copied into the signal.
    limitations: tuple[str, ...] = ()
    #: True when the provider is a labelled fixture rather than a real service.
    is_fixture: bool = False
    docs_url: str | None = None
    docs_checked_on: str | None = None

    @property
    def is_callable(self) -> bool:
        return self.status == "ok"


@dataclass(frozen=True)
class ImageDetectionRequest:
    """An image to analyze, identified by content, not by file path."""

    asset_id: str
    sha256: str
    data: bytes
    media_type: str
    width: int
    height: int
    #: Records whether bytes were altered before sending (e.g. re-encoded to
    #: satisfy a vendor limit). The contract wants this tracked, since a
    #: transformed image is not the same forensic subject as the original.
    preprocessing: str = "none"
    bytes_modified: bool = False


@dataclass(frozen=True)
class TextDetectionRequest:
    """Text to analyze plus the exact hash of what will be sent.

    ``text_sha256`` must hash the exact string handed to the vendor. OCR text is
    a separate target and must never be silently concatenated with the caption.
    """

    text: str
    text_sha256: str
    target_kind: TargetKind
    language: str | None = None


@dataclass
class DetectionOutcome:
    """What the vendor reported, before contract normalization.

    ``status`` and ``raw_*`` come from the adapter. ``assessment`` deliberately
    does not appear here: ``normalization.build_signal`` derives it so the
    status/assessment coupling in invariants 11 and 15 holds for every provider.
    """

    status: SignalStatus
    raw_score: float | None = None
    raw_scale: RawScale | None = None
    raw_label: str | None = None
    model_version: str | None = None
    applicable_language: str | None = None
    limitations: list[str] = field(default_factory=list)
    error_code: str | None = None
    #: Non-sensitive diagnostics (latency, http status, vendor request id).
    #: Never contains credentials or full request bodies.
    diagnostics: dict[str, object] = field(default_factory=dict)
    #: Vendor's own calibration claim, if documented.
    calibration_status: str = "unknown"


@runtime_checkable
class ImageAIDetector(Protocol):
    def capability(self) -> DetectorCapability: ...

    def detect(self, request: ImageDetectionRequest) -> DetectionOutcome: ...


@runtime_checkable
class TextAIDetector(Protocol):
    def capability(self) -> DetectorCapability: ...

    def detect(self, request: TextDetectionRequest) -> DetectionOutcome: ...


class DetectorError(Exception):
    """Adapter-level failure carrying a stable, non-sensitive error code."""

    def __init__(self, code: str, message: str, *, status: SignalStatus = "failed") -> None:
        self.code = code
        self.status = status
        super().__init__(message)
