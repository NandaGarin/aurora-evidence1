"""Deduplication and independence grouping.

Two different questions, deliberately kept separate:

``duplicate_cluster_id``
    "Is this the same document?" — same canonical URL, identical normalized
    text, or near-identical text (SimHash).

``independence_group_id``
    "Would counting both of these twice be double counting?" — Dev 3 uses this
    to stop one wire story republished across ten domains from acting like ten
    independent sources. It is therefore *coarser* than the duplicate cluster:
    syndicated copies with different URLs and different publishers still land in
    one independence group.

Contract constraints honoured here:

* Duplicates and syndication share an ``independence_group_id`` even when URLs
  differ (invariant 8).
* Nothing is discarded. Every candidate keeps its provenance; grouping only
  annotates. Removing "redundant" sources would destroy the audit trail and
  hide that a claim rests on a single origin.
* A missing group is never treated as a fresh independent source. Every item
  gets an explicit group id plus the rule that assigned it, recorded in
  ``grouping_basis``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from aurora_evidence.retrieval.textutil import (
    canonical_url,
    content_tokens,
    hamming_distance,
    jaccard,
    normalized_text_hash,
    registrable_domain,
    shingles,
    simhash,
)

#: Near-duplicate detection is a two-stage test, because no single SimHash
#: threshold separates the cases well. Measured on the demo corpus (see
#: docs/evaluation.md, "dedup threshold calibration"):
#:
#:   same wire story + extra sentence   -> Hamming  6, shingle Jaccard 0.86
#:   same wire story + headline prefix  -> Hamming  4, shingle Jaccard 0.93
#:   same event, independently written  -> Hamming 30, shingle Jaccard 0.00
#:   different topic                    -> Hamming 32, shingle Jaccard 0.00
#:
#: So SimHash is used only as a cheap candidate gate (it is O(1) per pair), and
#: the decision is confirmed with shingle overlap. Relying on Hamming alone
#: forced a choice between missing edited copies (threshold 3) and risking
#: merges of unrelated short documents (threshold 12).
DEFAULT_SIMHASH_THRESHOLD = 12

#: Confirmation threshold: fraction of shared 4-gram shingles required before two
#: documents are declared near-duplicates. The measured gap between 0.86 and
#: 0.00 leaves a wide margin, so this is not finely tuned.
DEFAULT_JACCARD_THRESHOLD = 0.7

#: Documents shorter than this have too few shingles for SimHash to be
#: meaningful, so only exact-normalized-text equality is used for them.
MIN_TOKENS_FOR_SIMHASH = 12


@dataclass
class DedupCandidate:
    """Minimal view of a retrieved item that dedup needs."""

    key: str
    text: str
    url: str | None = None
    publisher: str | None = None
    title: str = ""

    def __post_init__(self) -> None:
        self.canonical = canonical_url(self.url)
        self.domain = registrable_domain(self.url)
        self.tokens = content_tokens(f"{self.title} {self.text}")
        self.text_hash = normalized_text_hash(self.text) if self.text.strip() else None
        self.simhash = (
            simhash(self.tokens) if len(self.tokens) >= MIN_TOKENS_FOR_SIMHASH else 0
        )
        self.shingle_set = set(shingles(self.tokens, 4))


@dataclass
class GroupAssignment:
    duplicate_cluster_id: str
    independence_group_id: str
    duplicate_of: list[str] = field(default_factory=list)
    grouping_basis: list[str] = field(default_factory=list)


class _UnionFind:
    def __init__(self, keys: Iterable[str]) -> None:
        self._parent = {key: key for key in keys}

    def find(self, key: str) -> str:
        root = key
        while self._parent[root] != root:
            root = self._parent[root]
        # Path compression.
        while self._parent[key] != root:
            self._parent[key], key = root, self._parent[key]
        return root

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            # Keep the lexicographically smaller root so ids are deterministic.
            if right_root < left_root:
                left_root, right_root = right_root, left_root
            self._parent[right_root] = left_root

    def groups(self) -> dict[str, list[str]]:
        clusters: dict[str, list[str]] = {}
        for key in self._parent:
            clusters.setdefault(self.find(key), []).append(key)
        return clusters


def _stable_id(prefix: str, members: Sequence[str]) -> str:
    """Deterministic opaque id derived from sorted member keys.

    Stable within a corpus/run snapshot, which is what the contract requires,
    and reproducible across processes so exports can be diffed.
    """
    digest = hashlib.sha256("\x1f".join(sorted(members)).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:24]}"


def assign_groups(
    candidates: Sequence[DedupCandidate],
    *,
    simhash_threshold: int = DEFAULT_SIMHASH_THRESHOLD,
    jaccard_threshold: float = DEFAULT_JACCARD_THRESHOLD,
) -> dict[str, GroupAssignment]:
    """Compute duplicate clusters and independence groups for ``candidates``."""
    keys = [candidate.key for candidate in candidates]
    by_key = {candidate.key: candidate for candidate in candidates}

    duplicates = _UnionFind(keys)
    independence = _UnionFind(keys)
    basis: dict[str, set[str]] = {key: set() for key in keys}

    # --- rule 1: identical canonical URL -> same document -------------------
    by_canonical: dict[str, list[str]] = {}
    for candidate in candidates:
        if candidate.canonical:
            by_canonical.setdefault(candidate.canonical, []).append(candidate.key)
    for group in by_canonical.values():
        for other in group[1:]:
            duplicates.union(group[0], other)
            independence.union(group[0], other)
            basis[other].add("same_canonical_url")
            basis[group[0]].add("same_canonical_url")

    # --- rule 2: identical normalized text -> same document ----------------
    by_text: dict[str, list[str]] = {}
    for candidate in candidates:
        if candidate.text_hash:
            by_text.setdefault(candidate.text_hash, []).append(candidate.key)
    for group in by_text.values():
        for other in group[1:]:
            duplicates.union(group[0], other)
            independence.union(group[0], other)
            basis[other].add("exact_text_hash")
            basis[group[0]].add("exact_text_hash")

    # --- rule 3: near-duplicate text -> syndication ------------------------
    # Stage 1 (SimHash Hamming) narrows the pairs; stage 2 (shingle Jaccard)
    # decides. See the threshold notes at the top of this module.
    hashed = [c for c in candidates if c.simhash]
    for index, left in enumerate(hashed):
        for right in hashed[index + 1 :]:
            if hamming_distance(left.simhash, right.simhash) > simhash_threshold:
                continue
            if jaccard(left.shingle_set, right.shingle_set) < jaccard_threshold:
                continue
            duplicates.union(left.key, right.key)
            independence.union(left.key, right.key)
            basis[left.key].add("near_duplicate_text")
            basis[right.key].add("near_duplicate_text")

    # --- rule 4: same registrable domain -> not independent of each other --
    # Two articles from one outlet are editorially linked even when their text
    # differs, so they must not vote twice. This affects the independence group
    # only; they remain distinct documents.
    by_domain: dict[str, list[str]] = {}
    for candidate in candidates:
        grouping_key = candidate.domain or (
            f"publisher:{candidate.publisher.casefold()}" if candidate.publisher else None
        )
        if grouping_key:
            by_domain.setdefault(grouping_key, []).append(candidate.key)
    for group in by_domain.values():
        for other in group[1:]:
            independence.union(group[0], other)
            basis[other].add("same_publisher_or_domain")
            basis[group[0]].add("same_publisher_or_domain")

    duplicate_clusters = duplicates.groups()
    independence_groups = independence.groups()
    duplicate_ids = {
        root: _stable_id("dc", members) for root, members in duplicate_clusters.items()
    }
    independence_ids = {
        root: _stable_id("ig", members) for root, members in independence_groups.items()
    }

    assignments: dict[str, GroupAssignment] = {}
    for key in keys:
        duplicate_root = duplicates.find(key)
        siblings = [
            other for other in duplicate_clusters[duplicate_root] if other != key
        ]
        reasons = sorted(basis[key])
        if not reasons:
            # Explicit: this item was not matched to anything by any rule. That
            # is a statement about our rules, not proof of independence.
            reasons = ["singleton_no_match"]
        assignments[key] = GroupAssignment(
            duplicate_cluster_id=duplicate_ids[duplicate_root],
            independence_group_id=independence_ids[independence.find(key)],
            duplicate_of=sorted(siblings),
            grouping_basis=reasons,
        )
    _ = by_key  # kept for readability of the mapping above
    return assignments


def dedup_effectiveness(assignments: dict[str, GroupAssignment]) -> dict[str, float | int]:
    """Metrics for docs/evaluation.md: how much redundancy was detected."""
    total = len(assignments)
    if total == 0:
        return {
            "items": 0,
            "duplicate_clusters": 0,
            "independence_groups": 0,
            "duplicate_reduction_ratio": 0.0,
            "independence_reduction_ratio": 0.0,
        }
    clusters = len({a.duplicate_cluster_id for a in assignments.values()})
    groups = len({a.independence_group_id for a in assignments.values()})
    return {
        "items": total,
        "duplicate_clusters": clusters,
        "independence_groups": groups,
        # 0.0 means "everything was distinct"; higher means more redundancy found.
        "duplicate_reduction_ratio": round(1.0 - clusters / total, 6),
        "independence_reduction_ratio": round(1.0 - groups / total, 6),
    }
