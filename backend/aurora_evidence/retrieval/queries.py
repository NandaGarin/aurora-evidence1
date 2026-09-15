"""Deterministic query planning.

Builds queries from the caption and, when Development 1 atoms are present, from
entities/actions/time/location. Preserves names, numbers, and negation. LLM
query expansion is intentionally NOT implemented here (optional per prompt); the
deterministic baseline is mandatory and sufficient for local retrieval.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_WORD_RE = re.compile(r"\w+", re.UNICODE)
# Light stopword list (id + en) for building focused entity queries.
_STOPWORDS = {
    "yang", "dan", "di", "ke", "dari", "pada", "untuk", "dengan", "adalah", "itu",
    "ini", "para", "sebuah", "the", "a", "an", "of", "in", "on", "at", "and", "to",
    "for", "with", "is", "are", "was", "were",
}


@dataclass
class PlannedQuery:
    query_id: str
    query: str
    atom_ids: list[str] = field(default_factory=list)
    variation: str = "caption"  # caption|entity|temporal|fact_check|atom


def _keywords(text: str, limit: int = 8) -> list[str]:
    seen: list[str] = []
    for token in _WORD_RE.findall(text):
        low = token.lower()
        if low in _STOPWORDS or len(low) < 3:
            continue
        if token not in seen:
            seen.append(token)
        if len(seen) >= limit:
            break
    return seen


def plan_queries(
    claim_text: str,
    atoms: list[dict] | None = None,
    language: str | None = None,
) -> list[PlannedQuery]:
    """Return a small, deterministic set of query variations.

    Order is stable so runs are reproducible. query_id is derived from the index.
    """
    queries: list[PlannedQuery] = []

    def add(query: str, variation: str, atom_ids: list[str] | None = None) -> None:
        query = query.strip()
        if not query:
            return
        # de-dup on (query, variation)
        for existing in queries:
            if existing.query == query and existing.variation == variation:
                return
        queries.append(
            PlannedQuery(
                query_id=f"q{len(queries) + 1:03d}",
                query=query,
                atom_ids=atom_ids or [],
                variation=variation,
            )
        )

    # 1. Full caption (verbatim) — most faithful query.
    add(claim_text, "caption")

    # 2. Entity/keyword focus.
    kw = _keywords(claim_text)
    if kw:
        add(" ".join(kw), "entity")

    # 3. Fact-check oriented.
    if kw:
        add(" ".join(kw[:5]) + " fakta OR hoaks OR klarifikasi", "fact_check")

    # 4. Per-atom queries when analysis is present.
    for atom in atoms or []:
        statement = (atom.get("statement") or "").strip()
        if not statement:
            continue
        atom_id = atom.get("atom_id")
        add(statement, "atom", [atom_id] if atom_id else [])

    return queries
