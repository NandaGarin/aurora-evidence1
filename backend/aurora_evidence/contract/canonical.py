"""Canonical JSON (RFC 8785 / JCS) and SHA-256 helpers — pure standard library.

Contract requirements (KONTRAK_BERSAMA.md, "Identitas dan kanonisasi"):

- SHA-256 hex, lowercase, 64 chars.
- Canonical JSON follows RFC 8785 / JCS, UTF-8 without BOM.
- Reject NaN/Infinity and duplicate keys.
- No Unicode normalization on caption or quote text.
- ``json.dumps(sort_keys=True)`` must NOT be assumed identical to JCS.

Why this module is implemented by hand instead of delegating to a package:

1. ``json.dumps(sort_keys=True)`` sorts by Unicode code point. JCS sorts object
   keys by UTF-16 code unit. Those orders differ for astral characters (U+10000
   and above encode to surrogates 0xD800..0xDFFF, which sort *before* U+E000
   in UTF-16 but *after* in code point order). Emoji keys therefore hash
   differently. We sort on the UTF-16-BE encoding, which is byte-order
   equivalent to UTF-16 code unit order.
2. JCS serializes numbers with the ECMAScript ``Number::toString`` algorithm.
   Python's ``repr`` gives the same shortest round-trip *digits* but a different
   *layout* (``1e-07`` vs ``1e-7``, ``1e+21`` matches but ``100.0`` vs ``100``).
   ``_ecma_number`` below re-lays-out those digits per ECMA-262 7.1.12.1.

Keeping this dependency-free means the identity/hashing rules that all three
AURORA modules must agree on can be verified on any Python 3.11+ without
network access, which is also what makes them testable in CI.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

# Largest integer exactly representable as an IEEE-754 double.
_MAX_SAFE_INTEGER = 2**53 - 1

# Shortest-escape forms mandated by RFC 8785 section 3.2.2.2.
_ESCAPES = {
    0x08: "\\b",
    0x09: "\\t",
    0x0A: "\\n",
    0x0C: "\\f",
    0x0D: "\\r",
    0x22: '\\"',
    0x5C: "\\\\",
}

_EXP_SPLIT = re.compile(r"[eE]")


class CanonicalizationError(ValueError):
    """Raised when a value cannot be represented as canonical JSON."""


# --------------------------------------------------------------------------- #
# Numbers
# --------------------------------------------------------------------------- #
def _ecma_number(value: float) -> str:
    """Serialize a double exactly as ECMAScript ``Number::toString`` would.

    RFC 8785 defers number formatting to ECMA-262 7.1.12.1. We obtain the
    shortest round-trip decimal digits from ``repr`` (CPython uses David
    Gay/Grisu shortest representation, same digit string as V8) and then apply
    the ECMAScript layout rules to those digits.
    """
    if value != value or value in (float("inf"), float("-inf")):  # NaN/Inf
        raise CanonicalizationError("NaN and Infinity are not valid JSON numbers")
    if value == 0:
        # Covers both +0.0 and -0.0: ECMAScript ToString(-0) is "0".
        return "0"
    if value < 0:
        return "-" + _ecma_number(-value)

    text = repr(float(value))
    if "e" in text or "E" in text:
        mantissa, exponent_text = _EXP_SPLIT.split(text)
        exponent = int(exponent_text)
    else:
        mantissa, exponent = text, 0

    if "." in mantissa:
        int_part, frac_part = mantissa.split(".")
    else:
        int_part, frac_part = mantissa, ""

    raw_digits = int_part + frac_part
    stripped = raw_digits.lstrip("0")
    leading_zeros = len(raw_digits) - len(stripped)

    # ``n`` is the position of the decimal point relative to the digit string:
    # value == 0.<digits> * 10**n
    n = len(int_part) + exponent - leading_zeros
    digits = stripped.rstrip("0")
    if not digits:
        return "0"
    k = len(digits)

    if k <= n <= 21:
        return digits + "0" * (n - k)
    if 0 < n <= 21:
        return digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return "0." + "0" * (-n) + digits

    # Exponential notation.
    exp = n - 1
    sign = "+" if exp >= 0 else "-"
    head = digits if k == 1 else digits[0] + "." + digits[1:]
    return f"{head}e{sign}{abs(exp)}"


def _serialize_number(value: Any) -> str:
    if isinstance(value, bool):  # bool is an int subclass; handled by caller
        raise CanonicalizationError("bool must not reach _serialize_number")
    if isinstance(value, int):
        if abs(value) > _MAX_SAFE_INTEGER:
            raise CanonicalizationError(
                f"integer {value} exceeds the IEEE-754 safe range; JCS numbers are "
                "doubles, so serializing it would silently lose precision"
            )
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalizationError("NaN and Infinity are not valid JSON numbers")
        return _ecma_number(value)
    raise CanonicalizationError(f"unsupported number type: {type(value)!r}")


# --------------------------------------------------------------------------- #
# Strings
# --------------------------------------------------------------------------- #
def _serialize_string(value: str) -> str:
    """Escape per RFC 8785: only ``"``, ``\\`` and C0 controls are escaped."""
    out = ['"']
    for char in value:
        code = ord(char)
        escape = _ESCAPES.get(code)
        if escape is not None:
            out.append(escape)
        elif code < 0x20:
            out.append(f"\\u{code:04x}")
        elif 0xD800 <= code <= 0xDFFF:
            raise CanonicalizationError(
                "lone surrogate is not encodable as UTF-8 and cannot be canonicalized"
            )
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _utf16_sort_key(key: str) -> bytes:
    """UTF-16 code unit order, as required by JCS (not code point order)."""
    return key.encode("utf-16-be", errors="surrogatepass")


# --------------------------------------------------------------------------- #
# Values
# --------------------------------------------------------------------------- #
def _serialize(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return _serialize_string(value)
    if isinstance(value, (int, float)):
        return _serialize_number(value)
    if isinstance(value, dict):
        keys = list(value.keys())
        for key in keys:
            if not isinstance(key, str):
                raise CanonicalizationError(
                    f"object keys must be strings, got {type(key)!r}"
                )
        if len(set(keys)) != len(keys):  # pragma: no cover - dict cannot hold dupes
            raise CanonicalizationError("duplicate object keys")
        parts = [
            f"{_serialize_string(key)}:{_serialize(value[key])}"
            for key in sorted(keys, key=_utf16_sort_key)
        ]
        return "{" + ",".join(parts) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_serialize(item) for item in value) + "]"
    raise CanonicalizationError(f"type {type(value)!r} is not JSON-serializable")


def canonical_json_bytes(obj: Any) -> bytes:
    """Serialize ``obj`` to canonical JSON bytes (RFC 8785 / JCS), UTF-8, no BOM."""
    return _serialize(obj).encode("utf-8")


def canonical_json(obj: Any) -> str:
    """Canonical JSON as a ``str`` (useful for logs and golden vectors)."""
    return _serialize(obj)


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            raise CanonicalizationError(f"duplicate object key in JSON input: {key!r}")
        seen.add(key)
    return dict(pairs)


def loads_strict(text: str | bytes) -> Any:
    """Parse JSON rejecting duplicate keys and NaN/Infinity literals.

    The contract requires both. ``json.loads`` accepts ``NaN``/``Infinity`` by
    default and silently keeps the last of duplicated keys, so neither default
    is safe for bundles that arrive from another module.
    """
    if isinstance(text, bytes):
        if text.startswith(b"\xef\xbb\xbf"):
            raise CanonicalizationError("canonical JSON must be UTF-8 without BOM")
        text = text.decode("utf-8")
    return json.loads(
        text,
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_constant,
    )


def _reject_constant(name: str) -> Any:
    raise CanonicalizationError(f"JSON constant {name!r} is not allowed by the contract")


# --------------------------------------------------------------------------- #
# Hashing
# --------------------------------------------------------------------------- #
def sha256_hex(data: bytes) -> str:
    """Lowercase hex SHA-256 of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def text_sha256(text: str) -> str:
    """SHA-256 of the exact UTF-8 bytes of ``text`` (no Unicode normalization)."""
    return sha256_hex(text.encode("utf-8"))


def canonical_sha256(obj: Any) -> str:
    """SHA-256 over the canonical JSON serialization of ``obj``."""
    return sha256_hex(canonical_json_bytes(obj))


def is_sha256_hex(value: Any) -> bool:
    """True when ``value`` is a lowercase 64-char hex digest."""
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )
