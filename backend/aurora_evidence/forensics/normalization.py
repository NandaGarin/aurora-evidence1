"""Turn a vendor :class:`DetectionOutcome` into a contract-valid ForensicSignal.

This is the single place where an assessment is decided, which is what makes the
contract's detector rules enforceable across arbitrary providers:

* Invariant 11 — a status other than ``ok`` can never yield
  ``likely_ai_generated``, and ``ai_generated_score`` is only filled when the
  vendor's scale *and direction* are known.
* Invariant 15 — ``unsupported``/``unavailable``/``failed`` force
  ``assessment="not_assessed"`` with a null score; ``inconclusive`` forces
  ``uncertain``; ``ok`` may still be ``uncertain`` (no forced binary label); a
  raw score outside its declared scale raises instead of being clamped.

Also enforced here: min–max normalization changes the *scale*, it does not
calibrate a probability. ``ai_generated_score`` is therefore reported as a
rescaled vendor number with ``calibration_status`` preserved, and the UI must
present it as an indication of content origin — never as "95% certain AI".
"""

from __future__ import annotations

from dataclasses import dataclass

from aurora_evidence.contract.ids import signal_id as make_signal_id
from aurora_evidence.forensics.interfaces import (
    DetectionOutcome,
    ImageDetectionRequest,
    RawScale,
    TextDetectionRequest,
)

#: Band edges for turning a normalized score into a coarse assessment.
#:
#: Deliberately wide and asymmetric-friendly: anything between the two edges is
#: reported as ``uncertain`` rather than pushed to a side. These are display
#: bands, not decision thresholds validated against ground truth, and they are
#: configurable per provider once a local validation study exists.
DEFAULT_AI_BAND = 0.75
DEFAULT_HUMAN_BAND = 0.25


class NormalizationError(ValueError):
    """Raised when a vendor payload cannot be represented without distortion."""


@dataclass(frozen=True)
class AssessmentBands:
    ai_at_or_above: float = DEFAULT_AI_BAND
    human_at_or_below: float = DEFAULT_HUMAN_BAND

    def __post_init__(self) -> None:
        if not 0.0 <= self.human_at_or_below < self.ai_at_or_above <= 1.0:
            raise ValueError("require 0 <= human_band < ai_band <= 1")


def normalize_score(raw_score: float, scale: RawScale) -> float:
    """Rescale a raw vendor score to [0,1] oriented so higher means "more AI".

    Raises when the score falls outside the declared scale. Silently clamping
    would hide a vendor schema change or a misconfigured adapter, and the
    contract requires a warning/error instead.
    """
    if raw_score != raw_score or raw_score in (float("inf"), float("-inf")):
        raise NormalizationError("raw_score must be finite")
    if not scale.min <= raw_score <= scale.max:
        raise NormalizationError(
            f"raw_score {raw_score} is outside the declared scale "
            f"[{scale.min},{scale.max}]; refusing to clamp silently"
        )
    span = scale.max - scale.min
    fraction = (raw_score - scale.min) / span
    return fraction if scale.higher_means_ai else 1.0 - fraction


def derive_assessment(
    *,
    status: str,
    normalized: float | None,
    bands: AssessmentBands | None = None,
) -> str:
    """Map (status, normalized score) to a contract ``assessment`` value."""
    # Invariant 15: these statuses mean nothing was assessed.
    if status in {"unsupported", "unavailable", "failed"}:
        return "not_assessed"
    # Invariant 15: inconclusive is uncertainty, not a human verdict.
    if status == "inconclusive":
        return "uncertain"
    if status != "ok":  # defensive: unknown status is never an accusation
        return "not_assessed"
    if normalized is None:
        # ok but unusable number: we know the call succeeded and nothing more.
        return "uncertain"
    edges = bands or AssessmentBands()
    if normalized >= edges.ai_at_or_above:
        return "likely_ai_generated"
    if normalized <= edges.human_at_or_below:
        return "likely_human_or_camera"
    return "uncertain"


def build_signal(
    *,
    run_id: str,
    sequence: int,
    provider: str,
    modality: str,
    outcome: DetectionOutcome,
    analyzed_at: str,
    target_kind: str,
    asset_id: str | None = None,
    text_sha256: str | None = None,
    bands: AssessmentBands | None = None,
    extra_limitations: tuple[str, ...] = (),
) -> dict[str, object]:
    """Assemble the ForensicSignal dict. Always contract-shaped."""
    if target_kind == "image":
        if not asset_id:
            raise NormalizationError("image target requires asset_id")
        if text_sha256 is not None:
            raise NormalizationError("image target requires text_sha256=None")
        if modality != "image":
            raise NormalizationError("image target requires modality='image'")
    elif target_kind in {"claim_text", "ocr_text"}:
        if not text_sha256:
            raise NormalizationError(f"{target_kind} target requires text_sha256")
        if asset_id is not None:
            raise NormalizationError(f"{target_kind} target requires asset_id=None")
        if modality != "text":
            raise NormalizationError(f"{target_kind} target requires modality='text'")
    else:
        raise NormalizationError(f"unsupported target kind {target_kind!r}")

    normalized: float | None = None
    limitations = list(outcome.limitations) + list(extra_limitations)

    # ai_generated_score requires a known scale AND direction (invariant 11).
    if outcome.status == "ok" and outcome.raw_score is not None and outcome.raw_scale is not None:
        normalized = normalize_score(outcome.raw_score, outcome.raw_scale)
    elif outcome.status == "ok" and outcome.raw_score is not None and outcome.raw_scale is None:
        limitations.append(
            "Skala/arah skor vendor tidak terdokumentasi, sehingga skor mentah "
            "disimpan tanpa normalisasi dan ai_generated_score dibiarkan null."
        )

    assessment = derive_assessment(status=outcome.status, normalized=normalized, bands=bands)

    # Invariant 15: non-assessed statuses carry no score at all.
    if assessment == "not_assessed":
        normalized = None

    if normalized is not None:
        limitations.append(
            "ai_generated_score adalah hasil penskalaan ulang skor vendor, "
            "BUKAN probabilitas terkalibrasi."
        )

    return {
        "signal_id": make_signal_id(run_id, sequence),
        "target": {
            "kind": target_kind,
            "asset_id": asset_id,
            "text_sha256": text_sha256,
        },
        "modality": modality,
        "provider": provider,
        "model_version": outcome.model_version,
        "task": "ai_generation_detection",
        "status": outcome.status,
        "raw_score": outcome.raw_score,
        "raw_scale": outcome.raw_scale.as_dict() if outcome.raw_scale else None,
        "ai_generated_score": round(normalized, 6) if normalized is not None else None,
        "raw_label": outcome.raw_label,
        "assessment": assessment,
        "calibration_status": outcome.calibration_status,
        "applicable_language": outcome.applicable_language,
        "limitations": _dedupe(limitations),
        "analyzed_at": analyzed_at,
        "error_code": outcome.error_code,
    }


def _dedupe(values: list[str]) -> list[str]:
    """Stable de-duplication so repeated caveats appear once."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def unavailable_outcome(
    *, code: str, message: str, provider_configured: bool
) -> DetectionOutcome:
    """Build the outcome used when a detector cannot run at all.

    Kept as a helper so no adapter is tempted to fall back to fixture output
    when credentials are missing — the contract requires that to surface as
    ``unavailable``/``unconfigured`` instead.
    """
    return DetectionOutcome(
        status="unavailable",
        error_code=code,
        limitations=[message],
        calibration_status="not_applicable" if not provider_configured else "unknown",
    )


def request_target(
    request: ImageDetectionRequest | TextDetectionRequest,
) -> tuple[str, str, str | None, str | None]:
    """Return ``(modality, target_kind, asset_id, text_sha256)`` for a request."""
    if isinstance(request, ImageDetectionRequest):
        return "image", "image", request.asset_id, None
    return "text", request.target_kind, None, request.text_sha256
