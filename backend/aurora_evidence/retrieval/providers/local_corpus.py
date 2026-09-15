"""Local corpus BM25 provider — the real, offline retrieval path.

Loads a JSONL corpus (one document per line) and ranks with BM25. Uses the
``rank_bm25`` library when available; otherwise falls back to a correct, if
slower, pure-Python Okapi BM25 so the local path works with zero heavy deps.

This is labeled a LOCAL CORPUS search (kind="local_corpus"), not a web search.
The demo corpus is clearly synthetic and lives in fixtures/corpus/.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

from aurora_evidence.retrieval.providers.base import Capability, SearchHit

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


class _PurePythonBM25:
    """Okapi BM25 (k1=1.5, b=0.75). Deterministic, dependency-free fallback."""

    def __init__(self, corpus_tokens: list[list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.corpus = corpus_tokens
        self.n = len(corpus_tokens)
        self.doc_len = [len(d) for d in corpus_tokens]
        self.avgdl = (sum(self.doc_len) / self.n) if self.n else 0.0
        self.df: Counter[str] = Counter()
        for doc in corpus_tokens:
            for term in set(doc):
                self.df[term] += 1
        self.idf: dict[str, float] = {}
        for term, df in self.df.items():
            # BM25+ style idf, always positive
            self.idf[term] = math.log(1 + (self.n - df + 0.5) / (df + 0.5))

    def get_scores(self, query_tokens: list[str]) -> list[float]:
        scores = [0.0] * self.n
        q_counts = Counter(query_tokens)
        for i, doc in enumerate(self.corpus):
            if not doc:
                continue
            counts = Counter(doc)
            dl = self.doc_len[i]
            denom_norm = self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
            s = 0.0
            for term in q_counts:
                if term not in counts:
                    continue
                freq = counts[term]
                s += self.idf.get(term, 0.0) * (freq * (self.k1 + 1)) / (freq + denom_norm)
            scores[i] = s
        return scores


def _make_bm25(corpus_tokens: list[list[str]]):
    try:
        from rank_bm25 import BM25Okapi  # type: ignore

        return BM25Okapi(corpus_tokens)
    except Exception:
        return _PurePythonBM25(corpus_tokens)


class LocalCorpusProvider:
    """SearchProvider over a JSONL corpus.

    Each corpus line: {"id","title","text","url"?,"publisher"?,"language"?,
    "published_at"?,"kind"?}. ``kind`` defaults to "local_corpus".
    """

    def __init__(self, corpus_path: Path | str) -> None:
        self.corpus_path = Path(corpus_path)
        self.docs: list[dict] = []
        self._bm25 = None
        self._loaded = False

    def load(self) -> None:
        self.docs = []
        if self.corpus_path.exists():
            for line in self.corpus_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    self.docs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        corpus_tokens = [tokenize(f"{d.get('title','')} {d.get('text','')}") for d in self.docs]
        self._bm25 = _make_bm25(corpus_tokens) if corpus_tokens else None
        self._loaded = True

    def capability(self) -> Capability:
        if not self._loaded:
            self.load()
        if not self.docs:
            return Capability(
                provider="local_corpus",
                capability="local_corpus",
                status="unconfigured",
                message=f"no documents found at {self.corpus_path}",
            )
        return Capability(
            provider="local_corpus",
            capability="local_corpus",
            status="ok",
            message=f"{len(self.docs)} documents",
            languages=["id", "en"],
        )

    def search(self, query: str, *, limit: int, budget_s: float = 0.0) -> list[SearchHit]:
        if not self._loaded:
            self.load()
        if not self.docs or self._bm25 is None:
            return []
        scores = list(self._bm25.get_scores(tokenize(query)))
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        hits: list[SearchHit] = []
        for rank, idx in enumerate(ranked[:limit], start=1):
            if scores[idx] <= 0:
                continue
            d = self.docs[idx]
            text = d.get("text", "")
            hits.append(
                SearchHit(
                    title=d.get("title", ""),
                    url=d.get("url"),
                    snippet=text[:300],
                    full_text=text,
                    published_at=d.get("published_at"),
                    publisher=d.get("publisher"),
                    language=d.get("language"),
                    provider="local_corpus",
                    kind=d.get("kind", "local_corpus"),
                    provider_rank=rank,
                    provider_score=float(scores[idx]),
                    raw={"corpus_id": d.get("id")},
                )
            )
        return hits
