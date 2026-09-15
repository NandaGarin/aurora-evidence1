"""Normative validator for AURORA contract v1.0.0 — pure standard library.

This module is the single source of truth for the 16 invariants in
KONTRAK_BERSAMA.md. It validates plain ``dict`` structures (as parsed from JSON)
rather than typed objects, because it has to run in three places where the input
is untrusted and possibly malformed:

* ``POST /api/v1/retrieve`` request bodies coming from Dev 1 or Dev 3,
* bundle/ZIP import through the UI,
* our own output, right before we hand a bundle back (self-check).

Design choices
--------------
*Collect, don't fail fast.* Import UX is much better when the user sees every
problem at once, so ``check_*`` returns a list of :class:`Violation` and only
``validate_*`` raises.

*Codes are stable.* ``Violation.code`` values are part of the API error surface
(``details.violations``). ``SCHEMA_VERSION_UNSUPPORTED`` and
``INPUT_ANALYSIS_REQUIRED`` are mandated by the contract and map to HTTP 422.

*Null is not zero.* Every check distinguishes "absent/unknown" (``None``) from
"measured zero". Invariant 3 forbids substituting 0 for unknown, so a missing
key and a null value are treated differently from ``0``/``""``/``[]``.

*No Unicode normalization.* Text is compared and hashed exactly as received
(invariant 7 and the canonicalization rules).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Sequence

from aurora_evidence.contract.canonical import is_sha256_hex, text_sha256
from aurora_evidence.contract.labels import (
    CALIBRATION_STATUSES,
    CONTENT_STATUSES,
    DECISION_CALIBRATION_STATUSES,
    EVIDENCE_KINDS,
    FACT_LABELS,
    FORENSIC_ASSESSMENTS,
    FORENSIC_NOT_ASSESSED_STATUSES,
    FORENSIC_STATUSES,
    FORENSIC_TASKS,
    INFERENCE_KINDS,
    MATCH_TYPES,
    MODALITIES,
    MODES,
    PROVIDER_STATUSES,
    ROLES,
    RUN_STATUSES,
    STANCES,
    SUPPORTED_SCHEMA_VERSIONS,
    TARGET_KINDS,
    VISUAL_LABELS,
)

__all__ = [
    "ContractViolation",
    "Violation",
    "check_bundle",
    "check_evidence",
    "check_forensic_signal",
    "check_retrieval",
    "validate_bundle",
    "validate_evidence",
    "validate_forensic_signal",
    "validate_retrieval",
]

#: Tolerance for probability sums (invariant 3).
PROBABILITY_TOLERANCE = 1e-6

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_ATOM_ID_RE = re.compile(r"^a\d{6}$")
_EVIDENCE_ID_RE = re.compile(r"^ev_[0-9a-f]{32}_\d{6}$")
_SIGNAL_ID_RE = re.compile(r"^fs_[0-9a-f]{32}_\d{6}$")
_REGION_ID_RE = re.compile(r"^rg_[0-9a-f]{32}_\d{6}$")
_ATOM_SET_ID_RE = re.compile(r"^aset_[0-9a-f]{64}$")
_ASSET_ID_RE = re.compile(r"^asset_[0-9a-f]{64}$")
# BCP 47: pragmatic subset (primary subtag + optional subtags). "und" is allowed.
_BCP47_RE = re.compile(r"^[A-Za-z]{2,8}(-[0-9A-Za-z]{1,8})*$")


@dataclass(frozen=True)
class Violation:
    """A single contract breach, addressed by a JSON-pointer-ish path."""

    code: str
    path: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        location = self.path or "<root>"
        return f"[{self.code}] {location}: {self.message}"


class ContractViolation(Exception):
    """Raised by ``validate_*`` when at least one invariant is broken."""

    def __init__(self, violations: Sequence[Violation]) -> None:
        self.violations = list(violations)
        summary = "; ".join(str(v) for v in self.violations[:5])
        if len(self.violations) > 5:
            summary += f"; (+{len(self.violations) - 5} more)"
        super().__init__(summary or "contract violation")

    @property
    def primary_code(self) -> str:
        """Code to surface in the API error envelope.

        Contract-level rejections outrank field-level ones: a caller sending an
        unsupported schema version needs to see that, not the 40 downstream
        shape errors it causes.
        """
        for preferred in ("SCHEMA_VERSION_UNSUPPORTED", "INPUT_ANALYSIS_REQUIRED"):
            if any(v.code == preferred for v in self.violations):
                return preferred
        return "CONTRACT_VIOLATION"

    def as_details(self) -> dict[str, Any]:
        """Non-sensitive detail payload for ``{error:{...,details}}``."""
        return {
            "violations": [
                {"code": v.code, "path": v.path, "message": v.message}
                for v in self.violations
            ]
        }


# --------------------------------------------------------------------------- #
# Collector
# --------------------------------------------------------------------------- #
@dataclass
class _Collector:
    violations: list[Violation] = field(default_factory=list)

    def add(self, code: str, path: str, message: str) -> None:
        self.violations.append(Violation(code=code, path=path, message=message))

    # -- primitive predicates ------------------------------------------------
    def obj(self, value: Any, path: str) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            self.add("TYPE_MISMATCH", path, f"expected object, got {_kind(value)}")
            return None
        return value

    def arr(self, value: Any, path: str) -> list[Any] | None:
        if not isinstance(value, list):
            self.add("TYPE_MISMATCH", path, f"expected array, got {_kind(value)}")
            return None
        return value

    def string(self, value: Any, path: str, *, allow_empty: bool = True) -> str | None:
        if not isinstance(value, str):
            self.add("TYPE_MISMATCH", path, f"expected string, got {_kind(value)}")
            return None
        if not allow_empty and not value:
            self.add("EMPTY_STRING", path, "must be a non-empty string")
            return None
        return value

    def opt_string(self, value: Any, path: str, *, allow_empty: bool = True) -> str | None:
        if value is None:
            return None
        return self.string(value, path, allow_empty=allow_empty)

    def boolean(self, value: Any, path: str) -> bool | None:
        if not isinstance(value, bool):
            self.add("TYPE_MISMATCH", path, f"expected boolean, got {_kind(value)}")
            return None
        return value

    def finite_number(self, value: Any, path: str) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            self.add("TYPE_MISMATCH", path, f"expected number, got {_kind(value)}")
            return None
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            self.add("NON_FINITE_NUMBER", path, "numbers must be finite (invariant 13)")
            return None
        return number

    def score(self, value: Any, path: str) -> float | None:
        """Score = finite number within [0,1]. Not automatically a probability."""
        number = self.finite_number(value, path)
        if number is None:
            return None
        if not 0.0 <= number <= 1.0:
            self.add("SCORE_OUT_OF_RANGE", path, f"score must be within [0,1], got {number}")
            return None
        return number

    def opt_score(self, value: Any, path: str) -> float | None:
        if value is None:
            return None
        return self.score(value, path)

    def nonneg_int(self, value: Any, path: str) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int):
            self.add("TYPE_MISMATCH", path, f"expected integer, got {_kind(value)}")
            return None
        if value < 0:
            self.add("NEGATIVE_VALUE", path, f"must be >= 0, got {value}")
            return None
        return value

    def positive_int(self, value: Any, path: str) -> int | None:
        number = self.nonneg_int(value, path)
        if number is None:
            return None
        if number < 1:
            self.add("NEGATIVE_VALUE", path, f"must be >= 1, got {number}")
            return None
        return number

    def enum(self, value: Any, path: str, allowed: Iterable[str], code: str = "ENUM_INVALID") -> str | None:
        allowed_set = set(allowed)
        if not isinstance(value, str) or value not in allowed_set:
            self.add(
                code,
                path,
                f"expected one of {sorted(allowed_set)}, got {value!r}",
            )
            return None
        return value

    def timestamp(self, value: Any, path: str) -> str | None:
        """RFC 3339 with an explicit timezone offset."""
        text = self.string(value, path, allow_empty=False)
        if text is None:
            return None
        if not _parse_rfc3339(text):
            self.add(
                "TIMESTAMP_INVALID",
                path,
                "expected RFC 3339 timestamp with timezone offset (e.g. 2026-09-12T00:00:00Z)",
            )
            return None
        return text

    def opt_timestamp(self, value: Any, path: str) -> str | None:
        if value is None:
            return None
        return self.timestamp(value, path)

    def require_keys(self, obj: dict[str, Any], path: str, required: Iterable[str]) -> None:
        for key in required:
            if key not in obj:
                self.add("FIELD_MISSING", _join(path, key), "required field is absent")

    def reject_unknown(self, obj: dict[str, Any], path: str, allowed: Iterable[str]) -> None:
        allowed_set = set(allowed)
        for key in obj:
            if key not in allowed_set:
                self.add(
                    "UNKNOWN_FIELD",
                    _join(path, key),
                    "unknown field; internal additions belong in extensions",
                )

    def str_map(self, value: Any, path: str) -> dict[str, str] | None:
        obj = self.obj(value, path)
        if obj is None:
            return None
        for key, item in obj.items():
            self.string(item, _join(path, key))
        return obj

    def number_map(self, value: Any, path: str) -> dict[str, float] | None:
        obj = self.obj(value, path)
        if obj is None:
            return None
        for key, item in obj.items():
            self.finite_number(item, _join(path, key))
        return obj

    def str_list(self, value: Any, path: str) -> list[str] | None:
        arr = self.arr(value, path)
        if arr is None:
            return None
        for index, item in enumerate(arr):
            self.string(item, f"{path}[{index}]")
        return arr

    def probabilities(self, value: Any, path: str, labels: Sequence[str]) -> None:
        """Invariant 3: all labels present, finite, >= 0, summing to 1 ± 1e-6."""
        if value is None:
            return
        obj = self.obj(value, path)
        if obj is None:
            return
        missing = [label for label in labels if label not in obj]
        if missing:
            self.add(
                "PROBABILITIES_INCOMPLETE",
                path,
                f"missing label(s) {missing}; probabilities must cover all of {list(labels)}",
            )
        extra = [key for key in obj if key not in set(labels)]
        if extra:
            self.add("PROBABILITIES_INCOMPLETE", path, f"unexpected label(s) {extra}")
        total = 0.0
        for label in labels:
            if label not in obj:
                continue
            number = self.finite_number(obj[label], _join(path, label))
            if number is None:
                return
            if number < 0:
                self.add("NEGATIVE_VALUE", _join(path, label), "probability must be >= 0")
                return
            total += number
        if missing or extra:
            return
        if abs(total - 1.0) > PROBABILITY_TOLERANCE:
            self.add(
                "PROBABILITIES_NOT_NORMALIZED",
                path,
                f"probabilities must sum to 1 within {PROBABILITY_TOLERANCE}, got {total!r}",
            )


def _kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _parse_rfc3339(text: str) -> datetime | None:
    candidate = text
    if candidate.endswith(("Z", "z")):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed



# --------------------------------------------------------------------------- #
# Shared building blocks
# --------------------------------------------------------------------------- #
_WARNING_KEYS = ("code", "message", "component")
_RUN_KEYS = ("run_id", "mode", "started_at", "finished_at", "status", "versions", "warnings")
_MEDIA_KEYS = ("asset_id", "sha256", "media_type", "width", "height", "uri")


def _check_uuid(c: _Collector, value: Any, path: str) -> str | None:
    text = c.string(value, path, allow_empty=False)
    if text is None:
        return None
    if not _UUID_RE.match(text):
        c.add(
            "ID_FORMAT_INVALID",
            path,
            "expected lowercase UUID with hyphens",
        )
        return None
    return text


def _check_warnings(c: _Collector, value: Any, path: str) -> None:
    arr = c.arr(value, path)
    if arr is None:
        return
    for index, item in enumerate(arr):
        item_path = f"{path}[{index}]"
        obj = c.obj(item, item_path)
        if obj is None:
            continue
        c.require_keys(obj, item_path, _WARNING_KEYS)
        c.reject_unknown(obj, item_path, _WARNING_KEYS)
        for key in _WARNING_KEYS:
            if key in obj:
                c.string(obj[key], _join(item_path, key), allow_empty=False)


def _check_run(c: _Collector, value: Any, path: str, *, bundle_mode: str | None) -> dict[str, Any] | None:
    obj = c.obj(value, path)
    if obj is None:
        return None
    c.require_keys(obj, path, _RUN_KEYS)
    c.reject_unknown(obj, path, _RUN_KEYS)
    if "run_id" in obj:
        _check_uuid(c, obj["run_id"], _join(path, "run_id"))
    mode = c.enum(obj.get("mode"), _join(path, "mode"), MODES) if "mode" in obj else None
    # Invariant 12: demo and live must never mix silently.
    if mode is not None and bundle_mode is not None and mode != bundle_mode:
        c.add(
            "MODE_MISMATCH",
            _join(path, "mode"),
            f"run.mode={mode!r} must equal bundle.mode={bundle_mode!r} (invariant 12)",
        )
    if "started_at" in obj:
        c.timestamp(obj["started_at"], _join(path, "started_at"))
    if "finished_at" in obj:
        c.timestamp(obj["finished_at"], _join(path, "finished_at"))
    if "status" in obj:
        c.enum(obj["status"], _join(path, "status"), RUN_STATUSES)
    if "versions" in obj:
        c.str_map(obj["versions"], _join(path, "versions"))
    if "warnings" in obj:
        _check_warnings(c, obj["warnings"], _join(path, "warnings"))
    return obj


def _check_media_ref(c: _Collector, value: Any, path: str) -> dict[str, Any] | None:
    obj = c.obj(value, path)
    if obj is None:
        return None
    c.require_keys(obj, path, _MEDIA_KEYS)
    c.reject_unknown(obj, path, _MEDIA_KEYS)
    digest = None
    if "sha256" in obj:
        digest = c.string(obj["sha256"], _join(path, "sha256"), allow_empty=False)
        if digest is not None and not is_sha256_hex(digest):
            c.add("HASH_FORMAT_INVALID", _join(path, "sha256"), "expected lowercase 64-char hex sha256")
            digest = None
    if "asset_id" in obj:
        asset_id = c.string(obj["asset_id"], _join(path, "asset_id"), allow_empty=False)
        if asset_id is not None:
            if not _ASSET_ID_RE.match(asset_id):
                c.add("ID_FORMAT_INVALID", _join(path, "asset_id"), "expected 'asset_' + sha256 hex")
            elif digest is not None and asset_id != f"asset_{digest}":
                # Identity is content-addressed; transport URI must not affect it.
                c.add(
                    "ASSET_ID_MISMATCH",
                    _join(path, "asset_id"),
                    "asset_id must be 'asset_' + sha256 of the original uploaded bytes",
                )
    if "media_type" in obj:
        c.string(obj["media_type"], _join(path, "media_type"), allow_empty=False)
    if "width" in obj:
        c.positive_int(obj["width"], _join(path, "width"))
    if "height" in obj:
        c.positive_int(obj["height"], _join(path, "height"))
    if "uri" in obj:
        c.string(obj["uri"], _join(path, "uri"), allow_empty=False)
    return obj


def _check_bbox(c: _Collector, value: Any, path: str) -> None:
    """Invariant 4: normalized coordinates, x_min < x_max and y_min < y_max."""
    arr = c.arr(value, path)
    if arr is None:
        return
    if len(arr) != 4:
        c.add("BBOX_INVALID", path, f"expected 4 numbers [x_min,y_min,x_max,y_max], got {len(arr)}")
        return
    numbers = [c.finite_number(item, f"{path}[{index}]") for index, item in enumerate(arr)]
    if any(number is None for number in numbers):
        return
    x_min, y_min, x_max, y_max = numbers  # type: ignore[misc]
    if not (0.0 <= x_min < x_max <= 1.0):
        c.add("BBOX_INVALID", path, f"require 0 <= x_min < x_max <= 1, got {x_min}..{x_max}")
    if not (0.0 <= y_min < y_max <= 1.0):
        c.add("BBOX_INVALID", path, f"require 0 <= y_min < y_max <= 1, got {y_min}..{y_max}")


# --------------------------------------------------------------------------- #
# Analysis (owned by Dev 1; Dev 2 validates what it receives)
# --------------------------------------------------------------------------- #
_ATOM_KEYS = (
    "atom_id",
    "statement",
    "role",
    "subject",
    "predicate",
    "object",
    "qualifiers",
    "spans",
    "depends_on",
    "check_worthiness",
    "parser_confidence",
)
_QUALIFIER_KEYS = ("negated", "quantity", "time", "location")


def _check_atom(c: _Collector, value: Any, path: str, *, claim_length: int | None) -> str | None:
    obj = c.obj(value, path)
    if obj is None:
        return None
    c.require_keys(obj, path, _ATOM_KEYS)
    c.reject_unknown(obj, path, _ATOM_KEYS)

    atom_id = None
    if "atom_id" in obj:
        atom_id = c.string(obj["atom_id"], _join(path, "atom_id"), allow_empty=False)
        if atom_id is not None and not _ATOM_ID_RE.match(atom_id):
            c.add("ID_FORMAT_INVALID", _join(path, "atom_id"), "expected a000001-style id")
    if "statement" in obj:
        c.string(obj["statement"], _join(path, "statement"), allow_empty=False)
    if "role" in obj:
        c.enum(obj["role"], _join(path, "role"), ROLES)
    if "predicate" in obj:
        c.string(obj["predicate"], _join(path, "predicate"), allow_empty=False)
    for nullable in ("subject", "object"):
        if nullable in obj:
            c.opt_string(obj[nullable], _join(path, nullable))
    if "check_worthiness" in obj:
        c.score(obj["check_worthiness"], _join(path, "check_worthiness"))
    if "parser_confidence" in obj:
        c.opt_score(obj["parser_confidence"], _join(path, "parser_confidence"))

    qualifiers = c.obj(obj.get("qualifiers"), _join(path, "qualifiers")) if "qualifiers" in obj else None
    if qualifiers is not None:
        qpath = _join(path, "qualifiers")
        c.require_keys(qualifiers, qpath, _QUALIFIER_KEYS)
        c.reject_unknown(qualifiers, qpath, _QUALIFIER_KEYS)
        if "negated" in qualifiers:
            c.boolean(qualifiers["negated"], _join(qpath, "negated"))
        if "quantity" in qualifiers and qualifiers["quantity"] is not None:
            c.finite_number(qualifiers["quantity"], _join(qpath, "quantity"))
        for nullable in ("time", "location"):
            if nullable in qualifiers:
                c.opt_string(qualifiers[nullable], _join(qpath, nullable))

    # Invariant 1: spans are Unicode code point indices, end exclusive.
    if "spans" in obj:
        spans = c.arr(obj["spans"], _join(path, "spans"))
        if spans is not None:
            for index, span in enumerate(spans):
                span_path = f"{_join(path, 'spans')}[{index}]"
                span_obj = c.obj(span, span_path)
                if span_obj is None:
                    continue
                c.require_keys(span_obj, span_path, ("start", "end"))
                c.reject_unknown(span_obj, span_path, ("start", "end"))
                start = c.nonneg_int(span_obj.get("start"), _join(span_path, "start"))
                end = c.nonneg_int(span_obj.get("end"), _join(span_path, "end"))
                if start is None or end is None:
                    continue
                if end <= start:
                    c.add("SPAN_INVALID", span_path, f"end must be exclusive and > start ({start}..{end})")
                elif claim_length is not None and end > claim_length:
                    c.add(
                        "SPAN_OUT_OF_RANGE",
                        span_path,
                        f"span end {end} exceeds claim_text length {claim_length} in code points",
                    )

    if "depends_on" in obj:
        c.str_list(obj["depends_on"], _join(path, "depends_on"))
    return atom_id


def _check_dependencies(c: _Collector, atoms: list[Any], path: str) -> None:
    """Invariant 1: depends_on must not be dangling or cyclic."""
    graph: dict[str, list[str]] = {}
    for atom in atoms:
        if not isinstance(atom, dict):
            continue
        atom_id = atom.get("atom_id")
        depends = atom.get("depends_on")
        if isinstance(atom_id, str) and isinstance(depends, list):
            graph[atom_id] = [d for d in depends if isinstance(d, str)]

    known = set(graph)
    for atom_id, depends in graph.items():
        for target in depends:
            if target not in known:
                c.add(
                    "DEPENDENCY_DANGLING",
                    path,
                    f"atom {atom_id} depends on unknown atom {target}",
                )
            elif target == atom_id:
                c.add("DEPENDENCY_CYCLIC", path, f"atom {atom_id} depends on itself")

    # Iterative DFS with colouring so a cycle is reported once.
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {atom_id: WHITE for atom_id in graph}
    reported = False
    for root in graph:
        if colour[root] != WHITE:
            continue
        stack: list[tuple[str, int]] = [(root, 0)]
        colour[root] = GREY
        while stack:
            node, cursor = stack.pop()
            children = graph.get(node, [])
            if cursor < len(children):
                stack.append((node, cursor + 1))
                child = children[cursor]
                if child not in colour:
                    continue
                if colour[child] == GREY:
                    if not reported:
                        c.add("DEPENDENCY_CYCLIC", path, f"dependency cycle reaching atom {child}")
                        reported = True
                elif colour[child] == WHITE:
                    colour[child] = GREY
                    stack.append((child, 0))
            else:
                colour[node] = BLACK


_REGION_KEYS = ("region_id", "asset_id", "bbox", "score", "description")
_VISUAL_KEYS = (
    "atom_id",
    "visual_status",
    "probabilities",
    "unmatched_mass",
    "observability_score",
    "supporting_regions",
    "contradicting_regions",
    "counter_evidence",
    "rationale",
    "inference_kind",
)
_OCR_KEYS = ("text", "bbox", "confidence", "language")
_ANALYSIS_KEYS = ("run", "atom_set_id", "atomic_claims", "visual_assessments", "ocr")


def _check_region(c: _Collector, value: Any, path: str) -> None:
    obj = c.obj(value, path)
    if obj is None:
        return
    c.require_keys(obj, path, _REGION_KEYS)
    c.reject_unknown(obj, path, _REGION_KEYS)
    if "region_id" in obj:
        region = c.string(obj["region_id"], _join(path, "region_id"), allow_empty=False)
        if region is not None and not _REGION_ID_RE.match(region):
            c.add("ID_FORMAT_INVALID", _join(path, "region_id"), "expected rg_<uuidhex>_<000001>")
    if "asset_id" in obj:
        asset = c.string(obj["asset_id"], _join(path, "asset_id"), allow_empty=False)
        if asset is not None and not _ASSET_ID_RE.match(asset):
            c.add("ID_FORMAT_INVALID", _join(path, "asset_id"), "expected 'asset_' + sha256 hex")
    if "bbox" in obj:
        _check_bbox(c, obj["bbox"], _join(path, "bbox"))
    if "score" in obj:
        c.opt_score(obj["score"], _join(path, "score"))
    if "description" in obj:
        c.string(obj["description"], _join(path, "description"))


def _check_analysis(
    c: _Collector,
    value: Any,
    path: str,
    *,
    bundle_mode: str | None,
    claim_length: int | None,
) -> tuple[str | None, set[str]]:
    """Validate an imported Analysis. Returns (atom_set_id, atom_ids)."""
    obj = c.obj(value, path)
    if obj is None:
        return None, set()
    c.require_keys(obj, path, _ANALYSIS_KEYS)
    c.reject_unknown(obj, path, _ANALYSIS_KEYS)

    if "run" in obj:
        _check_run(c, obj["run"], _join(path, "run"), bundle_mode=bundle_mode)

    atom_set_id = None
    if "atom_set_id" in obj:
        atom_set_id = c.string(obj["atom_set_id"], _join(path, "atom_set_id"), allow_empty=False)
        if atom_set_id is not None and not _ATOM_SET_ID_RE.match(atom_set_id):
            c.add("ID_FORMAT_INVALID", _join(path, "atom_set_id"), "expected 'aset_' + sha256 hex")

    atom_ids: set[str] = set()
    atoms: list[Any] = []
    if "atomic_claims" in obj:
        atoms_arr = c.arr(obj["atomic_claims"], _join(path, "atomic_claims"))
        if atoms_arr is not None:
            atoms = atoms_arr
            seen: set[str] = set()
            for index, atom in enumerate(atoms_arr):
                atom_id = _check_atom(
                    c,
                    atom,
                    f"{_join(path, 'atomic_claims')}[{index}]",
                    claim_length=claim_length,
                )
                if atom_id is None:
                    continue
                if atom_id in seen:
                    c.add(
                        "ATOM_ID_DUPLICATE",
                        f"{_join(path, 'atomic_claims')}[{index}].atom_id",
                        f"atom_id {atom_id} appears more than once in the atom set",
                    )
                seen.add(atom_id)
            atom_ids = seen
            _check_dependencies(c, atoms_arr, _join(path, "atomic_claims"))

    if "visual_assessments" in obj:
        assessments = c.arr(obj["visual_assessments"], _join(path, "visual_assessments"))
        if assessments is not None:
            for index, assessment in enumerate(assessments):
                apath = f"{_join(path, 'visual_assessments')}[{index}]"
                aobj = c.obj(assessment, apath)
                if aobj is None:
                    continue
                c.require_keys(aobj, apath, _VISUAL_KEYS)
                c.reject_unknown(aobj, apath, _VISUAL_KEYS)
                if "atom_id" in aobj:
                    ref = c.string(aobj["atom_id"], _join(apath, "atom_id"), allow_empty=False)
                    if ref is not None and atom_ids and ref not in atom_ids:
                        c.add(
                            "ATOM_REFERENCE_UNKNOWN",
                            _join(apath, "atom_id"),
                            f"visual assessment references unknown atom {ref}",
                        )
                if "visual_status" in aobj:
                    c.enum(aobj["visual_status"], _join(apath, "visual_status"), VISUAL_LABELS)
                if "probabilities" in aobj:
                    c.probabilities(aobj["probabilities"], _join(apath, "probabilities"), VISUAL_LABELS)
                for score_key in ("unmatched_mass", "observability_score"):
                    if score_key in aobj:
                        c.opt_score(aobj[score_key], _join(apath, score_key))
                for regions_key in ("supporting_regions", "contradicting_regions"):
                    if regions_key in aobj:
                        regions = c.arr(aobj[regions_key], _join(apath, regions_key))
                        if regions is not None:
                            for r_index, region in enumerate(regions):
                                _check_region(c, region, f"{_join(apath, regions_key)}[{r_index}]")
                if "counter_evidence" in aobj:
                    c.opt_string(aobj["counter_evidence"], _join(apath, "counter_evidence"))
                if "rationale" in aobj:
                    c.string(aobj["rationale"], _join(apath, "rationale"))
                if "inference_kind" in aobj:
                    c.enum(aobj["inference_kind"], _join(apath, "inference_kind"), INFERENCE_KINDS)
                # Invariant 4: a visual contradiction must cite a region and counter_evidence.
                if aobj.get("visual_status") == "Contradicted":
                    regions = aobj.get("contradicting_regions")
                    counter = aobj.get("counter_evidence")
                    if not isinstance(regions, list) or not regions:
                        c.add(
                            "CONTRADICTION_UNSUPPORTED",
                            _join(apath, "contradicting_regions"),
                            "Contradicted requires at least one contradicting region (invariant 4)",
                        )
                    if not isinstance(counter, str) or not counter.strip():
                        c.add(
                            "CONTRADICTION_UNSUPPORTED",
                            _join(apath, "counter_evidence"),
                            "Contradicted requires matching counter_evidence (invariant 4)",
                        )

    if "ocr" in obj:
        ocr_arr = c.arr(obj["ocr"], _join(path, "ocr"))
        if ocr_arr is not None:
            for index, entry in enumerate(ocr_arr):
                opath = f"{_join(path, 'ocr')}[{index}]"
                oobj = c.obj(entry, opath)
                if oobj is None:
                    continue
                c.require_keys(oobj, opath, _OCR_KEYS)
                c.reject_unknown(oobj, opath, _OCR_KEYS)
                if "text" in oobj:
                    c.string(oobj["text"], _join(opath, "text"))
                if "bbox" in oobj:
                    _check_bbox(c, oobj["bbox"], _join(opath, "bbox"))
                if "confidence" in oobj:
                    c.opt_score(oobj["confidence"], _join(opath, "confidence"))
                if "language" in oobj:
                    c.opt_string(oobj["language"], _join(opath, "language"))

    return atom_set_id, atom_ids



# --------------------------------------------------------------------------- #
# Evidence — owned by Dev 2
# --------------------------------------------------------------------------- #
_EVIDENCE_KEYS = (
    "evidence_id",
    "atom_ids",
    "source",
    "content",
    "provenance",
    "relevance_score",
    "credibility_score",
    "rerank_score",
    "score_breakdown",
)
_SOURCE_KEYS = (
    "provider",
    "kind",
    "url",
    "title",
    "publisher",
    "language",
    "published_at",
    "retrieved_at",
)
_CONTENT_KEYS = ("text", "excerpt", "sha256", "status")
_PROVENANCE_KEYS = (
    "original_url",
    "archive_url",
    "first_seen_at",
    "captured_at",
    "date_basis",
    "discovery_method",
    "duplicate_cluster_id",
    "independence_group_id",
    "temporal_eligible",
    "usage_note",
    "image_matches",
)
_IMAGE_MATCH_KEYS = ("asset_id", "matched_url", "match_type", "score")


def check_evidence(
    evidence: Any,
    *,
    path: str = "evidence",
    known_atom_ids: set[str] | None = None,
    expect_empty_atom_ids: bool = False,
) -> list[Violation]:
    """Validate one Evidence object. See ``validate_evidence`` to raise instead."""
    c = _Collector()
    _check_evidence(c, evidence, path, known_atom_ids=known_atom_ids, expect_empty_atom_ids=expect_empty_atom_ids)
    return c.violations


def _check_evidence(
    c: _Collector,
    value: Any,
    path: str,
    *,
    known_atom_ids: set[str] | None,
    expect_empty_atom_ids: bool,
) -> str | None:
    obj = c.obj(value, path)
    if obj is None:
        return None
    c.require_keys(obj, path, _EVIDENCE_KEYS)
    c.reject_unknown(obj, path, _EVIDENCE_KEYS)

    evidence_id = None
    if "evidence_id" in obj:
        evidence_id = c.string(obj["evidence_id"], _join(path, "evidence_id"), allow_empty=False)
        if evidence_id is not None and not _EVIDENCE_ID_RE.match(evidence_id):
            c.add(
                "ID_FORMAT_INVALID",
                _join(path, "evidence_id"),
                "expected ev_<32 hex run uuid>_<six digits>",
            )

    # Invariant 2 / 8: atom references must belong to the same atom set.
    if "atom_ids" in obj:
        atom_ids = c.str_list(obj["atom_ids"], _join(path, "atom_ids"))
        if atom_ids is not None:
            if expect_empty_atom_ids and atom_ids:
                c.add(
                    "ATOM_IDS_NOT_EMPTY",
                    _join(path, "atom_ids"),
                    "retrieval without analysis must use atom_ids=[] (invariant 2)",
                )
            if known_atom_ids is not None:
                for atom_id in atom_ids:
                    if atom_id not in known_atom_ids:
                        c.add(
                            "ATOM_REFERENCE_UNKNOWN",
                            _join(path, "atom_ids"),
                            f"evidence references atom {atom_id} that is absent from the atom set",
                        )
            if len(set(atom_ids)) != len(atom_ids):
                c.add("ATOM_IDS_DUPLICATE", _join(path, "atom_ids"), "atom_ids must not repeat")

    # ---- source ----
    source = c.obj(obj.get("source"), _join(path, "source")) if "source" in obj else None
    if source is not None:
        spath = _join(path, "source")
        c.require_keys(source, spath, _SOURCE_KEYS)
        c.reject_unknown(source, spath, _SOURCE_KEYS)
        if "provider" in source:
            c.string(source["provider"], _join(spath, "provider"), allow_empty=False)
        if "kind" in source:
            c.enum(source["kind"], _join(spath, "kind"), EVIDENCE_KINDS)
        # url may legitimately be null for local/imported documents (invariant 7).
        if "url" in source:
            c.opt_string(source["url"], _join(spath, "url"), allow_empty=False)
        if "title" in source:
            c.string(source["title"], _join(spath, "title"))
        for nullable in ("publisher", "language"):
            if nullable in source:
                c.opt_string(source[nullable], _join(spath, nullable), allow_empty=False)
        if isinstance(source.get("language"), str) and not _BCP47_RE.match(source["language"]):
            c.add(
                "LANGUAGE_TAG_INVALID",
                _join(spath, "language"),
                "expected a BCP 47 tag such as id, en or und",
            )
        if "published_at" in source:
            c.opt_timestamp(source["published_at"], _join(spath, "published_at"))
        if "retrieved_at" in source:
            c.timestamp(source["retrieved_at"], _join(spath, "retrieved_at"))

    # ---- content ----
    content = c.obj(obj.get("content"), _join(path, "content")) if "content" in obj else None
    if content is not None:
        cpath = _join(path, "content")
        c.require_keys(content, cpath, _CONTENT_KEYS)
        c.reject_unknown(content, cpath, _CONTENT_KEYS)
        text = c.string(content.get("text"), _join(cpath, "text")) if "text" in content else None
        excerpt = c.string(content.get("excerpt"), _join(cpath, "excerpt")) if "excerpt" in content else None
        status = c.enum(content.get("status"), _join(cpath, "status"), CONTENT_STATUSES) if "status" in content else None

        # Invariant 7: excerpt must be an exact substring of content.text.
        if text is not None and excerpt is not None:
            if text == "":
                if excerpt != "":
                    c.add(
                        "EXCERPT_NOT_SUBSTRING",
                        _join(cpath, "excerpt"),
                        "empty content requires an empty excerpt (invariant 7)",
                    )
            elif excerpt and excerpt not in text:
                c.add(
                    "EXCERPT_NOT_SUBSTRING",
                    _join(cpath, "excerpt"),
                    "excerpt must be an exact substring of content.text (invariant 7); "
                    "paraphrases and generated summaries are not quotes",
                )

        # Invariant 7: sha256 is over the exact UTF-8 bytes of content.text.
        if "sha256" in content:
            digest = c.string(content["sha256"], _join(cpath, "sha256"), allow_empty=False)
            if digest is not None:
                if not is_sha256_hex(digest):
                    c.add("HASH_FORMAT_INVALID", _join(cpath, "sha256"), "expected lowercase 64-char hex")
                elif text is not None and digest != text_sha256(text):
                    c.add(
                        "CONTENT_HASH_MISMATCH",
                        _join(cpath, "sha256"),
                        "sha256 must equal SHA-256 of the exact UTF-8 bytes of content.text",
                    )

        # "unavailable" means we obtained nothing; carrying text would contradict it.
        if status == "unavailable" and text:
            c.add(
                "CONTENT_STATUS_INCONSISTENT",
                _join(cpath, "status"),
                "status='unavailable' must carry empty text; unavailable content supports nothing",
            )

    # ---- provenance ----
    provenance = c.obj(obj.get("provenance"), _join(path, "provenance")) if "provenance" in obj else None
    if provenance is not None:
        ppath = _join(path, "provenance")
        c.require_keys(provenance, ppath, _PROVENANCE_KEYS)
        c.reject_unknown(provenance, ppath, _PROVENANCE_KEYS)
        for nullable_url in ("original_url", "archive_url"):
            if nullable_url in provenance:
                c.opt_string(provenance[nullable_url], _join(ppath, nullable_url), allow_empty=False)
        # Unknown times stay null; they are never guessed from other fields.
        for nullable_time in ("first_seen_at", "captured_at"):
            if nullable_time in provenance:
                c.opt_timestamp(provenance[nullable_time], _join(ppath, nullable_time))
        if "date_basis" in provenance:
            c.opt_string(provenance["date_basis"], _join(ppath, "date_basis"), allow_empty=False)
        if "discovery_method" in provenance:
            c.string(provenance["discovery_method"], _join(ppath, "discovery_method"), allow_empty=False)
        # Invariant 8: grouping ids are opaque but must be present and non-empty.
        for group_key in ("duplicate_cluster_id", "independence_group_id"):
            if group_key in provenance:
                c.string(provenance[group_key], _join(ppath, group_key), allow_empty=False)
        if "temporal_eligible" in provenance and provenance["temporal_eligible"] is not None:
            c.boolean(provenance["temporal_eligible"], _join(ppath, "temporal_eligible"))
        if "usage_note" in provenance:
            c.opt_string(provenance["usage_note"], _join(ppath, "usage_note"))
        if "image_matches" in provenance:
            matches = c.arr(provenance["image_matches"], _join(ppath, "image_matches"))
            if matches is not None:
                for index, match in enumerate(matches):
                    mpath = f"{_join(ppath, 'image_matches')}[{index}]"
                    mobj = c.obj(match, mpath)
                    if mobj is None:
                        continue
                    c.require_keys(mobj, mpath, _IMAGE_MATCH_KEYS)
                    c.reject_unknown(mobj, mpath, _IMAGE_MATCH_KEYS)
                    if "asset_id" in mobj:
                        asset = c.string(mobj["asset_id"], _join(mpath, "asset_id"), allow_empty=False)
                        if asset is not None and not _ASSET_ID_RE.match(asset):
                            c.add("ID_FORMAT_INVALID", _join(mpath, "asset_id"), "expected 'asset_' + sha256 hex")
                    if "matched_url" in mobj:
                        c.opt_string(mobj["matched_url"], _join(mpath, "matched_url"), allow_empty=False)
                    if "match_type" in mobj:
                        c.enum(mobj["match_type"], _join(mpath, "match_type"), MATCH_TYPES)
                    if "score" in mobj:
                        c.opt_score(mobj["score"], _join(mpath, "score"))

    # ---- scores ----
    # relevance/credibility are bounded scores; rerank may be an unbounded logit.
    for score_key in ("relevance_score", "credibility_score"):
        if score_key in obj:
            c.opt_score(obj[score_key], _join(path, score_key))
    if "rerank_score" in obj and obj["rerank_score"] is not None:
        c.finite_number(obj["rerank_score"], _join(path, "rerank_score"))
    if "score_breakdown" in obj:
        c.number_map(obj["score_breakdown"], _join(path, "score_breakdown"))

    return evidence_id


# --------------------------------------------------------------------------- #
# ForensicSignal — owned by Dev 2
# --------------------------------------------------------------------------- #
_SIGNAL_KEYS = (
    "signal_id",
    "target",
    "modality",
    "provider",
    "model_version",
    "task",
    "status",
    "raw_score",
    "raw_scale",
    "ai_generated_score",
    "raw_label",
    "assessment",
    "calibration_status",
    "applicable_language",
    "limitations",
    "analyzed_at",
    "error_code",
)
_TARGET_KEYS = ("kind", "asset_id", "text_sha256")
_SCALE_KEYS = ("min", "max", "higher_means_ai")


def check_forensic_signal(signal: Any, *, path: str = "forensic_signal") -> list[Violation]:
    c = _Collector()
    _check_forensic_signal(c, signal, path, known_asset_ids=None)
    return c.violations


def _check_forensic_signal(
    c: _Collector,
    value: Any,
    path: str,
    *,
    known_asset_ids: set[str] | None,
) -> str | None:
    obj = c.obj(value, path)
    if obj is None:
        return None
    c.require_keys(obj, path, _SIGNAL_KEYS)
    c.reject_unknown(obj, path, _SIGNAL_KEYS)

    signal_id = None
    if "signal_id" in obj:
        signal_id = c.string(obj["signal_id"], _join(path, "signal_id"), allow_empty=False)
        if signal_id is not None and not _SIGNAL_ID_RE.match(signal_id):
            c.add("ID_FORMAT_INVALID", _join(path, "signal_id"), "expected fs_<32 hex run uuid>_<six digits>")

    modality = c.enum(obj.get("modality"), _join(path, "modality"), MODALITIES) if "modality" in obj else None
    status = c.enum(obj.get("status"), _join(path, "status"), FORENSIC_STATUSES) if "status" in obj else None
    assessment = (
        c.enum(obj.get("assessment"), _join(path, "assessment"), FORENSIC_ASSESSMENTS)
        if "assessment" in obj
        else None
    )

    if "provider" in obj:
        c.string(obj["provider"], _join(path, "provider"), allow_empty=False)
    if "model_version" in obj:
        c.opt_string(obj["model_version"], _join(path, "model_version"), allow_empty=False)
    if "task" in obj:
        c.enum(obj["task"], _join(path, "task"), FORENSIC_TASKS)
    if "calibration_status" in obj:
        c.enum(obj["calibration_status"], _join(path, "calibration_status"), CALIBRATION_STATUSES)
    if "applicable_language" in obj:
        c.opt_string(obj["applicable_language"], _join(path, "applicable_language"), allow_empty=False)
    if "limitations" in obj:
        c.str_list(obj["limitations"], _join(path, "limitations"))
    if "analyzed_at" in obj:
        c.timestamp(obj["analyzed_at"], _join(path, "analyzed_at"))
    if "error_code" in obj:
        c.opt_string(obj["error_code"], _join(path, "error_code"), allow_empty=False)
    if "raw_label" in obj:
        c.opt_string(obj["raw_label"], _join(path, "raw_label"))

    # ---- target: invariants 11 and 15 ----
    target = c.obj(obj.get("target"), _join(path, "target")) if "target" in obj else None
    target_kind = None
    if target is not None:
        tpath = _join(path, "target")
        c.require_keys(target, tpath, _TARGET_KEYS)
        c.reject_unknown(target, tpath, _TARGET_KEYS)
        target_kind = c.enum(target.get("kind"), _join(tpath, "kind"), TARGET_KINDS) if "kind" in target else None
        asset_id = target.get("asset_id")
        text_hash = target.get("text_sha256")

        if target_kind == "image":
            if modality is not None and modality != "image":
                c.add(
                    "FORENSIC_TARGET_MISMATCH",
                    _join(path, "modality"),
                    "target.kind='image' requires modality='image' (invariant 15)",
                )
            if not isinstance(asset_id, str) or not _ASSET_ID_RE.match(asset_id):
                c.add(
                    "FORENSIC_TARGET_MISMATCH",
                    _join(tpath, "asset_id"),
                    "image target requires a valid asset_id (invariant 15)",
                )
            elif known_asset_ids is not None and asset_id not in known_asset_ids:
                c.add(
                    "FORENSIC_TARGET_MISMATCH",
                    _join(tpath, "asset_id"),
                    f"image target references asset {asset_id} that is not present in this bundle",
                )
            if text_hash is not None:
                c.add(
                    "FORENSIC_TARGET_MISMATCH",
                    _join(tpath, "text_sha256"),
                    "image target requires text_sha256=null (invariant 15)",
                )
        elif target_kind in {"claim_text", "ocr_text"}:
            if modality is not None and modality != "text":
                c.add(
                    "FORENSIC_TARGET_MISMATCH",
                    _join(path, "modality"),
                    f"target.kind={target_kind!r} requires modality='text' (invariant 15)",
                )
            if asset_id is not None:
                c.add(
                    "FORENSIC_TARGET_MISMATCH",
                    _join(tpath, "asset_id"),
                    "text target requires asset_id=null (invariant 15)",
                )
            if not is_sha256_hex(text_hash):
                c.add(
                    "FORENSIC_TARGET_MISMATCH",
                    _join(tpath, "text_sha256"),
                    "text target requires the exact UTF-8 sha256 of the analyzed text (invariant 15)",
                )

    # ---- raw score and scale: invariant 15, no silent clamping ----
    raw_score = None
    if "raw_score" in obj and obj["raw_score"] is not None:
        raw_score = c.finite_number(obj["raw_score"], _join(path, "raw_score"))

    scale = None
    if "raw_scale" in obj and obj["raw_scale"] is not None:
        scale = c.obj(obj["raw_scale"], _join(path, "raw_scale"))
        if scale is not None:
            scpath = _join(path, "raw_scale")
            c.require_keys(scale, scpath, _SCALE_KEYS)
            c.reject_unknown(scale, scpath, _SCALE_KEYS)
            minimum = c.finite_number(scale.get("min"), _join(scpath, "min")) if "min" in scale else None
            maximum = c.finite_number(scale.get("max"), _join(scpath, "max")) if "max" in scale else None
            if "higher_means_ai" in scale:
                c.boolean(scale["higher_means_ai"], _join(scpath, "higher_means_ai"))
            if minimum is not None and maximum is not None:
                if not minimum < maximum:
                    c.add("RAW_SCALE_INVALID", scpath, f"require min < max, got {minimum} >= {maximum}")
                elif raw_score is not None and not (minimum <= raw_score <= maximum):
                    c.add(
                        "RAW_SCORE_OUT_OF_SCALE",
                        _join(path, "raw_score"),
                        f"raw_score {raw_score} lies outside the declared scale "
                        f"[{minimum},{maximum}]; this must surface as a warning/error, not a silent clamp",
                    )

    ai_score = None
    if "ai_generated_score" in obj and obj["ai_generated_score"] is not None:
        ai_score = c.opt_score(obj["ai_generated_score"], _join(path, "ai_generated_score"))
        # Invariant 11: only normalize when the vendor's direction/scale is known.
        if scale is None:
            c.add(
                "AI_SCORE_WITHOUT_SCALE",
                _join(path, "ai_generated_score"),
                "ai_generated_score may only be filled when raw_scale (direction and range) "
                "is known (invariant 11)",
            )

    # ---- status <-> assessment coupling: invariants 11 and 15 ----
    if status is not None and assessment is not None:
        if status in FORENSIC_NOT_ASSESSED_STATUSES:
            if assessment != "not_assessed":
                c.add(
                    "FORENSIC_STATUS_ASSESSMENT_MISMATCH",
                    _join(path, "assessment"),
                    f"status={status!r} requires assessment='not_assessed' (invariant 15)",
                )
            if ai_score is not None:
                c.add(
                    "FORENSIC_STATUS_ASSESSMENT_MISMATCH",
                    _join(path, "ai_generated_score"),
                    f"status={status!r} requires ai_generated_score=null (invariant 15)",
                )
        elif status == "inconclusive" and assessment != "uncertain":
            c.add(
                "FORENSIC_STATUS_ASSESSMENT_MISMATCH",
                _join(path, "assessment"),
                "status='inconclusive' requires assessment='uncertain' (invariant 15)",
            )
        # Invariant 11: a non-ok status must never accuse content of being AI.
        if status != "ok" and assessment == "likely_ai_generated":
            c.add(
                "FORENSIC_ACCUSATION_WITHOUT_OK",
                _join(path, "assessment"),
                f"status={status!r} must not yield 'likely_ai_generated' (invariant 11)",
            )
    return signal_id


# --------------------------------------------------------------------------- #
# Retrieval — owned by Dev 2
# --------------------------------------------------------------------------- #
_RETRIEVAL_KEYS = (
    "run",
    "atom_set_id",
    "evidence_list",
    "forensic_signals",
    "query_log",
    "provider_status",
)
_QUERY_LOG_KEYS = ("query_id", "atom_ids", "provider", "query", "duration_ms", "result_count")
_PROVIDER_STATUS_KEYS = ("provider", "capability", "status", "message")


def check_retrieval(
    retrieval: Any,
    *,
    path: str = "retrieval",
    bundle_mode: str | None = None,
    analysis_atom_set_id: str | None = None,
    known_atom_ids: set[str] | None = None,
    has_analysis: bool = False,
    known_asset_ids: set[str] | None = None,
) -> list[Violation]:
    c = _Collector()
    _check_retrieval(
        c,
        retrieval,
        path,
        bundle_mode=bundle_mode,
        analysis_atom_set_id=analysis_atom_set_id,
        known_atom_ids=known_atom_ids,
        has_analysis=has_analysis,
        known_asset_ids=known_asset_ids,
    )
    return c.violations


def _check_retrieval(
    c: _Collector,
    value: Any,
    path: str,
    *,
    bundle_mode: str | None,
    analysis_atom_set_id: str | None,
    known_atom_ids: set[str] | None,
    has_analysis: bool,
    known_asset_ids: set[str] | None,
) -> None:
    obj = c.obj(value, path)
    if obj is None:
        return
    c.require_keys(obj, path, _RETRIEVAL_KEYS)
    c.reject_unknown(obj, path, _RETRIEVAL_KEYS)

    if "run" in obj:
        _check_run(c, obj["run"], _join(path, "run"), bundle_mode=bundle_mode)

    # Invariant 2: without analysis, atom_set_id is null and every atom_ids is [].
    expect_empty_atom_ids = not has_analysis
    if "atom_set_id" in obj:
        atom_set_id = obj["atom_set_id"]
        if has_analysis:
            text = c.string(atom_set_id, _join(path, "atom_set_id"), allow_empty=False)
            if text is not None:
                if not _ATOM_SET_ID_RE.match(text):
                    c.add("ID_FORMAT_INVALID", _join(path, "atom_set_id"), "expected 'aset_' + sha256 hex")
                elif analysis_atom_set_id is not None and text != analysis_atom_set_id:
                    c.add(
                        "ATOM_SET_MISMATCH",
                        _join(path, "atom_set_id"),
                        "retrieval.atom_set_id must equal analysis.atom_set_id (invariant 2); "
                        "reusing evidence across atom sets requires explicit remapping",
                    )
        elif atom_set_id is not None:
            c.add(
                "ATOM_SET_MISMATCH",
                _join(path, "atom_set_id"),
                "retrieval without analysis must use atom_set_id=null (invariant 2)",
            )

    if "evidence_list" in obj:
        evidence_list = c.arr(obj["evidence_list"], _join(path, "evidence_list"))
        if evidence_list is not None:
            seen: set[str] = set()
            for index, evidence in enumerate(evidence_list):
                evidence_id = _check_evidence(
                    c,
                    evidence,
                    f"{_join(path, 'evidence_list')}[{index}]",
                    known_atom_ids=known_atom_ids,
                    expect_empty_atom_ids=expect_empty_atom_ids,
                )
                if evidence_id is None:
                    continue
                if evidence_id in seen:
                    c.add(
                        "EVIDENCE_ID_DUPLICATE",
                        f"{_join(path, 'evidence_list')}[{index}].evidence_id",
                        f"evidence_id {evidence_id} appears more than once",
                    )
                seen.add(evidence_id)

    if "forensic_signals" in obj:
        signals = c.arr(obj["forensic_signals"], _join(path, "forensic_signals"))
        if signals is not None:
            seen_signals: set[str] = set()
            for index, signal in enumerate(signals):
                signal_id = _check_forensic_signal(
                    c,
                    signal,
                    f"{_join(path, 'forensic_signals')}[{index}]",
                    known_asset_ids=known_asset_ids,
                )
                if signal_id is None:
                    continue
                if signal_id in seen_signals:
                    c.add(
                        "SIGNAL_ID_DUPLICATE",
                        f"{_join(path, 'forensic_signals')}[{index}].signal_id",
                        f"signal_id {signal_id} appears more than once",
                    )
                seen_signals.add(signal_id)

    if "query_log" in obj:
        entries = c.arr(obj["query_log"], _join(path, "query_log"))
        if entries is not None:
            for index, entry in enumerate(entries):
                epath = f"{_join(path, 'query_log')}[{index}]"
                eobj = c.obj(entry, epath)
                if eobj is None:
                    continue
                c.require_keys(eobj, epath, _QUERY_LOG_KEYS)
                c.reject_unknown(eobj, epath, _QUERY_LOG_KEYS)
                if "query_id" in eobj:
                    c.string(eobj["query_id"], _join(epath, "query_id"), allow_empty=False)
                if "provider" in eobj:
                    c.string(eobj["provider"], _join(epath, "provider"), allow_empty=False)
                if "query" in eobj:
                    c.string(eobj["query"], _join(epath, "query"), allow_empty=False)
                # Invariant 13: durations and counts are non-negative.
                if "duration_ms" in eobj:
                    c.nonneg_int(eobj["duration_ms"], _join(epath, "duration_ms"))
                if "result_count" in eobj:
                    c.nonneg_int(eobj["result_count"], _join(epath, "result_count"))
                if "atom_ids" in eobj:
                    atom_ids = c.str_list(eobj["atom_ids"], _join(epath, "atom_ids"))
                    if atom_ids is not None:
                        if expect_empty_atom_ids and atom_ids:
                            c.add(
                                "ATOM_IDS_NOT_EMPTY",
                                _join(epath, "atom_ids"),
                                "query_log without analysis must use atom_ids=[] (invariant 2)",
                            )
                        if known_atom_ids is not None:
                            for atom_id in atom_ids:
                                if atom_id not in known_atom_ids:
                                    c.add(
                                        "ATOM_REFERENCE_UNKNOWN",
                                        _join(epath, "atom_ids"),
                                        f"query_log references unknown atom {atom_id}",
                                    )

    if "provider_status" in obj:
        statuses = c.arr(obj["provider_status"], _join(path, "provider_status"))
        if statuses is not None:
            for index, entry in enumerate(statuses):
                spath = f"{_join(path, 'provider_status')}[{index}]"
                sobj = c.obj(entry, spath)
                if sobj is None:
                    continue
                c.require_keys(sobj, spath, _PROVIDER_STATUS_KEYS)
                c.reject_unknown(sobj, spath, _PROVIDER_STATUS_KEYS)
                if "provider" in sobj:
                    c.string(sobj["provider"], _join(spath, "provider"), allow_empty=False)
                if "capability" in sobj:
                    c.string(sobj["capability"], _join(spath, "capability"), allow_empty=False)
                if "status" in sobj:
                    c.enum(sobj["status"], _join(spath, "status"), PROVIDER_STATUSES)
                if "message" in sobj:
                    c.opt_string(sobj["message"], _join(spath, "message"))



# --------------------------------------------------------------------------- #
# Decision — owned by Dev 3; Dev 2 must not corrupt it, and validates on import
# --------------------------------------------------------------------------- #
_CALIBRATION_KEYS = (
    "status",
    "calibration_id",
    "method",
    "alpha",
    "sample_count",
    "group",
    "fallback_used",
    "validity_notes",
)
_EVIDENCE_LINK_KEYS = ("evidence_id", "atom_id", "stance", "quote", "rationale")
_ATOMIC_DECISION_KEYS = (
    "atom_id",
    "base_label",
    "status",
    "probabilities",
    "confidence_set",
    "abstention_flag",
    "abstention_reasons",
    "evidence_links",
    "visual_atom_ids",
    "explanation",
    "calibration",
)
_REPORT_KEYS = (
    "summary",
    "key_findings",
    "unresolved_questions",
    "evidence_ids",
    "forensic_signal_ids",
    "limitations",
    "suggested_next_steps",
    "misinformation_category",
)
_HUMAN_REVIEW_KEYS = ("reviewer", "reviewed_at", "verdict", "reason")
_DECISION_KEYS = (
    "run",
    "atom_set_id",
    "atomic_verdicts",
    "base_label",
    "final_verdict",
    "probabilities",
    "confidence_set",
    "abstention_flag",
    "abstention_reasons",
    "calibration",
    "decision_report",
    "human_review",
)


def _check_calibration(c: _Collector, value: Any, path: str) -> None:
    obj = c.obj(value, path)
    if obj is None:
        return
    c.require_keys(obj, path, _CALIBRATION_KEYS)
    c.reject_unknown(obj, path, _CALIBRATION_KEYS)
    status = c.enum(obj.get("status"), _join(path, "status"), DECISION_CALIBRATION_STATUSES) if "status" in obj else None
    if "calibration_id" in obj:
        c.opt_string(obj["calibration_id"], _join(path, "calibration_id"), allow_empty=False)
    if "method" in obj:
        c.opt_string(obj["method"], _join(path, "method"), allow_empty=False)
    alpha = None
    if "alpha" in obj and obj["alpha"] is not None:
        alpha = c.finite_number(obj["alpha"], _join(path, "alpha"))
        if alpha is not None and not 0.0 < alpha < 1.0:
            c.add("ALPHA_INVALID", _join(path, "alpha"), f"require 0 < alpha < 1, got {alpha}")
    sample_count = c.nonneg_int(obj.get("sample_count"), _join(path, "sample_count")) if "sample_count" in obj else None
    for nullable in ("group", "fallback_used"):
        if nullable in obj:
            c.opt_string(obj[nullable], _join(path, nullable), allow_empty=False)
    if "validity_notes" in obj:
        c.str_list(obj["validity_notes"], _join(path, "validity_notes"))

    # Invariant 16: uncalibrated must not carry calibration artefacts.
    if status == "uncalibrated":
        if obj.get("calibration_id") is not None:
            c.add("CALIBRATION_INCONSISTENT", _join(path, "calibration_id"), "uncalibrated requires calibration_id=null")
        if obj.get("alpha") is not None:
            c.add("CALIBRATION_INCONSISTENT", _join(path, "alpha"), "uncalibrated requires alpha=null")
        if sample_count not in (None, 0):
            c.add("CALIBRATION_INCONSISTENT", _join(path, "sample_count"), "uncalibrated requires sample_count=0")
    elif status in {"calibrated", "demo_only"}:
        if obj.get("calibration_id") is None:
            c.add("CALIBRATION_INCONSISTENT", _join(path, "calibration_id"), f"status={status!r} requires a calibration_id")
        if obj.get("method") is None:
            c.add("CALIBRATION_INCONSISTENT", _join(path, "method"), f"status={status!r} requires a method")
        if obj.get("alpha") is None:
            c.add("CALIBRATION_INCONSISTENT", _join(path, "alpha"), f"status={status!r} requires a valid alpha")


def _check_confidence_set(c: _Collector, value: Any, path: str) -> None:
    """Invariant 9/14: null != []; no duplicate labels; factual labels only."""
    if value is None:
        return
    arr = c.arr(value, path)
    if arr is None:
        return
    seen: set[str] = set()
    for index, item in enumerate(arr):
        label = c.enum(item, f"{path}[{index}]", FACT_LABELS)
        if label is None:
            continue
        if label in seen:
            c.add("CONFIDENCE_SET_DUPLICATE", path, f"confidence_set must not repeat {label!r} (invariant 14)")
        seen.add(label)


def _check_decision(
    c: _Collector,
    value: Any,
    path: str,
    *,
    bundle_mode: str | None,
    analysis_atom_set_id: str | None,
    known_atom_ids: set[str] | None,
    known_evidence_ids: set[str] | None,
    evidence_texts: dict[str, str] | None,
) -> None:
    obj = c.obj(value, path)
    if obj is None:
        return
    c.require_keys(obj, path, _DECISION_KEYS)
    c.reject_unknown(obj, path, _DECISION_KEYS)

    if "run" in obj:
        _check_run(c, obj["run"], _join(path, "run"), bundle_mode=bundle_mode)
    if "atom_set_id" in obj:
        text = c.string(obj["atom_set_id"], _join(path, "atom_set_id"), allow_empty=False)
        if text is not None and analysis_atom_set_id is not None and text != analysis_atom_set_id:
            c.add(
                "ATOM_SET_MISMATCH",
                _join(path, "atom_set_id"),
                "decision.atom_set_id must equal analysis.atom_set_id",
            )
    if "base_label" in obj:
        c.enum(obj["base_label"], _join(path, "base_label"), FACT_LABELS)
    final_verdict = (
        c.enum(obj.get("final_verdict"), _join(path, "final_verdict"), FACT_LABELS)
        if "final_verdict" in obj
        else None
    )
    if "probabilities" in obj:
        c.probabilities(obj["probabilities"], _join(path, "probabilities"), FACT_LABELS)
    if "confidence_set" in obj:
        _check_confidence_set(c, obj["confidence_set"], _join(path, "confidence_set"))
    abstention_flag = c.boolean(obj.get("abstention_flag"), _join(path, "abstention_flag")) if "abstention_flag" in obj else None
    if "abstention_reasons" in obj:
        c.str_list(obj["abstention_reasons"], _join(path, "abstention_reasons"))
    if "calibration" in obj:
        _check_calibration(c, obj["calibration"], _join(path, "calibration"))

    # Invariant 10: abstention forces the operational verdict to InsufficientEvidence.
    if abstention_flag is True and final_verdict is not None and final_verdict != "InsufficientEvidence":
        c.add(
            "ABSTENTION_VERDICT_MISMATCH",
            _join(path, "final_verdict"),
            "abstention_flag=true requires final_verdict='InsufficientEvidence' (invariant 10)",
        )

    if "atomic_verdicts" in obj:
        verdicts = c.arr(obj["atomic_verdicts"], _join(path, "atomic_verdicts"))
        if verdicts is not None:
            seen_atoms: set[str] = set()
            for index, verdict in enumerate(verdicts):
                vpath = f"{_join(path, 'atomic_verdicts')}[{index}]"
                vobj = c.obj(verdict, vpath)
                if vobj is None:
                    continue
                c.require_keys(vobj, vpath, _ATOMIC_DECISION_KEYS)
                c.reject_unknown(vobj, vpath, _ATOMIC_DECISION_KEYS)
                atom_id = c.string(vobj.get("atom_id"), _join(vpath, "atom_id"), allow_empty=False) if "atom_id" in vobj else None
                if atom_id is not None:
                    # Invariant 14: exactly one decision per atom.
                    if atom_id in seen_atoms:
                        c.add("ATOMIC_DECISION_DUPLICATE", _join(vpath, "atom_id"), f"more than one decision for atom {atom_id}")
                    seen_atoms.add(atom_id)
                    if known_atom_ids is not None and atom_id not in known_atom_ids:
                        c.add("ATOM_REFERENCE_UNKNOWN", _join(vpath, "atom_id"), f"unknown atom {atom_id}")
                if "base_label" in vobj:
                    c.enum(vobj["base_label"], _join(vpath, "base_label"), FACT_LABELS)
                v_status = c.enum(vobj.get("status"), _join(vpath, "status"), FACT_LABELS) if "status" in vobj else None
                if "probabilities" in vobj:
                    c.probabilities(vobj["probabilities"], _join(vpath, "probabilities"), FACT_LABELS)
                if "confidence_set" in vobj:
                    _check_confidence_set(c, vobj["confidence_set"], _join(vpath, "confidence_set"))
                v_abstain = c.boolean(vobj.get("abstention_flag"), _join(vpath, "abstention_flag")) if "abstention_flag" in vobj else None
                if v_abstain is True and v_status is not None and v_status != "InsufficientEvidence":
                    c.add(
                        "ABSTENTION_VERDICT_MISMATCH",
                        _join(vpath, "status"),
                        "abstention_flag=true requires status='InsufficientEvidence' (invariant 10)",
                    )
                if "abstention_reasons" in vobj:
                    c.str_list(vobj["abstention_reasons"], _join(vpath, "abstention_reasons"))
                if "visual_atom_ids" in vobj:
                    c.str_list(vobj["visual_atom_ids"], _join(vpath, "visual_atom_ids"))
                if "explanation" in vobj:
                    c.string(vobj["explanation"], _join(vpath, "explanation"))
                if "calibration" in vobj:
                    _check_calibration(c, vobj["calibration"], _join(vpath, "calibration"))
                if "evidence_links" in vobj:
                    links = c.arr(vobj["evidence_links"], _join(vpath, "evidence_links"))
                    if links is not None:
                        for l_index, link in enumerate(links):
                            lpath = f"{_join(vpath, 'evidence_links')}[{l_index}]"
                            lobj = c.obj(link, lpath)
                            if lobj is None:
                                continue
                            c.require_keys(lobj, lpath, _EVIDENCE_LINK_KEYS)
                            c.reject_unknown(lobj, lpath, _EVIDENCE_LINK_KEYS)
                            link_evidence = lobj.get("evidence_id")
                            if isinstance(link_evidence, str) and known_evidence_ids is not None:
                                if link_evidence not in known_evidence_ids:
                                    c.add(
                                        "EVIDENCE_REFERENCE_UNKNOWN",
                                        _join(lpath, "evidence_id"),
                                        f"evidence_link references unknown evidence {link_evidence}",
                                    )
                            # Invariant 14: link.atom_id must match its containing decision.
                            if isinstance(lobj.get("atom_id"), str) and atom_id is not None:
                                if lobj["atom_id"] != atom_id:
                                    c.add(
                                        "EVIDENCE_LINK_ATOM_MISMATCH",
                                        _join(lpath, "atom_id"),
                                        f"evidence_link.atom_id must equal the containing decision's atom {atom_id}",
                                    )
                            if "stance" in lobj:
                                c.enum(lobj["stance"], _join(lpath, "stance"), STANCES)
                            if "rationale" in lobj:
                                c.string(lobj["rationale"], _join(lpath, "rationale"))
                            # Invariant 14: a non-null quote must be an exact substring.
                            quote = lobj.get("quote")
                            if quote is not None:
                                quote_text = c.string(quote, _join(lpath, "quote"))
                                if (
                                    quote_text
                                    and evidence_texts is not None
                                    and isinstance(link_evidence, str)
                                    and link_evidence in evidence_texts
                                    and quote_text not in evidence_texts[link_evidence]
                                ):
                                    c.add(
                                        "QUOTE_NOT_SUBSTRING",
                                        _join(lpath, "quote"),
                                        "quote must be an exact substring of the referenced "
                                        "evidence content.text (invariant 14)",
                                    )

    if "decision_report" in obj:
        report = c.obj(obj["decision_report"], _join(path, "decision_report"))
        if report is not None:
            rpath = _join(path, "decision_report")
            c.require_keys(report, rpath, _REPORT_KEYS)
            c.reject_unknown(report, rpath, _REPORT_KEYS)
            if "summary" in report:
                c.string(report["summary"], _join(rpath, "summary"))
            for list_key in (
                "key_findings",
                "unresolved_questions",
                "evidence_ids",
                "forensic_signal_ids",
                "limitations",
                "suggested_next_steps",
            ):
                if list_key in report:
                    c.str_list(report[list_key], _join(rpath, list_key))
            if "misinformation_category" in report:
                c.opt_string(report["misinformation_category"], _join(rpath, "misinformation_category"), allow_empty=False)
            if known_evidence_ids is not None and isinstance(report.get("evidence_ids"), list):
                for evidence_ref in report["evidence_ids"]:
                    if isinstance(evidence_ref, str) and evidence_ref not in known_evidence_ids:
                        c.add(
                            "EVIDENCE_REFERENCE_UNKNOWN",
                            _join(rpath, "evidence_ids"),
                            f"report references unknown evidence {evidence_ref}",
                        )

    if "human_review" in obj and obj["human_review"] is not None:
        review = c.obj(obj["human_review"], _join(path, "human_review"))
        if review is not None:
            hpath = _join(path, "human_review")
            c.require_keys(review, hpath, _HUMAN_REVIEW_KEYS)
            c.reject_unknown(review, hpath, _HUMAN_REVIEW_KEYS)
            if "reviewer" in review:
                c.string(review["reviewer"], _join(hpath, "reviewer"), allow_empty=False)
            if "reviewed_at" in review:
                c.timestamp(review["reviewed_at"], _join(hpath, "reviewed_at"))
            if "verdict" in review:
                c.enum(review["verdict"], _join(hpath, "verdict"), FACT_LABELS)
            if "reason" in review:
                c.string(review["reason"], _join(hpath, "reason"), allow_empty=False)


# --------------------------------------------------------------------------- #
# Bundle
# --------------------------------------------------------------------------- #
_BUNDLE_KEYS = (
    "schema_version",
    "case_id",
    "claim_revision",
    "mode",
    "created_at",
    "input",
    "analysis",
    "retrieval",
    "decision",
    "warnings",
    "extensions",
)
_INPUT_KEYS = ("claim_text", "language", "image", "as_of")


def check_bundle(
    bundle: Any,
    *,
    require_analysis: bool = False,
    verify_atom_set_id: bool = True,
) -> list[Violation]:
    """Validate a whole AuroraBundle and return every violation found.

    Parameters
    ----------
    require_analysis:
        Dev 3 sets this (``INPUT_ANALYSIS_REQUIRED``). Dev 2 leaves it False
        because invariant 2 explicitly permits ``analysis=null``.
    verify_atom_set_id:
        Recompute ``analysis.atom_set_id`` from the canonical atom list and
        compare. This catches a stale or hand-edited atom set, which is the
        cheapest way to detect that upstream results no longer match the input.
    """
    c = _Collector()
    obj = c.obj(bundle, "")
    if obj is None:
        return c.violations

    # Invariant 12: unsupported versions must be rejected explicitly, and there
    # is no point reporting shape errors against the wrong schema.
    version = obj.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        c.add(
            "SCHEMA_VERSION_UNSUPPORTED",
            "schema_version",
            f"unsupported schema_version {version!r}; this build accepts "
            f"{sorted(SUPPORTED_SCHEMA_VERSIONS)}",
        )
        return c.violations

    c.require_keys(obj, "", _BUNDLE_KEYS)
    c.reject_unknown(obj, "", _BUNDLE_KEYS)

    _check_uuid(c, obj.get("case_id"), "case_id")
    if "claim_revision" in obj:
        c.positive_int(obj["claim_revision"], "claim_revision")
    bundle_mode = c.enum(obj.get("mode"), "mode", MODES) if "mode" in obj else None
    if "created_at" in obj:
        c.timestamp(obj["created_at"], "created_at")
    if "warnings" in obj:
        _check_warnings(c, obj["warnings"], "warnings")
    if "extensions" in obj:
        c.obj(obj["extensions"], "extensions")

    # ---- input ----
    claim_text: str | None = None
    image_sha256: str | None = None
    known_asset_ids: set[str] = set()
    input_obj = c.obj(obj.get("input"), "input") if "input" in obj else None
    if input_obj is not None:
        c.require_keys(input_obj, "input", _INPUT_KEYS)
        c.reject_unknown(input_obj, "input", _INPUT_KEYS)
        if "claim_text" in input_obj:
            claim_text = c.string(input_obj["claim_text"], "input.claim_text")
            # Invariant 13: non-empty after trim, but stored untrimmed.
            if claim_text is not None and not claim_text.strip():
                c.add(
                    "CLAIM_TEXT_EMPTY",
                    "input.claim_text",
                    "claim_text must be non-empty after trimming (invariant 13)",
                )
        if "language" in input_obj:
            language = c.string(input_obj["language"], "input.language", allow_empty=False)
            if language is not None and not _BCP47_RE.match(language):
                c.add(
                    "LANGUAGE_TAG_INVALID",
                    "input.language",
                    "expected a BCP 47 tag such as id, en or und",
                )
        # Invariant 13: module 2 explicitly allows image=null.
        if input_obj.get("image") is not None:
            media = _check_media_ref(c, input_obj["image"], "input.image")
            if media is not None:
                if isinstance(media.get("sha256"), str):
                    image_sha256 = media["sha256"]
                if isinstance(media.get("asset_id"), str):
                    known_asset_ids.add(media["asset_id"])
        if "as_of" in input_obj:
            c.opt_timestamp(input_obj["as_of"], "input.as_of")

    claim_length = len(claim_text) if isinstance(claim_text, str) else None

    # ---- analysis ----
    analysis_atom_set_id: str | None = None
    known_atom_ids: set[str] | None = None
    has_analysis = obj.get("analysis") is not None
    if has_analysis:
        analysis_atom_set_id, atom_ids = _check_analysis(
            c,
            obj["analysis"],
            "analysis",
            bundle_mode=bundle_mode,
            claim_length=claim_length,
        )
        known_atom_ids = atom_ids
        if (
            verify_atom_set_id
            and analysis_atom_set_id is not None
            and isinstance(obj.get("analysis"), dict)
            and isinstance(claim_text, str)
            and isinstance(obj.get("case_id"), str)
            and isinstance(obj.get("claim_revision"), int)
            and not isinstance(obj.get("claim_revision"), bool)
        ):
            _verify_atom_set_id(
                c,
                analysis=obj["analysis"],
                case_id=obj["case_id"],
                claim_revision=obj["claim_revision"],
                claim_text=claim_text,
                image_sha256=image_sha256,
                declared=analysis_atom_set_id,
            )
    elif require_analysis:
        # Invariant 2: never invent atoms; say so explicitly.
        c.add(
            "INPUT_ANALYSIS_REQUIRED",
            "analysis",
            "a valid analysis is required; atoms must not be created implicitly (invariant 2)",
        )

    # ---- retrieval ----
    known_evidence_ids: set[str] | None = None
    evidence_texts: dict[str, str] | None = None
    if obj.get("retrieval") is not None:
        _check_retrieval(
            c,
            obj["retrieval"],
            "retrieval",
            bundle_mode=bundle_mode,
            analysis_atom_set_id=analysis_atom_set_id,
            known_atom_ids=known_atom_ids,
            has_analysis=has_analysis,
            known_asset_ids=known_asset_ids or None,
        )
        known_evidence_ids, evidence_texts = _collect_evidence_index(obj["retrieval"])

    # ---- decision ----
    if obj.get("decision") is not None:
        _check_decision(
            c,
            obj["decision"],
            "decision",
            bundle_mode=bundle_mode,
            analysis_atom_set_id=analysis_atom_set_id,
            known_atom_ids=known_atom_ids,
            known_evidence_ids=known_evidence_ids,
            evidence_texts=evidence_texts,
        )

    return c.violations


def _collect_evidence_index(retrieval: Any) -> tuple[set[str], dict[str, str]]:
    """Map evidence_id -> content.text so quotes can be checked exactly."""
    ids: set[str] = set()
    texts: dict[str, str] = {}
    if not isinstance(retrieval, dict):
        return ids, texts
    for evidence in retrieval.get("evidence_list") or []:
        if not isinstance(evidence, dict):
            continue
        evidence_id = evidence.get("evidence_id")
        if not isinstance(evidence_id, str):
            continue
        ids.add(evidence_id)
        content = evidence.get("content")
        if isinstance(content, dict) and isinstance(content.get("text"), str):
            texts[evidence_id] = content["text"]
    return ids, texts


def _verify_atom_set_id(
    c: _Collector,
    *,
    analysis: dict[str, Any],
    case_id: str,
    claim_revision: int,
    claim_text: str,
    image_sha256: str | None,
    declared: str,
) -> None:
    """Invariant 1: atom_set_id is a deterministic hash over the atom set."""
    from aurora_evidence.contract.ids import atom_set_id_for, normalize_atoms_for_hash

    atoms = analysis.get("atomic_claims")
    if not isinstance(atoms, list):
        return
    try:
        normalized = normalize_atoms_for_hash(atoms)
        expected = atom_set_id_for(
            case_id=case_id,
            claim_revision=claim_revision,
            claim_text=claim_text,
            image_sha256=image_sha256,
            atomic_claims=normalized,
        )
    except Exception:
        # Shape problems are already reported by _check_analysis; recomputation
        # is a cross-check, not the place to surface malformed atoms.
        return
    if expected != declared:
        c.add(
            "ATOM_SET_ID_MISMATCH",
            "analysis.atom_set_id",
            "atom_set_id does not match SHA-256(JCS({case_id, claim_revision, "
            "claim_text_sha256, image_sha256, atomic_claims})); the atom set is "
            f"stale or was edited. expected {expected}",
        )


# --------------------------------------------------------------------------- #
# Raising wrappers
# --------------------------------------------------------------------------- #
def validate_bundle(
    bundle: Any,
    *,
    require_analysis: bool = False,
    verify_atom_set_id: bool = True,
) -> dict[str, Any]:
    """Validate a bundle, raising :class:`ContractViolation` on any breach."""
    violations = check_bundle(
        bundle,
        require_analysis=require_analysis,
        verify_atom_set_id=verify_atom_set_id,
    )
    if violations:
        raise ContractViolation(violations)
    return bundle  # type: ignore[return-value]


def validate_retrieval(retrieval: Any, **kwargs: Any) -> dict[str, Any]:
    violations = check_retrieval(retrieval, **kwargs)
    if violations:
        raise ContractViolation(violations)
    return retrieval  # type: ignore[return-value]


def validate_evidence(evidence: Any, **kwargs: Any) -> dict[str, Any]:
    violations = check_evidence(evidence, **kwargs)
    if violations:
        raise ContractViolation(violations)
    return evidence  # type: ignore[return-value]


def validate_forensic_signal(signal: Any, **kwargs: Any) -> dict[str, Any]:
    violations = check_forensic_signal(signal, **kwargs)
    if violations:
        raise ContractViolation(violations)
    return signal  # type: ignore[return-value]
