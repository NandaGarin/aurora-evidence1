"""Pydantic v2 models for AURORA contract v1.0.0.

These models are the shared data shapes. Development 2 (evidence/forensics) owns
the Evidence and ForensicSignal definitions but must keep every public field
name/type/enum identical across the three modules. Internal additions go into
``extensions``.

The validators here enforce the contract invariants that are checkable at the
model level (finite scores, probability sums, excerpt-substring, forensic
status<->assessment consistency, etc.). Cross-object invariants (dangling
references, staleness) are enforced in the validation service.
"""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aurora_evidence.contract.canonical import text_sha256

SCHEMA_VERSION = "1.0.0"

# --- primitive aliases -------------------------------------------------------
Score = Annotated[float, Field(ge=0.0, le=1.0)]
ISODateTime = str

VisualLabel = Literal["Supported", "Contradicted", "Unobservable"]
FactLabel = Literal["Supported", "Contradicted", "InsufficientEvidence"]
Role = Literal[
    "actor", "action", "object", "attribute", "location", "time", "quantity", "relation", "cause"
]
Mode = Literal["demo", "live"]

_PROB_TOL = 1e-6


def _check_finite(value: float | None, field: str) -> None:
    if value is not None and not math.isfinite(value):
        raise ValueError(f"{field} must be finite (no NaN/Infinity)")


class StrictModel(BaseModel):
    """Base: reject unknown top-level fields so contract drift is caught early."""

    model_config = ConfigDict(extra="forbid")


class Warning(StrictModel):
    code: str
    message: str
    component: str


class MediaRef(StrictModel):
    asset_id: str
    sha256: str
    media_type: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    uri: str


class RunInfo(StrictModel):
    run_id: str
    mode: Mode
    started_at: ISODateTime
    finished_at: ISODateTime
    status: Literal["completed", "partial", "failed"]
    versions: dict[str, str] = Field(default_factory=dict)
    warnings: list[Warning] = Field(default_factory=list)


# --- Analysis (owned by Dev 1; Dev 2 validates/consumes) ---------------------
class AtomQualifiers(StrictModel):
    negated: bool
    quantity: float | None = None
    time: str | None = None
    location: str | None = None


class AtomSpan(StrictModel):
    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode="after")
    def _end_after_start(self) -> "AtomSpan":
        if self.end < self.start:
            raise ValueError("span end must be >= start (end is exclusive)")
        return self


class Atom(StrictModel):
    atom_id: str
    statement: str
    role: Role
    subject: str | None
    predicate: str
    object: str | None
    qualifiers: AtomQualifiers
    spans: list[AtomSpan]
    depends_on: list[str] = Field(default_factory=list)
    check_worthiness: Score
    parser_confidence: Score | None


class Region(StrictModel):
    region_id: str
    asset_id: str
    bbox: tuple[float, float, float, float]
    score: Score | None
    description: str

    @model_validator(mode="after")
    def _bbox_bounds(self) -> "Region":
        x0, y0, x1, y1 = self.bbox
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ValueError("bbox must satisfy 0<=x_min<x_max<=1 and 0<=y_min<y_max<=1")
        return self


class VisualAssessment(StrictModel):
    atom_id: str
    visual_status: VisualLabel
    probabilities: dict[str, float] | None
    unmatched_mass: Score | None
    observability_score: Score | None
    supporting_regions: list[Region] = Field(default_factory=list)
    contradicting_regions: list[Region] = Field(default_factory=list)
    counter_evidence: str | None
    rationale: str
    inference_kind: Literal["trained", "heuristic", "fixture", "unavailable"]

    @model_validator(mode="after")
    def _probs(self) -> "VisualAssessment":
        _validate_probabilities(self.probabilities, ("Supported", "Contradicted", "Unobservable"))
        return self


class OCRSpan(StrictModel):
    text: str
    bbox: tuple[float, float, float, float]
    confidence: Score | None
    language: str | None


class Analysis(StrictModel):
    run: RunInfo
    atom_set_id: str
    atomic_claims: list[Atom]
    visual_assessments: list[VisualAssessment] = Field(default_factory=list)
    ocr: list[OCRSpan] = Field(default_factory=list)


# --- Evidence (owned by Dev 2) ----------------------------------------------
class EvidenceSource(StrictModel):
    provider: str
    kind: Literal[
        "fact_check", "news", "official", "web", "image_provenance", "local_corpus", "user_supplied"
    ]
    url: str | None
    title: str
    publisher: str | None
    language: str | None
    published_at: ISODateTime | None
    retrieved_at: ISODateTime


class EvidenceContent(StrictModel):
    text: str
    excerpt: str
    sha256: str
    status: Literal["full", "excerpt_only", "snippet_only", "unavailable"]

    @model_validator(mode="after")
    def _content_rules(self) -> "EvidenceContent":
        # content.sha256 hashes the exact UTF-8 content.text.
        expected = text_sha256(self.text)
        if self.sha256 != expected:
            raise ValueError("content.sha256 must equal sha256 of exact UTF-8 content.text")
        # excerpt must be a substring of content.text (or empty when text empty).
        if self.text == "":
            if self.excerpt != "":
                raise ValueError("excerpt must be empty when content.text is empty")
        elif self.excerpt and self.excerpt not in self.text:
            raise ValueError("excerpt must be a substring of content.text")
        return self


class ImageMatch(StrictModel):
    asset_id: str
    matched_url: str | None
    match_type: Literal["exact", "near_duplicate", "semantic"]
    score: Score | None


class EvidenceProvenance(StrictModel):
    original_url: str | None
    archive_url: str | None
    first_seen_at: ISODateTime | None
    captured_at: ISODateTime | None
    date_basis: str | None
    discovery_method: str
    duplicate_cluster_id: str = Field(min_length=1)
    independence_group_id: str = Field(min_length=1)
    temporal_eligible: bool | None
    usage_note: str | None
    image_matches: list[ImageMatch] = Field(default_factory=list)


class Evidence(StrictModel):
    evidence_id: str
    atom_ids: list[str] = Field(default_factory=list)
    source: EvidenceSource
    content: EvidenceContent
    provenance: EvidenceProvenance
    relevance_score: Score | None
    credibility_score: Score | None
    rerank_score: float | None
    score_breakdown: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _finite_rerank(self) -> "Evidence":
        _check_finite(self.rerank_score, "rerank_score")
        for k, v in self.score_breakdown.items():
            _check_finite(v, f"score_breakdown[{k}]")
        return self


# --- Forensic signals (owned by Dev 2) --------------------------------------
class ForensicTarget(StrictModel):
    kind: Literal["claim_text", "image", "ocr_text"]
    asset_id: str | None
    text_sha256: str | None


class RawScale(StrictModel):
    min: float
    max: float
    higher_means_ai: bool

    @model_validator(mode="after")
    def _order(self) -> "RawScale":
        _check_finite(self.min, "raw_scale.min")
        _check_finite(self.max, "raw_scale.max")
        if not (self.min < self.max):
            raise ValueError("raw_scale.min must be < raw_scale.max")
        return self


class ForensicSignal(StrictModel):
    signal_id: str
    target: ForensicTarget
    modality: Literal["image", "text"]
    provider: str
    model_version: str | None
    task: Literal["ai_generation_detection"]
    status: Literal["ok", "inconclusive", "unsupported", "unavailable", "failed"]
    raw_score: float | None
    raw_scale: RawScale | None
    ai_generated_score: Score | None
    raw_label: str | None
    assessment: Literal[
        "likely_ai_generated", "likely_human_or_camera", "uncertain", "not_assessed"
    ]
    calibration_status: Literal["unknown", "provider_claimed", "locally_validated", "not_applicable"]
    applicable_language: str | None
    limitations: list[str] = Field(default_factory=list)
    analyzed_at: ISODateTime
    error_code: str | None

    @model_validator(mode="after")
    def _forensic_rules(self) -> "ForensicSignal":
        _check_finite(self.raw_score, "raw_score")
        # target/modality consistency (invariant 15)
        if self.target.kind == "image":
            if self.modality != "image":
                raise ValueError("image target requires modality=image")
            if self.target.asset_id is None or self.target.text_sha256 is not None:
                raise ValueError("image target requires asset_id set and text_sha256=null")
        else:  # claim_text / ocr_text
            if self.modality != "text":
                raise ValueError("text target requires modality=text")
            if self.target.asset_id is not None or self.target.text_sha256 is None:
                raise ValueError("text target requires asset_id=null and text_sha256 set")
        # status <-> assessment/score consistency (invariants 11, 15)
        if self.status in ("unsupported", "unavailable", "failed"):
            if self.assessment != "not_assessed":
                raise ValueError(f"status={self.status} requires assessment=not_assessed")
            if self.ai_generated_score is not None:
                raise ValueError(f"status={self.status} requires ai_generated_score=null")
        if self.status == "inconclusive" and self.assessment != "uncertain":
            raise ValueError("status=inconclusive requires assessment=uncertain")
        if self.status != "ok" and self.assessment == "likely_ai_generated":
            raise ValueError("non-ok status must not yield likely_ai_generated")
        return self


class QueryLogEntry(StrictModel):
    query_id: str
    atom_ids: list[str] = Field(default_factory=list)
    provider: str
    query: str
    duration_ms: int = Field(ge=0)
    result_count: int = Field(ge=0)


class ProviderStatus(StrictModel):
    provider: str
    capability: str
    status: Literal["ok", "disabled", "unconfigured", "rate_limited", "failed", "unsupported"]
    message: str | None


class Retrieval(StrictModel):
    run: RunInfo
    atom_set_id: str | None
    evidence_list: list[Evidence] = Field(default_factory=list)
    forensic_signals: list[ForensicSignal] = Field(default_factory=list)
    query_log: list[QueryLogEntry] = Field(default_factory=list)
    provider_status: list[ProviderStatus] = Field(default_factory=list)


# --- Decision (owned by Dev 3; Dev 2 preserves but never fills) --------------
class EvidenceLink(StrictModel):
    evidence_id: str
    atom_id: str
    stance: Literal["Supports", "Contradicts", "NotRelevant", "Unclear"]
    quote: str | None
    rationale: str


class Calibration(StrictModel):
    status: Literal["calibrated", "uncalibrated", "demo_only"]
    calibration_id: str | None
    method: str | None
    alpha: float | None
    sample_count: int = Field(ge=0)
    group: str | None
    fallback_used: str | None
    validity_notes: list[str] = Field(default_factory=list)


class AtomicDecision(StrictModel):
    atom_id: str
    base_label: FactLabel
    status: FactLabel
    probabilities: dict[str, float] | None
    confidence_set: list[FactLabel] | None
    abstention_flag: bool
    abstention_reasons: list[str] = Field(default_factory=list)
    evidence_links: list[EvidenceLink] = Field(default_factory=list)
    visual_atom_ids: list[str] = Field(default_factory=list)
    explanation: str
    calibration: Calibration

    @model_validator(mode="after")
    def _probs(self) -> "AtomicDecision":
        _validate_probabilities(
            self.probabilities, ("Supported", "Contradicted", "InsufficientEvidence")
        )
        if self.confidence_set is not None and len(set(self.confidence_set)) != len(
            self.confidence_set
        ):
            raise ValueError("confidence_set must not contain duplicate labels")
        return self


class DecisionReport(StrictModel):
    summary: str
    key_findings: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    forensic_signal_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    suggested_next_steps: list[str] = Field(default_factory=list)
    misinformation_category: str | None


class HumanReview(StrictModel):
    reviewer: str
    reviewed_at: ISODateTime
    verdict: FactLabel
    reason: str


class Decision(StrictModel):
    run: RunInfo
    atom_set_id: str
    atomic_verdicts: list[AtomicDecision] = Field(default_factory=list)
    base_label: FactLabel
    final_verdict: FactLabel
    probabilities: dict[str, float] | None
    confidence_set: list[FactLabel] | None
    abstention_flag: bool
    abstention_reasons: list[str] = Field(default_factory=list)
    calibration: Calibration
    decision_report: DecisionReport
    human_review: HumanReview | None


# --- Bundle -----------------------------------------------------------------
class BundleInput(StrictModel):
    claim_text: str
    language: str
    image: MediaRef | None
    as_of: ISODateTime | None

    @model_validator(mode="after")
    def _claim_nonempty(self) -> "BundleInput":
        if self.claim_text.strip() == "":
            raise ValueError("input.claim_text must be non-empty after trimming")
        return self


class AuroraBundle(StrictModel):
    schema_version: Literal["1.0.0"]
    case_id: str
    claim_revision: int = Field(ge=1)
    mode: Mode
    created_at: ISODateTime
    input: BundleInput
    analysis: Analysis | None
    retrieval: Retrieval | None
    decision: Decision | None
    warnings: list[Warning] = Field(default_factory=list)
    extensions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _mode_consistency(self) -> "AuroraBundle":
        # All run.mode must match bundle.mode (invariant 12).
        for name, section in (
            ("analysis", self.analysis),
            ("retrieval", self.retrieval),
            ("decision", self.decision),
        ):
            if section is not None and section.run.mode != self.mode:
                raise ValueError(f"{name}.run.mode must match bundle.mode ({self.mode})")
        return self


def _validate_probabilities(probs: dict[str, float] | None, labels: tuple[str, ...]) -> None:
    """probabilities is null OR contains all labels, each finite >=0, sum ~= 1."""
    if probs is None:
        return
    missing = set(labels) - set(probs)
    extra = set(probs) - set(labels)
    if missing or extra:
        raise ValueError(f"probabilities must contain exactly {labels}; got {sorted(probs)}")
    total = 0.0
    for label in labels:
        v = probs[label]
        if not math.isfinite(v) or v < 0:
            raise ValueError(f"probability for {label} must be finite and >= 0")
        total += v
    if abs(total - 1.0) > _PROB_TOL:
        raise ValueError(f"probabilities must sum to 1 within {_PROB_TOL}; got {total}")
