"""AURORA integration contract v1.0.0 — shared, must stay identical across all three modules.

Layering note (see docs/architecture.md, ADR-001)
------------------------------------------------
This package has two tiers:

``canonical`` / ``ids`` / ``labels`` / ``validate``
    Pure standard library. These carry the *normative* rules: canonical JSON,
    identity/hashing, enum vocabularies, and the 16 contract invariants. They
    import on any Python 3.11+ with no third-party package, so the rules the
    three modules must agree on stay verifiable in any environment.

``models``
    Pydantic v2 mirror of the contract, used at the FastAPI boundary for
    request/response typing and OpenAPI generation. It is imported lazily so
    that the tier above never depends on it. Deep invariant checks are NOT
    duplicated here — the Pydantic layer delegates to ``validate``.

Accessing ``aurora_evidence.contract.AuroraBundle`` still works and will raise a
clear error if Pydantic is not installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aurora_evidence.contract.canonical import (
    CanonicalizationError,
    canonical_json,
    canonical_json_bytes,
    canonical_sha256,
    is_sha256_hex,
    loads_strict,
    sha256_hex,
    text_sha256,
)
from aurora_evidence.contract.ids import (
    asset_id_for,
    atom_set_id_for,
    evidence_id,
    new_case_id,
    new_job_id,
    new_run_id,
    region_id,
    signal_id,
)
from aurora_evidence.contract.labels import (
    CONTENT_STATUSES,
    EVIDENCE_KINDS,
    FACT_LABELS,
    FORENSIC_ASSESSMENTS,
    FORENSIC_STATUSES,
    MATCH_TYPES,
    MODES,
    PROVIDER_STATUSES,
    ROLES,
    RUN_STATUSES,
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    TARGET_KINDS,
    VISUAL_LABELS,
)
from aurora_evidence.contract.validate import (
    ContractViolation,
    validate_bundle,
    validate_evidence,
    validate_forensic_signal,
    validate_retrieval,
)

# Names that live in the optional Pydantic tier.
_PYDANTIC_EXPORTS = {
    "Analysis",
    "Atom",
    "AuroraBundle",
    "Calibration",
    "Decision",
    "Evidence",
    "EvidenceContent",
    "EvidenceLink",
    "EvidenceProvenance",
    "EvidenceSource",
    "ForensicSignal",
    "ImageMatch",
    "MediaRef",
    "OCRSpan",
    "ProviderStatus",
    "QueryLogEntry",
    "Region",
    "Retrieval",
    "RunInfo",
    "VisualAssessment",
    "Warning",
}

if TYPE_CHECKING:  # pragma: no cover - typing only
    from aurora_evidence.contract.models import (  # noqa: F401
        Analysis,
        Atom,
        AuroraBundle,
        Calibration,
        Decision,
        Evidence,
        EvidenceContent,
        EvidenceLink,
        EvidenceProvenance,
        EvidenceSource,
        ForensicSignal,
        ImageMatch,
        MediaRef,
        OCRSpan,
        ProviderStatus,
        QueryLogEntry,
        Region,
        Retrieval,
        RunInfo,
        VisualAssessment,
        Warning,
    )


def __getattr__(name: str) -> Any:
    """Lazily expose the Pydantic tier without making it a hard dependency."""
    if name in _PYDANTIC_EXPORTS:
        try:
            from aurora_evidence.contract import models
        except ModuleNotFoundError as exc:  # pragma: no cover - env dependent
            raise ModuleNotFoundError(
                f"aurora_evidence.contract.{name} needs the Pydantic tier, but "
                f"{exc.name!r} is not installed. Install the API extra "
                "(`uv sync` / `pip install -e backend`) or use the pure-stdlib "
                "helpers in aurora_evidence.contract.validate instead."
            ) from exc
        return getattr(models, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # canonicalization / hashing
    "CanonicalizationError",
    "canonical_json",
    "canonical_json_bytes",
    "canonical_sha256",
    "is_sha256_hex",
    "loads_strict",
    "sha256_hex",
    "text_sha256",
    # identity
    "asset_id_for",
    "atom_set_id_for",
    "evidence_id",
    "new_case_id",
    "new_job_id",
    "new_run_id",
    "region_id",
    "signal_id",
    # vocabularies
    "CONTENT_STATUSES",
    "EVIDENCE_KINDS",
    "FACT_LABELS",
    "FORENSIC_ASSESSMENTS",
    "FORENSIC_STATUSES",
    "MATCH_TYPES",
    "MODES",
    "PROVIDER_STATUSES",
    "ROLES",
    "RUN_STATUSES",
    "SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "TARGET_KINDS",
    "VISUAL_LABELS",
    # validation
    "ContractViolation",
    "validate_bundle",
    "validate_evidence",
    "validate_forensic_signal",
    "validate_retrieval",
    *sorted(_PYDANTIC_EXPORTS),
]
