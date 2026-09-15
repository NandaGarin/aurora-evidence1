"""Typed provider interfaces and capability declarations.

Business logic and UI depend only on these interfaces, never on vendor-specific
response fields. Configuration (env/YAML) selects concrete implementations via
the registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# provider_status.status enum from the contract
ProviderStatusValue = str  # "ok"|"disabled"|"unconfigured"|"rate_limited"|"failed"|"unsupported"


@dataclass
class SearchHit:
    """A raw candidate before extraction/normalization."""

    title: str
    url: str | None
    snippet: str
    published_at: str | None = None
    publisher: str | None = None
    language: str | None = None
    provider: str = ""
    kind: str = "web"  # fact_check|news|official|web|image_provenance|local_corpus|user_supplied
    raw: dict = field(default_factory=dict)
    # local corpus can supply full text directly:
    full_text: str | None = None
    provider_rank: int | None = None
    provider_score: float | None = None


@dataclass
class Capability:
    provider: str
    capability: str  # e.g. "web_search", "fact_check", "local_corpus", "image_provenance"
    status: ProviderStatusValue
    message: str | None = None
    languages: list[str] = field(default_factory=list)


@runtime_checkable
class SearchProvider(Protocol):
    """Text search over web/news/official sources or a local corpus."""

    def capability(self) -> Capability: ...

    def search(self, query: str, *, limit: int, budget_s: float) -> list[SearchHit]: ...


@runtime_checkable
class FactCheckProvider(Protocol):
    def capability(self) -> Capability: ...

    def search(self, claim: str, *, language: str | None, as_of: str | None, limit: int) -> list[SearchHit]: ...


@runtime_checkable
class ImageProvenanceProvider(Protocol):
    def capability(self) -> Capability: ...

    def search(self, image_sha256: str, *, query_context: str, limit: int) -> list[SearchHit]: ...
