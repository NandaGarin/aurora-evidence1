"""Two-stage ranking: Reciprocal Rank Fusion, then a feature reranker, then MMR.

Stage 1 — fusion
    Providers return incomparable scores (BM25 saturating sums, vendor relevance
    floats, or nothing but a rank). Reciprocal Rank Fusion only needs the rank
    order, which is exactly what all of them agree on, so it avoids inventing a
    common scale that does not exist.

Stage 2 — reranking
    A transparent linear model over features we can actually compute on CPU. A
    multilingual cross-encoder is the intended upgrade and plugs in behind the
    same ``Reranker`` interface; the weights here are declared HYPOTHESES and may
    only be tuned on validation data.

Stage 3 — diversity
    Maximal Marginal Relevance plus a per-independence-group cap, so ten copies
    of one wire story cannot fill the page. Diversity re-orders and caps; it
    never deletes a source for disagreeing with the caption.

Score semantics (contract):
    ``rerank_score`` may be an unbounded logit.
    ``relevance_score`` is [0,1] via the documented logistic map below and is
    *not* a probability that the claim is true.
    A missing score stays ``None`` and is never coerced to 0.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from aurora_evidence.retrieval.textutil import (
    STOPWORDS,
    content_tokens,
    jaccard,
    shingles,
    tokenize,
)

#: RRF damping constant. 60 is the value from the original Cormack et al. paper
#: and is kept as the documented default rather than tuned on our own test set.
RRF_K = 60

#: Feature weights for the stage-2 reranker.
#:
#: HYPOTHESES, not fitted values. They encode the ordering we expect (matching
#: the claim's terms matters most; having usable content matters next; source
#: type and provenance are supporting signals) and must be tuned on a validation
#: split only — never on test or calibration data.
DEFAULT_WEIGHTS: dict[str, float] = {
    "query_coverage": 2.4,
    "title_match": 0.9,
    "body_overlap": 1.2,
    "fusion_prior": 1.1,
    "content_completeness": 0.8,
    "credibility": 0.6,
    "image_match": 0.7,
    "temporal_eligible": 0.5,
    "temporal_ineligible_penalty": -1.4,
    "unknown_date_penalty": -0.3,
    "bias": -1.5,
}

#: Logistic steepness for mapping rerank_score -> relevance_score.
RELEVANCE_SLOPE = 1.0


@dataclass
class RankedItem:
    """A candidate with its fusion inputs and, after ranking, its scores."""

    key: str
    title: str
    text: str
    kind: str
    provider: str
    independence_group_id: str
    content_status: str = "full"
    credibility: float | None = None
    temporal_eligible: bool | None = None
    has_image_match: bool = False
    provider_ranks: dict[str, int] = field(default_factory=dict)

    fusion_score: float = 0.0
    rerank_score: float | None = None
    relevance_score: float | None = None
    score_breakdown: dict[str, float] = field(default_factory=dict)
    ranking_reasons: list[str] = field(default_factory=list)


def reciprocal_rank_fusion(
    ranked_lists: dict[str, Sequence[str]], *, k: int = RRF_K
) -> dict[str, float]:
    """RRF over ``{list_name: [key, ...]}`` where position 0 is the best hit."""
    fused: dict[str, float] = {}
    for keys in ranked_lists.values():
        for position, key in enumerate(keys):
            fused[key] = fused.get(key, 0.0) + 1.0 / (k + position + 1)
    return fused


def _query_coverage(query_tokens: set[str], doc_tokens: set[str]) -> float:
    """Fraction of meaningful query terms present in the document."""
    if not query_tokens:
        return 0.0
    return len(query_tokens & doc_tokens) / len(query_tokens)


def compute_features(
    item: RankedItem,
    *,
    query: str,
    max_fusion: float,
) -> dict[str, float]:
    """Compute the reranker's feature vector. Every feature is inspectable."""
    query_tokens = {token for token in content_tokens(query) if token not in STOPWORDS}
    title_tokens = set(tokenize(item.title))
    body_tokens = set(content_tokens(item.text))

    features: dict[str, float] = {
        "query_coverage": _query_coverage(query_tokens, body_tokens | title_tokens),
        "title_match": _query_coverage(query_tokens, title_tokens),
        "body_overlap": jaccard(
            set(shingles(content_tokens(query), 2)),
            set(shingles(content_tokens(item.text), 2)),
        ),
        # Normalized so fusion contributes comparably across result-set sizes.
        "fusion_prior": (item.fusion_score / max_fusion) if max_fusion > 0 else 0.0,
        "content_completeness": {
            "full": 1.0,
            "excerpt_only": 0.6,
            "snippet_only": 0.3,
            "unavailable": 0.0,
        }.get(item.content_status, 0.0),
        # Missing credibility must not read as 0 evidence of low credibility;
        # 0.5 is the explicit "unknown" midpoint and is recorded as such.
        "credibility": item.credibility if item.credibility is not None else 0.5,
        "image_match": 1.0 if item.has_image_match else 0.0,
    }

    # Temporal handling is split into three mutually exclusive terms so the
    # breakdown shows which case applied instead of hiding it in one number.
    if item.temporal_eligible is True:
        features["temporal_eligible"] = 1.0
        features["temporal_ineligible_penalty"] = 0.0
        features["unknown_date_penalty"] = 0.0
    elif item.temporal_eligible is False:
        features["temporal_eligible"] = 0.0
        features["temporal_ineligible_penalty"] = 1.0
        features["unknown_date_penalty"] = 0.0
    else:
        features["temporal_eligible"] = 0.0
        features["temporal_ineligible_penalty"] = 0.0
        features["unknown_date_penalty"] = 1.0

    features["bias"] = 1.0
    return features


def relevance_from_rerank(rerank_score: float, *, slope: float = RELEVANCE_SLOPE) -> float:
    """Documented map from an unbounded rerank logit to a [0,1] relevance score.

    ``relevance = 1 / (1 + exp(-slope * rerank_score))``

    This is a monotone squashing function for display and thresholding only. It
    is NOT calibrated, so it must never be presented as the probability that the
    evidence is relevant, let alone that the claim is true.
    """
    return 1.0 / (1.0 + math.exp(-slope * rerank_score))


def rerank(
    items: Sequence[RankedItem],
    *,
    query: str,
    weights: dict[str, float] | None = None,
) -> list[RankedItem]:
    """Score items with the linear feature model, filling in the breakdown."""
    active = dict(DEFAULT_WEIGHTS)
    if weights:
        active.update(weights)

    max_fusion = max((item.fusion_score for item in items), default=0.0)
    for item in items:
        features = compute_features(item, query=query, max_fusion=max_fusion)
        contributions = {
            name: round(active.get(name, 0.0) * value, 6)
            for name, value in features.items()
        }
        score = sum(contributions.values())
        item.rerank_score = round(score, 6)
        item.relevance_score = round(relevance_from_rerank(score), 6)
        # Keep both the raw features and their weighted contributions: the raw
        # value explains the document, the contribution explains the ranking.
        item.score_breakdown = {
            **{f"feature.{name}": round(value, 6) for name, value in features.items()},
            **{f"contribution.{name}": value for name, value in contributions.items()},
            "fusion_rrf": round(item.fusion_score, 6),
        }
        item.ranking_reasons = _explain(features, contributions)
    return sorted(items, key=lambda item: item.rerank_score or 0.0, reverse=True)


def _explain(features: dict[str, float], contributions: dict[str, float]) -> list[str]:
    """Short human-readable reasons drawn from the real feature values."""
    reasons: list[str] = []
    coverage = features.get("query_coverage", 0.0)
    if coverage >= 0.6:
        reasons.append(f"Mencakup {coverage:.0%} istilah kunci klaim.")
    elif coverage <= 0.2:
        reasons.append(f"Hanya mencakup {coverage:.0%} istilah kunci klaim.")
    if features.get("title_match", 0.0) >= 0.5:
        reasons.append("Judul cocok dengan unsur klaim.")
    if features.get("content_completeness", 0.0) <= 0.3:
        reasons.append("Isi yang berhasil diambil terbatas (snippet/tidak tersedia).")
    if features.get("image_match", 0.0) > 0:
        reasons.append("Terhubung ke gambar melalui kecocokan provenance.")
    if features.get("temporal_ineligible_penalty", 0.0) > 0:
        reasons.append("Terbit setelah as_of sehingga tidak layak untuk keputusan itu.")
    if features.get("unknown_date_penalty", 0.0) > 0:
        reasons.append("Tanggal sumber tidak diketahui.")
    ranked = sorted(contributions.items(), key=lambda pair: abs(pair[1]), reverse=True)
    top = [name for name, value in ranked if name != "bias" and abs(value) > 1e-9][:3]
    if top:
        reasons.append("Fitur paling berpengaruh: " + ", ".join(top) + ".")
    return reasons


def mmr_diversify(
    items: Sequence[RankedItem],
    *,
    lambda_relevance: float = 0.7,
    per_group_cap: int | None = 2,
    limit: int | None = None,
) -> list[RankedItem]:
    """Maximal Marginal Relevance with a per-independence-group cap.

    MMR trades relevance against novelty; the group cap is a hard backstop so a
    syndicated cluster cannot dominate even if every copy scores well. Items
    beyond the cap are *deferred to the tail*, not dropped, because the contract
    requires keeping all provenance and not hiding disagreeing sources.
    """
    if not items:
        return []
    pool = list(items)
    selected: list[RankedItem] = []
    deferred: list[RankedItem] = []
    group_counts: dict[str, int] = {}
    token_cache = {item.key: set(content_tokens(f"{item.title} {item.text}")) for item in pool}

    while pool:
        best_item: RankedItem | None = None
        best_value = -math.inf
        for candidate in pool:
            if (
                per_group_cap is not None
                and group_counts.get(candidate.independence_group_id, 0) >= per_group_cap
            ):
                continue
            relevance = candidate.relevance_score or 0.0
            redundancy = max(
                (jaccard(token_cache[candidate.key], token_cache[chosen.key]) for chosen in selected),
                default=0.0,
            )
            value = lambda_relevance * relevance - (1.0 - lambda_relevance) * redundancy
            if value > best_value:
                best_item, best_value = candidate, value

        if best_item is None:
            # Everything left is over its group cap: keep them, but at the tail.
            deferred.extend(
                sorted(pool, key=lambda item: item.relevance_score or 0.0, reverse=True)
            )
            break

        selected.append(best_item)
        pool.remove(best_item)
        group_counts[best_item.independence_group_id] = (
            group_counts.get(best_item.independence_group_id, 0) + 1
        )
        if limit is not None and len(selected) >= limit:
            deferred.extend(
                sorted(pool, key=lambda item: item.relevance_score or 0.0, reverse=True)
            )
            break

    return selected + deferred


def source_diversity(items: Iterable[RankedItem]) -> dict[str, int | float]:
    """Diversity measured over independent groups, not raw document count."""
    materialized = list(items)
    if not materialized:
        return {"items": 0, "independent_groups": 0, "providers": 0, "group_ratio": 0.0}
    groups = {item.independence_group_id for item in materialized}
    return {
        "items": len(materialized),
        "independent_groups": len(groups),
        "providers": len({item.provider for item in materialized}),
        "group_ratio": round(len(groups) / len(materialized), 6),
    }
