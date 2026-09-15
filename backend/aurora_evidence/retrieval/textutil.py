"""Text normalization, tokenization, shingling and SimHash.

Shared by BM25 scoring, near-duplicate detection and the feature reranker so
that all three agree on what a "token" is.

Important boundary: normalization here is for *matching only*. It never touches
stored text. The contract forbids Unicode normalization of captions and quotes,
and ``content.sha256`` is computed over the exact received bytes, so normalized
forms must never be written back into ``content.text`` or ``excerpt``.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Word characters incl. Unicode letters/digits; keeps Indonesian text intact.
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_WS_RE = re.compile(r"\s+", re.UNICODE)

#: Very small Indonesian + English stopword list. Deliberately conservative:
#: aggressive stopword removal destroys negation ("tidak", "bukan") and those
#: words are meaning-bearing for claim verification, so they are NOT included.
STOPWORDS: frozenset[str] = frozenset(
    {
        "yang", "dan", "di", "ke", "dari", "pada", "untuk", "dengan", "ini", "itu",
        "adalah", "akan", "atau", "sebagai", "oleh", "dalam", "juga", "telah",
        "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are",
        "was", "were", "be", "been", "as", "by", "with", "that", "this", "at",
    }
)

#: Negation and quantity cues that must survive every filtering step.
PRESERVED_TOKENS: frozenset[str] = frozenset(
    {"tidak", "bukan", "belum", "tanpa", "jangan", "no", "not", "never", "hoaks", "hoax"}
)

#: Query parameters that identify a campaign, not a document.
_TRACKING_PARAMS = frozenset(
    {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "utm_id", "gclid", "fbclid", "igshid", "mc_cid", "mc_eid", "ref", "referrer",
        "spm", "share", "amp", "at_medium", "at_campaign", "__twitter_impression",
    }
)


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens. Casefold handles non-ASCII pairs better than lower()."""
    return [match.group(0).casefold() for match in _TOKEN_RE.finditer(text)]


def content_tokens(text: str) -> list[str]:
    """Tokens with stopwords dropped, but negation/quantity cues preserved."""
    return [
        token
        for token in tokenize(text)
        if token in PRESERVED_TOKENS or token not in STOPWORDS
    ]


def normalize_for_matching(text: str) -> str:
    """Aggressively normalized form used ONLY for duplicate detection.

    Applies NFKC, casefolding and whitespace collapsing. This is exactly the
    kind of transformation the contract forbids on stored text, which is why it
    lives behind a name that says "for matching".
    """
    folded = unicodedata.normalize("NFKC", text).casefold()
    return _WS_RE.sub(" ", folded).strip()


def normalized_text_hash(text: str) -> str:
    """Stable hash of the normalized form; equal values mean "same document text"."""
    return hashlib.sha256(normalize_for_matching(text).encode("utf-8")).hexdigest()


def shingles(tokens: list[str], size: int = 4) -> list[str]:
    """Overlapping word n-grams. Order-sensitive, so reordered text differs."""
    if size <= 0:
        raise ValueError("shingle size must be positive")
    if len(tokens) < size:
        return [" ".join(tokens)] if tokens else []
    return [" ".join(tokens[i : i + size]) for i in range(len(tokens) - size + 1)]


def simhash(tokens: list[str], *, shingle_size: int = 4, bits: int = 64) -> int:
    """Charikar SimHash over token shingles.

    Near-duplicate news articles differ by a headline or a boilerplate block, so
    a similarity measure that tolerates small edits works better than exact
    hashing. SimHash gives that with a cheap Hamming comparison and no
    third-party dependency.

    Returns 0 for empty input, which callers must treat as "no signal" rather
    than as a hash that happens to collide with other empty documents.
    """
    grams = shingles(tokens, shingle_size)
    if not grams:
        return 0
    vector = [0] * bits
    for gram in grams:
        digest = int.from_bytes(
            hashlib.blake2b(gram.encode("utf-8"), digest_size=(bits // 8)).digest(),
            "big",
        )
        for position in range(bits):
            vector[position] += 1 if (digest >> position) & 1 else -1
    result = 0
    for position in range(bits):
        if vector[position] > 0:
            result |= 1 << position
    return result


def hamming_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 0.0
    union = len(left | right)
    return (len(left & right) / union) if union else 0.0


def canonical_url(url: str | None) -> str | None:
    """Canonicalize a URL for duplicate detection.

    Lowercases scheme/host, drops ``www.``, removes the fragment, strips known
    tracking parameters and sorts the remainder. Two links that differ only by
    campaign parameters are the same document.
    """
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return None
    host = parts.hostname or ""
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    netloc = host
    if parts.port and not (
        (parts.scheme.lower() == "http" and parts.port == 80)
        or (parts.scheme.lower() == "https" and parts.port == 443)
    ):
        netloc = f"{host}:{parts.port}"
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key.lower() not in _TRACKING_PARAMS
        )
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), netloc, path, query, ""))


def registrable_domain(url: str | None) -> str | None:
    """Best-effort registrable domain, used as a syndication signal.

    Without a Public Suffix List this cannot be exact, so multi-label public
    suffixes common in Indonesian media (``.co.id``, ``.or.id``, ``.web.id``)
    are special-cased. The result is a *heuristic grouping key*, never proof of
    independence — the contract is explicit that differing ids do not establish
    independence, so callers must keep the provenance that produced the group.
    """
    if not url:
        return None
    try:
        host = urlsplit(url.strip()).hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    two_part_suffixes = {
        "co.id", "or.id", "web.id", "go.id", "ac.id", "sch.id", "my.id", "biz.id",
        "co.uk", "org.uk", "ac.uk", "com.au", "com.br", "com.sg", "com.my",
    }
    if ".".join(labels[-2:]) in two_part_suffixes and len(labels) >= 3:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def excerpt_from(text: str, query_tokens: list[str], *, max_chars: int = 320) -> str:
    """Pick a real substring of ``text`` around the densest query-term match.

    Returns an exact substring so it can satisfy the contract rule that
    ``excerpt`` be a substring of ``content.text``. It never paraphrases or
    summarizes: a generated sentence would be a fabricated quote.
    """
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    wanted = {token for token in query_tokens if token not in STOPWORDS}
    if not wanted:
        return text[:max_chars]

    best_start, best_hits = 0, -1
    # Slide over sentence-ish anchors to keep the excerpt readable.
    anchors = [0] + [m.end() for m in re.finditer(r"[.!?]\s+", text)]
    for start in anchors:
        window = text[start : start + max_chars]
        if not window:
            continue
        hits = sum(1 for token in tokenize(window) if token in wanted)
        if hits > best_hits:
            best_start, best_hits = start, hits
    return text[best_start : best_start + max_chars]
