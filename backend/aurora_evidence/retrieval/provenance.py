"""Temporal eligibility, date basis and the auditable credibility indicator.

Three rules from the contract drive this module:

1. ``temporal_eligible`` is tri-state. ``True``/``False`` require a date we
   actually have; an unknown date is ``None`` and must *never* be optimistically
   read as eligible.
2. ``date_basis`` records *which* field the eligibility decision used, so a
   reviewer can tell "published before the cutoff" from "first seen before the
   cutoff". Publication and first-seen dates are not the capture date of a photo.
3. ``credibility_score`` is a metadata indicator, not truth. It is computed from
   observable properties only (is there a URL, a named publisher, a date, how
   complete is the content, what kind of source is it) and every component is
   returned so the UI and Dev 3 can audit the number instead of trusting it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

#: Prior per source kind for the credibility indicator.
#:
#: These are HYPOTHESES about how much scrutiny a source type usually receives,
#: not verdicts about individual outlets, and the contract forbids treating them
#: as a list of always-correct domains. They are configurable and must be tuned
#: on validation data only. A high prior never substitutes for the source
#: actually matching the claim's entity, event, place and time.
DEFAULT_KIND_PRIORS: dict[str, float] = {
    "fact_check": 0.80,
    "official": 0.75,
    "news": 0.55,
    "local_corpus": 0.50,
    "web": 0.35,
    "image_provenance": 0.40,
    "user_supplied": 0.30,
}

#: Content completeness contribution: reading a whole page is worth more than a
#: search-result snippet, and unavailable content is worth nothing.
CONTENT_STATUS_WEIGHT: dict[str, float] = {
    "full": 1.0,
    "excerpt_only": 0.6,
    "snippet_only": 0.35,
    "unavailable": 0.0,
}


def parse_timestamp(value: str | None) -> datetime | None:
    """Parse an RFC 3339 timestamp, returning None when absent or unparseable."""
    if not value or not isinstance(value, str):
        return None
    candidate = value.strip()
    if candidate.endswith(("Z", "z")):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


@dataclass
class TemporalVerdict:
    """Outcome of the ``as_of`` check for one piece of evidence."""

    temporal_eligible: bool | None
    date_basis: str | None
    usage_note: str | None = None


def assess_temporal(
    *,
    published_at: str | None,
    first_seen_at: str | None,
    as_of: str | None,
    strict: bool = True,
) -> TemporalVerdict:
    """Decide whether evidence may inform a decision "as of" a point in time.

    ``strict`` distinguishes the two modes the contract asks us to keep apart:

    ``strict=True`` (temporal evaluation)
        Only evidence with a known date at or before ``as_of`` is eligible.
        Unknown dates are ``None`` — visible, but not usable as eligible
        evidence. This is the mode that forbids future evidence.

    ``strict=False`` ("fact-checks available now")
        ``as_of`` is treated as advisory: later material is still marked
        ineligible for the as-of question, but an unknown date does not block
        the evidence from being surfaced. Eligibility stays ``None`` so the
        uncertainty is never hidden.

    When ``as_of`` is absent there is no cutoff to enforce, so a dated source is
    eligible and an undated one remains ``None``.
    """
    published = parse_timestamp(published_at)
    first_seen = parse_timestamp(first_seen_at)

    # Prefer the publication date; fall back to first-seen, clearly labelled.
    if published is not None:
        effective, basis = published, "source.published_at"
    elif first_seen is not None:
        effective, basis = first_seen, "provenance.first_seen_at"
    else:
        return TemporalVerdict(
            temporal_eligible=None,
            date_basis=None,
            usage_note=(
                "Tanggal sumber tidak diketahui; kelayakan temporal tidak dapat "
                "ditentukan dan tidak diasumsikan layak."
            ),
        )

    cutoff = parse_timestamp(as_of)
    if cutoff is None:
        return TemporalVerdict(
            temporal_eligible=True,
            date_basis=basis,
            usage_note=(
                None
                if basis == "source.published_at"
                else "Kelayakan memakai first_seen_at, bukan tanggal terbit."
            ),
        )

    if effective <= cutoff:
        return TemporalVerdict(
            temporal_eligible=True,
            date_basis=basis,
            usage_note=(
                None
                if basis == "source.published_at"
                else "Kelayakan memakai first_seen_at, bukan tanggal terbit."
            ),
        )

    return TemporalVerdict(
        temporal_eligible=False,
        date_basis=basis,
        usage_note=(
            f"Sumber bertanggal setelah as_of ({as_of}); tidak layak untuk "
            "keputusan pada waktu tersebut, tetapi tetap ditampilkan."
        )
        if strict
        else (
            f"Sumber bertanggal setelah as_of ({as_of}); ditandai tidak layak "
            "untuk pertanyaan as-of."
        ),
    )


@dataclass
class CredibilityAssessment:
    """Auditable credibility indicator: the score plus every component."""

    score: float
    components: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def assess_credibility(
    *,
    kind: str,
    url: str | None,
    publisher: str | None,
    published_at: str | None,
    content_status: str,
    kind_priors: dict[str, float] | None = None,
) -> CredibilityAssessment:
    """Metadata-based credibility indicator in [0,1].

    Explicitly NOT a probability that the source tells the truth. It answers a
    narrower, checkable question: how much verifiable provenance does this item
    carry? Every term is returned in ``components`` so a reviewer can see why.
    """
    priors = kind_priors or DEFAULT_KIND_PRIORS
    components: dict[str, float] = {}
    notes: list[str] = []

    components["kind_prior"] = priors.get(kind, 0.3)
    # Attributable: a resolvable link and a named publisher can be re-checked.
    components["has_url"] = 0.1 if url else 0.0
    components["has_publisher"] = 0.1 if publisher else 0.0
    components["has_date"] = 0.1 if parse_timestamp(published_at) else 0.0
    components["content_completeness"] = 0.2 * CONTENT_STATUS_WEIGHT.get(content_status, 0.0)

    if not url:
        notes.append(
            "Tanpa URL: dokumen lokal/impor tetap sah bila provenance-nya jelas, "
            "tetapi tidak dapat diverifikasi ulang secara publik."
        )
    if not parse_timestamp(published_at):
        notes.append("Tanggal terbit tidak diketahui; bobot kredibilitas dikurangi.")
    if content_status == "snippet_only":
        notes.append(
            "Hanya snippet yang benar-benar diambil; konteks penuh belum dibaca."
        )
    elif content_status == "unavailable":
        notes.append("Konten tidak berhasil diambil; tidak mendukung maupun membantah.")

    raw = sum(components.values())
    # Sum of maxima is 0.8 (prior) + 0.1*3 + 0.2 = 1.3, so normalize to [0,1].
    score = max(0.0, min(1.0, raw / 1.3))
    return CredibilityAssessment(
        score=round(score, 6),
        components={key: round(value, 6) for key, value in components.items()},
        notes=notes,
    )
