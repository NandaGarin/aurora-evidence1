"""The Dev-2 retrieval pipeline: bundle in, bundle with ``retrieval`` out.

Flow (section 1 of the assignment):

    input -> query planning -> multi-source retrieval -> content extraction
          -> dedup / temporal filtering -> reranking / diversity -> evidence bundle

plus a parallel, semantically separate branch:

    claim text + image -> AI detectors -> forensic_signals

Guarantees this module is responsible for
-----------------------------------------
* ``evidence_list`` contains only sources that were genuinely obtained. Detector
  output never enters it (invariant 6).
* Without ``analysis``, ``atom_set_id`` is ``None`` and every ``atom_ids`` is
  ``[]``; no atom set is ever invented (invariant 2).
* ``excerpt`` is a real substring and ``content.sha256`` hashes ``content.text``
  exactly (invariant 7).
* Every configured capability appears in ``provider_status``, including the ones
  that are ``unconfigured`` — a missing credential must be visible, not silent.
* Detector failure degrades the forensic branch only; factual retrieval still
  succeeds (assignment section 2).
* The assembled bundle is validated against the contract before being returned,
  so we never hand Dev 3 something we would reject on import.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from aurora_evidence.contract.canonical import canonical_sha256, text_sha256
from aurora_evidence.contract.ids import evidence_id as make_evidence_id
from aurora_evidence.contract.ids import new_run_id
from aurora_evidence.contract.validate import ContractViolation, check_bundle
from aurora_evidence.forensics.interfaces import (
    ImageDetectionRequest,
    TextDetectionRequest,
)
from aurora_evidence.forensics.normalization import build_signal
from aurora_evidence.forensics.registry import DetectorRegistry
from aurora_evidence.retrieval.dedup import DedupCandidate, assign_groups, dedup_effectiveness
from aurora_evidence.retrieval.provenance import assess_credibility, assess_temporal
from aurora_evidence.retrieval.queries import plan_queries
from aurora_evidence.retrieval.ranking import (
    RankedItem,
    mmr_diversify,
    reciprocal_rank_fusion,
    rerank,
    source_diversity,
)
from aurora_evidence.retrieval.textutil import content_tokens, excerpt_from
from aurora_evidence.version import PIPELINE_VERSION, SCHEMA_VERSION

DEFAULT_BUDGET_SECONDS = 45.0


@dataclass
class RetrievalSettings:
    """Everything the pipeline needs, resolved from env/config by the caller."""

    mode: str = "demo"
    corpus_path: Path | None = None
    per_query_limit: int = 10
    max_evidence: int = 20
    budget_seconds: float = DEFAULT_BUDGET_SECONDS
    strict_temporal: bool = True
    image_detector_provider: str = "none"
    text_detector_provider: str = "none"
    detector_profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    detector_credentials: dict[str, str] = field(default_factory=dict)
    rerank_weights: dict[str, float] = field(default_factory=dict)
    per_group_cap: int | None = 2
    mmr_lambda: float = 0.7
    corpus_version: str = "unknown"
    #: Extra search providers (web/fact-check/image provenance). Each must expose
    #: ``capability()`` and ``search(query, limit=..., budget_s=...)``.
    extra_providers: list[Any] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass
class _Candidate:
    """Internal working record for one retrieved hit."""

    key: str
    title: str
    text: str
    snippet: str
    url: str | None
    publisher: str | None
    language: str | None
    published_at: str | None
    provider: str
    kind: str
    content_status: str
    discovery_method: str
    atom_ids: list[str] = field(default_factory=list)
    provider_ranks: dict[str, int] = field(default_factory=dict)


def execute(bundle: dict[str, Any], settings: RetrievalSettings) -> dict[str, Any]:
    """Run retrieval for ``bundle`` and return a NEW bundle with ``retrieval`` set.

    The input bundle is not mutated. ``input`` and ``analysis`` are preserved,
    ``retrieval`` is replaced and ``decision`` is cleared, exactly as the contract
    specifies for ``POST /api/v1/retrieve``.
    """
    started_monotonic = time.monotonic()
    started_at = _now_iso()
    run_id = new_run_id()
    mode = bundle.get("mode", settings.mode)

    warnings: list[dict[str, str]] = []
    provider_status: list[dict[str, Any]] = []
    query_log: list[dict[str, Any]] = []

    analysis = bundle.get("analysis")
    has_analysis = isinstance(analysis, dict)
    atoms = list(analysis.get("atomic_claims") or []) if has_analysis else []
    atom_set_id = analysis.get("atom_set_id") if has_analysis else None

    claim_text = bundle["input"]["claim_text"]
    language = bundle["input"].get("language")
    as_of = bundle["input"].get("as_of")

    # ---- 1. query planning ------------------------------------------------
    planned = plan_queries(claim_text, atoms if has_analysis else None, language)

    # ---- 2. multi-source retrieval ---------------------------------------
    providers = _build_providers(settings, provider_status)
    candidates: dict[str, _Candidate] = {}
    ranked_lists: dict[str, list[str]] = {}
    budget_exhausted = False

    for query in planned:
        for provider in providers:
            elapsed = time.monotonic() - started_monotonic
            remaining = settings.budget_seconds - elapsed
            if remaining <= 0:
                budget_exhausted = True
                break
            query_started = time.monotonic()
            try:
                hits = provider.search(
                    query.query, limit=settings.per_query_limit, budget_s=remaining
                )
            except Exception as exc:  # a provider must never kill the run
                warnings.append(
                    {
                        "code": "PROVIDER_FAILED",
                        "message": f"Provider {getattr(provider, 'name', provider.__class__.__name__)} gagal: {exc}",
                        "component": "retrieval.providers",
                    }
                )
                _set_status(provider_status, provider, "failed", str(exc))
                continue
            duration_ms = max(0, int((time.monotonic() - query_started) * 1000))

            list_key = f"{getattr(provider, 'name', provider.__class__.__name__)}:{query.query_id}"
            order: list[str] = []
            for hit in hits:
                key = _candidate_key(hit)
                order.append(key)
                existing = candidates.get(key)
                if existing is None:
                    candidates[key] = _to_candidate(key, hit, query)
                else:
                    # Same document found by several queries: union the atom refs.
                    for atom_id in query.atom_ids:
                        if atom_id not in existing.atom_ids:
                            existing.atom_ids.append(atom_id)
            if order:
                ranked_lists[list_key] = order

            query_log.append(
                {
                    "query_id": f"{query.query_id}:{getattr(provider, 'name', 'provider')}",
                    "atom_ids": list(query.atom_ids) if has_analysis else [],
                    "provider": getattr(provider, "name", provider.__class__.__name__),
                    "query": query.query,
                    "duration_ms": duration_ms,
                    "result_count": len(hits),
                }
            )
        if budget_exhausted:
            break

    if budget_exhausted:
        warnings.append(
            {
                "code": "TIME_BUDGET_EXHAUSTED",
                "message": (
                    f"Anggaran waktu {settings.budget_seconds}s habis; hasil bersifat "
                    "parsial dan sebagian query tidak dijalankan."
                ),
                "component": "retrieval.pipeline",
            }
        )

    # ---- 3. dedup and independence grouping ------------------------------
    ordered = list(candidates.values())
    assignments = assign_groups(
        [
            DedupCandidate(
                key=candidate.key,
                text=candidate.text,
                url=candidate.url,
                publisher=candidate.publisher,
                title=candidate.title,
            )
            for candidate in ordered
        ]
    )

    # ---- 4. temporal eligibility + credibility ---------------------------
    temporal: dict[str, Any] = {}
    credibility: dict[str, Any] = {}
    for candidate in ordered:
        temporal[candidate.key] = assess_temporal(
            published_at=candidate.published_at,
            first_seen_at=None,
            as_of=as_of,
            strict=settings.strict_temporal,
        )
        credibility[candidate.key] = assess_credibility(
            kind=candidate.kind,
            url=candidate.url,
            publisher=candidate.publisher,
            published_at=candidate.published_at,
            content_status=candidate.content_status,
        )

    # ---- 5. fusion, reranking, diversity ---------------------------------
    fusion = reciprocal_rank_fusion(ranked_lists)
    items = [
        RankedItem(
            key=candidate.key,
            title=candidate.title,
            text=candidate.text,
            kind=candidate.kind,
            provider=candidate.provider,
            independence_group_id=assignments[candidate.key].independence_group_id,
            content_status=candidate.content_status,
            credibility=credibility[candidate.key].score,
            temporal_eligible=temporal[candidate.key].temporal_eligible,
            has_image_match=False,
            provider_ranks=candidate.provider_ranks,
        )
        for candidate in ordered
    ]
    for item in items:
        item.fusion_score = fusion.get(item.key, 0.0)

    reranked = rerank(items, query=claim_text, weights=settings.rerank_weights)
    diversified = mmr_diversify(
        reranked,
        lambda_relevance=settings.mmr_lambda,
        per_group_cap=settings.per_group_cap,
        limit=settings.max_evidence,
    )[: settings.max_evidence]

    # ---- 6. assemble evidence -------------------------------------------
    by_key = {candidate.key: candidate for candidate in ordered}
    query_tokens = content_tokens(claim_text)
    evidence_list: list[dict[str, Any]] = []
    for sequence, item in enumerate(diversified, start=1):
        candidate = by_key[item.key]
        assignment = assignments[candidate.key]
        verdict = temporal[candidate.key]
        credit = credibility[candidate.key]
        text = candidate.text
        excerpt = excerpt_from(text, query_tokens) if text else ""

        evidence_list.append(
            {
                "evidence_id": make_evidence_id(run_id, sequence),
                # Invariant 2: no atoms without an analysis.
                "atom_ids": sorted(candidate.atom_ids) if has_analysis else [],
                "source": {
                    "provider": candidate.provider,
                    "kind": candidate.kind,
                    "url": candidate.url,
                    "title": candidate.title,
                    "publisher": candidate.publisher,
                    "language": candidate.language,
                    "published_at": candidate.published_at,
                    "retrieved_at": started_at,
                },
                "content": {
                    "text": text,
                    "excerpt": excerpt,
                    "sha256": text_sha256(text),
                    "status": candidate.content_status,
                },
                "provenance": {
                    "original_url": candidate.url,
                    "archive_url": None,
                    # Unknown times stay null; we do not infer them.
                    "first_seen_at": None,
                    "captured_at": None,
                    "date_basis": verdict.date_basis,
                    "discovery_method": candidate.discovery_method,
                    "duplicate_cluster_id": assignment.duplicate_cluster_id,
                    "independence_group_id": assignment.independence_group_id,
                    "temporal_eligible": verdict.temporal_eligible,
                    "usage_note": verdict.usage_note,
                    "image_matches": [],
                },
                "relevance_score": item.relevance_score,
                "credibility_score": credit.score,
                "rerank_score": item.rerank_score,
                "score_breakdown": {
                    **item.score_breakdown,
                    **{
                        f"credibility.{name}": value
                        for name, value in credit.components.items()
                    },
                },
            }
        )

    # ---- 7. forensic branch (independent of the above) -------------------
    forensic_signals, forensic_warnings = _run_detectors(
        bundle=bundle,
        settings=settings,
        run_id=run_id,
        provider_status=provider_status,
    )
    warnings.extend(forensic_warnings)

    # ---- 8. assemble retrieval ------------------------------------------
    status = "partial" if (budget_exhausted or _has_failure(provider_status)) else "completed"
    if not evidence_list and status == "completed":
        # An empty result is a legitimate outcome, but it must be visible.
        warnings.append(
            {
                "code": "NO_EVIDENCE_FOUND",
                "message": (
                    "Tidak ada bukti yang ditemukan oleh sumber yang aktif. Ini bukan "
                    "bukti bahwa klaim salah maupun benar."
                ),
                "component": "retrieval.pipeline",
            }
        )

    retrieval = {
        "run": {
            "run_id": run_id,
            "mode": mode,
            "started_at": started_at,
            "finished_at": _now_iso(),
            "status": status,
            "versions": {
                "aurora_evidence": PIPELINE_VERSION,
                "schema": SCHEMA_VERSION,
                "corpus": settings.corpus_version,
                "query_planner": "deterministic-1",
                "reranker": "feature-linear-1",
                "dedup": "simhash+jaccard-1",
            },
            "warnings": warnings,
        },
        "atom_set_id": atom_set_id if has_analysis else None,
        "evidence_list": evidence_list,
        "forensic_signals": forensic_signals,
        "query_log": query_log,
        "provider_status": provider_status,
    }

    # ---- 9. new bundle ---------------------------------------------------
    result = dict(bundle)
    result["retrieval"] = retrieval
    # Contract: retrieve replaces retrieval and clears the downstream decision.
    result["decision"] = None
    result["extensions"] = _record_run_inputs(bundle, run_id, settings, assignments, diversified)

    problems = check_bundle(result)
    if problems:
        # Refuse to emit a bundle we would reject on import. Failing here is far
        # cheaper than letting Dev 3 discover the inconsistency later.
        raise ContractViolation(problems)
    return result


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _candidate_key(hit: Any) -> str:
    raw = getattr(hit, "raw", {}) or {}
    corpus_id = raw.get("corpus_id")
    if corpus_id:
        return f"corpus:{corpus_id}"
    if hit.url:
        return f"url:{hit.url}"
    return f"text:{text_sha256((hit.full_text or hit.snippet or '') + hit.title)[:32]}"


def _to_candidate(key: str, hit: Any, query: Any) -> _Candidate:
    """Convert a provider hit into a working candidate.

    ``content.status`` is set from what we actually obtained: full text when the
    provider gave us the body, ``snippet_only`` when all we have is a search
    snippet. Claiming ``full`` for a snippet would assert we read the page.
    """
    full_text = getattr(hit, "full_text", None)
    if full_text:
        text, status = full_text, "full"
    elif hit.snippet:
        text, status = hit.snippet, "snippet_only"
    else:
        text, status = "", "unavailable"
    return _Candidate(
        key=key,
        title=hit.title or "",
        text=text,
        snippet=hit.snippet or "",
        url=hit.url,
        publisher=hit.publisher,
        language=hit.language,
        published_at=hit.published_at,
        provider=hit.provider or "unknown",
        kind=hit.kind or "web",
        content_status=status,
        discovery_method=f"{hit.provider or 'unknown'}:{query.variation}",
        atom_ids=list(query.atom_ids),
        provider_ranks=(
            {hit.provider: hit.provider_rank} if hit.provider_rank is not None else {}
        ),
    )


def _build_providers(settings: RetrievalSettings, provider_status: list[dict[str, Any]]) -> list[Any]:
    """Instantiate search providers and record every capability's status."""
    from aurora_evidence.retrieval.providers.local_corpus import LocalCorpusProvider

    active: list[Any] = []

    if settings.corpus_path is not None:
        local = LocalCorpusProvider(settings.corpus_path)
        capability = local.capability()
        local.name = "local_corpus"  # type: ignore[attr-defined]
        provider_status.append(
            {
                "provider": capability.provider,
                "capability": "local_corpus_search",
                "status": capability.status,
                "message": capability.message,
            }
        )
        if capability.status == "ok":
            active.append(local)
    else:
        provider_status.append(
            {
                "provider": "local_corpus",
                "capability": "local_corpus_search",
                "status": "unconfigured",
                "message": "Corpus lokal belum diatur (AURORA_CORPUS_PATH).",
            }
        )

    for provider in settings.extra_providers:
        capability = provider.capability()
        if not hasattr(provider, "name"):
            provider.name = capability.provider  # type: ignore[attr-defined]
        provider_status.append(
            {
                "provider": capability.provider,
                "capability": capability.capability,
                "status": capability.status,
                "message": capability.message,
            }
        )
        if capability.status == "ok":
            active.append(provider)
    return active


def _set_status(
    provider_status: list[dict[str, Any]], provider: Any, status: str, message: str
) -> None:
    name = getattr(provider, "name", provider.__class__.__name__)
    for entry in provider_status:
        if entry["provider"] == name:
            entry["status"] = status
            entry["message"] = message[:500]
            return
    provider_status.append(
        {"provider": name, "capability": "unknown", "status": status, "message": message[:500]}
    )


def _has_failure(provider_status: Sequence[dict[str, Any]]) -> bool:
    return any(entry["status"] in {"failed", "rate_limited"} for entry in provider_status)


def _run_detectors(
    *,
    bundle: dict[str, Any],
    settings: RetrievalSettings,
    run_id: str,
    provider_status: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Run the AI-detector branch. Never raises into the factual pipeline."""
    registry = DetectorRegistry(
        profiles=settings.detector_profiles,
        credentials=settings.detector_credentials,
        mode=bundle.get("mode", settings.mode),
    )
    signals: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    sequence = 0

    claim_text = bundle["input"]["claim_text"]
    language = bundle["input"].get("language")

    # -- text branch: the primary target is the original claim_text ---------
    text_detector = registry.text_detector(settings.text_detector_provider)
    capability = text_detector.capability()
    provider_status.append(
        {
            "provider": capability.provider,
            "capability": "text_ai_detection",
            "status": capability.status,
            "message": capability.message,
        }
    )
    if capability.status == "ok":
        sequence += 1
        try:
            outcome = text_detector.detect(
                TextDetectionRequest(
                    text=claim_text,
                    text_sha256=text_sha256(claim_text),
                    target_kind="claim_text",
                    language=language,
                )
            )
            signals.append(
                build_signal(
                    run_id=run_id,
                    sequence=sequence,
                    provider=capability.provider,
                    modality="text",
                    outcome=outcome,
                    analyzed_at=_now_iso(),
                    target_kind="claim_text",
                    text_sha256=text_sha256(claim_text),
                    extra_limitations=capability.limitations,
                )
            )
        except Exception as exc:
            sequence -= 1
            warnings.append(
                {
                    "code": "TEXT_DETECTOR_FAILED",
                    "message": (
                        f"Detector teks gagal: {exc}. Retrieval faktual tidak terpengaruh."
                    ),
                    "component": "forensics.text",
                }
            )

    # -- image branch -------------------------------------------------------
    image = bundle["input"].get("image")
    image_detector = registry.image_detector(settings.image_detector_provider)
    image_capability = image_detector.capability()
    provider_status.append(
        {
            "provider": image_capability.provider,
            "capability": "image_ai_detection",
            "status": image_capability.status,
            "message": image_capability.message,
        }
    )
    if image_capability.status == "ok" and isinstance(image, dict):
        data = settings.detector_credentials.get("__image_bytes__")
        image_bytes = data.encode("latin-1") if isinstance(data, str) else b""
        if not image_bytes:
            warnings.append(
                {
                    "code": "IMAGE_BYTES_UNAVAILABLE",
                    "message": (
                        "Bundle memuat MediaRef tetapi byte gambar tidak tersedia di "
                        "penyimpanan lokal, sehingga deteksi gambar tidak dijalankan."
                    ),
                    "component": "forensics.image",
                }
            )
        else:
            sequence += 1
            try:
                outcome = image_detector.detect(
                    ImageDetectionRequest(
                        asset_id=image["asset_id"],
                        sha256=image["sha256"],
                        data=image_bytes,
                        media_type=image.get("media_type", "application/octet-stream"),
                        width=int(image.get("width") or 0),
                        height=int(image.get("height") or 0),
                    )
                )
                signals.append(
                    build_signal(
                        run_id=run_id,
                        sequence=sequence,
                        provider=image_capability.provider,
                        modality="image",
                        outcome=outcome,
                        analyzed_at=_now_iso(),
                        target_kind="image",
                        asset_id=image["asset_id"],
                        extra_limitations=image_capability.limitations,
                    )
                )
            except Exception as exc:
                sequence -= 1
                warnings.append(
                    {
                        "code": "IMAGE_DETECTOR_FAILED",
                        "message": (
                            f"Detector gambar gagal: {exc}. Retrieval faktual tidak terpengaruh."
                        ),
                        "component": "forensics.image",
                    }
                )
    return signals, warnings


def _record_run_inputs(
    bundle: dict[str, Any],
    run_id: str,
    settings: RetrievalSettings,
    assignments: dict[str, Any],
    ranked: Sequence[RankedItem],
) -> dict[str, Any]:
    """Record parent runs and input hashes under extensions.aurora_contract.

    The contract requires this so an old output can never be attached to a new
    run or revision: the snapshot hash pins exactly which input produced this
    retrieval.
    """
    extensions = dict(bundle.get("extensions") or {})
    contract_ext = dict(extensions.get("aurora_contract") or {})
    run_inputs = dict(contract_ext.get("run_inputs") or {})

    analysis = bundle.get("analysis")
    input_snapshot = {
        "case_id": bundle.get("case_id"),
        "claim_revision": bundle.get("claim_revision"),
        "claim_text_sha256": text_sha256(bundle["input"]["claim_text"]),
        "image_sha256": (bundle["input"].get("image") or {}).get("sha256")
        if bundle["input"].get("image")
        else None,
        "atom_set_id": analysis.get("atom_set_id") if isinstance(analysis, dict) else None,
    }
    run_inputs[run_id] = {
        "produced_by": "aurora-evidence/retrieve",
        "parent_run_ids": (
            [analysis["run"]["run_id"]]
            if isinstance(analysis, dict) and isinstance(analysis.get("run"), dict)
            else []
        ),
        "input_snapshot": input_snapshot,
        "input_snapshot_sha256": canonical_sha256(input_snapshot),
        "config_hash": canonical_sha256(
            {
                "per_query_limit": settings.per_query_limit,
                "max_evidence": settings.max_evidence,
                "strict_temporal": settings.strict_temporal,
                "per_group_cap": settings.per_group_cap,
                "mmr_lambda": settings.mmr_lambda,
                "rerank_weights": settings.rerank_weights,
                "corpus_version": settings.corpus_version,
                "image_detector": settings.image_detector_provider,
                "text_detector": settings.text_detector_provider,
            }
        ),
    }
    contract_ext["run_inputs"] = run_inputs
    extensions["aurora_contract"] = contract_ext

    evidence_ext = dict(extensions.get("aurora_evidence") or {})
    evidence_ext["last_run"] = {
        "run_id": run_id,
        "dedup": dedup_effectiveness(assignments),
        "diversity": source_diversity(ranked),
    }
    extensions["aurora_evidence"] = evidence_ext
    return extensions
