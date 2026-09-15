"""Closed vocabularies of AURORA contract v1.0.0.

These sets are normative and must stay byte-identical across the three modules.
Never widen one of them locally: the contract says public enum values may not be
renamed or extended unilaterally, and additions belong in ``extensions``.

The UI may translate these values into Indonesian for display, but the API
representation stays exactly as written here.
"""

from __future__ import annotations

from typing import Final

SCHEMA_VERSION: Final[str] = "1.0.0"

#: Versions this build accepts. Anything else -> SCHEMA_VERSION_UNSUPPORTED (422).
SUPPORTED_SCHEMA_VERSIONS: Final[frozenset[str]] = frozenset({"1.0.0"})

MODES: Final[frozenset[str]] = frozenset({"demo", "live"})

RUN_STATUSES: Final[frozenset[str]] = frozenset({"completed", "partial", "failed"})

JOB_STATUSES: Final[frozenset[str]] = frozenset(
    {"queued", "running", "succeeded", "partial", "failed"}
)

VISUAL_LABELS: Final[tuple[str, ...]] = ("Supported", "Contradicted", "Unobservable")

FACT_LABELS: Final[tuple[str, ...]] = (
    "Supported",
    "Contradicted",
    "InsufficientEvidence",
)

ROLES: Final[frozenset[str]] = frozenset(
    {
        "actor",
        "action",
        "object",
        "attribute",
        "location",
        "time",
        "quantity",
        "relation",
        "cause",
    }
)

EVIDENCE_KINDS: Final[frozenset[str]] = frozenset(
    {
        "fact_check",
        "news",
        "official",
        "web",
        "image_provenance",
        "local_corpus",
        "user_supplied",
    }
)

CONTENT_STATUSES: Final[frozenset[str]] = frozenset(
    {"full", "excerpt_only", "snippet_only", "unavailable"}
)

MATCH_TYPES: Final[frozenset[str]] = frozenset({"exact", "near_duplicate", "semantic"})

STANCES: Final[frozenset[str]] = frozenset(
    {"Supports", "Contradicts", "NotRelevant", "Unclear"}
)

TARGET_KINDS: Final[frozenset[str]] = frozenset({"claim_text", "image", "ocr_text"})

MODALITIES: Final[frozenset[str]] = frozenset({"image", "text"})

FORENSIC_TASKS: Final[frozenset[str]] = frozenset({"ai_generation_detection"})

FORENSIC_STATUSES: Final[frozenset[str]] = frozenset(
    {"ok", "inconclusive", "unsupported", "unavailable", "failed"}
)

FORENSIC_ASSESSMENTS: Final[frozenset[str]] = frozenset(
    {"likely_ai_generated", "likely_human_or_camera", "uncertain", "not_assessed"}
)

#: Statuses that must never yield an AI accusation (invariant 11).
FORENSIC_NON_OK_STATUSES: Final[frozenset[str]] = frozenset(
    {"inconclusive", "unsupported", "unavailable", "failed"}
)

#: Statuses that force ``assessment="not_assessed"`` and a null score (invariant 15).
FORENSIC_NOT_ASSESSED_STATUSES: Final[frozenset[str]] = frozenset(
    {"unsupported", "unavailable", "failed"}
)

CALIBRATION_STATUSES: Final[frozenset[str]] = frozenset(
    {"unknown", "provider_claimed", "locally_validated", "not_applicable"}
)

DECISION_CALIBRATION_STATUSES: Final[frozenset[str]] = frozenset(
    {"calibrated", "uncalibrated", "demo_only"}
)

PROVIDER_STATUSES: Final[frozenset[str]] = frozenset(
    {"ok", "disabled", "unconfigured", "rate_limited", "failed", "unsupported"}
)

INFERENCE_KINDS: Final[frozenset[str]] = frozenset(
    {"trained", "heuristic", "fixture", "unavailable"}
)

#: Capability names Dev 2 reports on ``GET /ready`` and in ``provider_status``.
CAPABILITIES: Final[tuple[str, ...]] = (
    "local_corpus_search",
    "web_search",
    "fact_check_search",
    "content_fetch",
    "image_provenance",
    "image_ai_detection",
    "text_ai_detection",
    "dense_retrieval",
    "reranking",
)


def is_supported_schema_version(value: object) -> bool:
    return isinstance(value, str) and value in SUPPORTED_SCHEMA_VERSIONS
