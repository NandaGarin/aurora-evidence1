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
