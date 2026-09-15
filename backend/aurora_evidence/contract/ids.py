"""Deterministic identity and ID-generation rules shared across all three modules.

Rules (see KONTRAK_BERSAMA.md):
- case_id, run_id, job_id: lowercase UUID with hyphens.
- asset_id: ``asset_`` + sha256(original uploaded bytes).
- atom_id: ``a000001`` ... within one atom set.
- atom_set_id: ``aset_`` + SHA256(JCS of
  {case_id, claim_revision, claim_text_sha256, image_sha256, atomic_claims}).
- evidence_id: ``ev_`` + run-retrieval UUID hex (no hyphens) + ``_`` + six-digit sequence.
- signal_id: ``fs_`` + run-retrieval UUID hex (no hyphens) + ``_`` + six-digit sequence.
- region_id: ``rg_`` + run-analysis UUID hex (no hyphens) + ``_`` + six-digit sequence.
"""

from __future__ import annotations

import uuid
from typing import Any

from aurora_evidence.contract.canonical import canonical_sha256, text_sha256


def new_case_id() -> str:
    return str(uuid.uuid4())


def new_run_id() -> str:
    return str(uuid.uuid4())


def new_job_id() -> str:
    return str(uuid.uuid4())


def _hex(run_id: str) -> str:
    """UUID hex without hyphens; accepts a UUID string or hex already."""
    return uuid.UUID(run_id).hex


def evidence_id(run_id: str, sequence: int) -> str:
    if sequence < 1:
        raise ValueError("evidence sequence must be >= 1")
    return f"ev_{_hex(run_id)}_{sequence:06d}"


def signal_id(run_id: str, sequence: int) -> str:
    if sequence < 1:
        raise ValueError("signal sequence must be >= 1")
    return f"fs_{_hex(run_id)}_{sequence:06d}"


def region_id(run_id: str, sequence: int) -> str:
    if sequence < 1:
        raise ValueError("region sequence must be >= 1")
    return f"rg_{_hex(run_id)}_{sequence:06d}"


def asset_id_for(sha256_hex_value: str) -> str:
    return f"asset_{sha256_hex_value}"


def atom_set_id_for(
    case_id: str,
    claim_revision: int,
    claim_text: str,
    image_sha256: str | None,
    atomic_claims: list[dict[str, Any]],
) -> str:
    """Compute ``aset_`` + SHA256(JCS(...)) exactly per the contract.

    ``atomic_claims`` must contain ALL Atom fields per the contract; sort the
    array by atom_id, depends_on lexicographically, and spans by start then end.
    This function assumes the caller passes already-normalized atom dicts (the
    canonicalization then guarantees a stable hash). Dev 2 rarely computes this
    (it comes from Dev 1) but needs it to VALIDATE imported analysis.
    """
    obj = {
        "case_id": case_id,
        "claim_revision": claim_revision,
        "claim_text_sha256": text_sha256(claim_text),
        "image_sha256": image_sha256,
        "atomic_claims": atomic_claims,
    }
    return f"aset_{canonical_sha256(obj)}"



#: Every Atom field that participates in the atom_set_id hash, per the contract
#: ("atomic_claims berisi SEMUA field Atom sesuai kontrak").
_ATOM_HASH_FIELDS = (
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
_QUALIFIER_HASH_FIELDS = ("negated", "quantity", "time", "location")


def normalize_atoms_for_hash(atoms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Put an atom list into the exact canonical shape the contract hashes.

    The contract fixes three orderings so that the same atom set produces the
    same ``atom_set_id`` in all three repositories regardless of how each one
    happens to build its lists:

    * the array is sorted by ``atom_id``,
    * ``depends_on`` is sorted lexicographically,
    * ``spans`` are sorted by ``start`` then ``end``.

    Only contract fields are kept. Any local extra key is dropped here rather
    than silently changing the hash — extras belong in ``extensions``, not in
    the identity of the atom set.

    Note that JCS already sorts object keys, so this function only has to fix
    *array* order; it deliberately does not reorder object members itself.
    """
    normalized: list[dict[str, Any]] = []
    for atom in atoms:
        if not isinstance(atom, dict):
            raise TypeError(f"atom must be an object, got {type(atom)!r}")

        qualifiers_in = atom.get("qualifiers")
        if not isinstance(qualifiers_in, dict):
            raise TypeError("atom.qualifiers must be an object")
        qualifiers = {key: qualifiers_in.get(key) for key in _QUALIFIER_HASH_FIELDS}

        spans_in = atom.get("spans")
        if not isinstance(spans_in, list):
            raise TypeError("atom.spans must be an array")
        spans = sorted(
            ({"start": span.get("start"), "end": span.get("end")} for span in spans_in),
            key=lambda span: (span["start"], span["end"]),
        )

        depends_in = atom.get("depends_on")
        if not isinstance(depends_in, list):
            raise TypeError("atom.depends_on must be an array")
        depends_on = sorted(depends_in)

        entry = {key: atom.get(key) for key in _ATOM_HASH_FIELDS}
        entry["qualifiers"] = qualifiers
        entry["spans"] = spans
        entry["depends_on"] = depends_on
        normalized.append(entry)

    normalized.sort(key=lambda atom: atom["atom_id"])
    return normalized


def atom_set_id_from_bundle(bundle: dict[str, Any]) -> str:
    """Recompute ``atom_set_id`` straight from a bundle (validation helper)."""
    analysis = bundle.get("analysis") or {}
    image = (bundle.get("input") or {}).get("image")
    return atom_set_id_for(
        case_id=bundle["case_id"],
        claim_revision=bundle["claim_revision"],
        claim_text=bundle["input"]["claim_text"],
        image_sha256=(image or {}).get("sha256") if image else None,
        atomic_claims=normalize_atoms_for_hash(analysis.get("atomic_claims") or []),
    )
