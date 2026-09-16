"""Bridge between the HTTP/worker layer and the pure retrieval pipeline.

The pipeline (``aurora_evidence.retrieval.pipeline``) is deliberately free of
database and web-framework concerns: bundle in, bundle out. This module supplies
what it cannot know by itself — configuration resolved from the environment and
the provider profile — and persists the outcome.

Responsibilities, in order:

1. Build :class:`RetrievalSettings` from :class:`app.config.Settings`.
2. Run the pipeline (which validates its own output against the contract).
3. Record ``Case`` / ``Revision`` / ``RetrievalRun`` so history survives restart.

What this module must NOT do: reinterpret pipeline results. If the pipeline says
a run was ``partial``, that status is carried through unchanged, because a
partial retrieval is a legitimate result the consumer must be able to see.
"""

from __future__ import annotations

from typing import Any

from aurora_evidence.contract.validate import ContractViolation, check_bundle
from aurora_evidence.retrieval.pipeline import RetrievalSettings, execute
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import Case, RetrievalRun, Revision
from app.services.capabilities import (
    detector_credentials,
    load_provider_profile,
    resolve_selection,
)
from aurora_evidence.contract.canonical import text_sha256


class RetrievalInputError(ValueError):
    """The bundle cannot be processed; callers map this to HTTP 422."""

    def __init__(self, message: str, violations: list[Any] | None = None) -> None:
        super().__init__(message)
        self.violations = violations or []


def build_settings(settings: Settings, *, mode: str) -> RetrievalSettings:
    """Translate app configuration into pipeline settings.

    ``mode`` comes from the bundle rather than the environment: a single
    instance may serve a demo bundle and a live bundle, and the registry needs
    the *bundle's* mode to decide whether a fixture detector is permissible.
    """
    raw, profiles = load_provider_profile(settings.provider_profile)
    retrieval_cfg = raw.get("retrieval", {}) if isinstance(raw, dict) else {}
    image_provider, text_provider = resolve_selection(settings)

    return RetrievalSettings(
        mode=mode,
        corpus_path=settings.corpus_path,
        per_query_limit=int(retrieval_cfg.get("per_query_limit", 10)),
        max_evidence=int(retrieval_cfg.get("max_evidence", 20)),
        # Env wins over the profile file so an operator can tighten the budget
        # without editing committed config.
        budget_seconds=float(settings.retrieval_budget_seconds),
        strict_temporal=bool(retrieval_cfg.get("strict_temporal", True)),
        per_group_cap=retrieval_cfg.get("per_group_cap", 2),
        mmr_lambda=float(retrieval_cfg.get("mmr_lambda", 0.7)),
        corpus_version=str(retrieval_cfg.get("corpus_version", settings.corpus_path.name)),
        image_detector_provider=image_provider,
        text_detector_provider=text_provider,
        detector_profiles=profiles,
        detector_credentials=detector_credentials(settings),
    )


def validate_input_bundle(payload: Any) -> list[Any]:
    """Validate an incoming bundle with the repository's own validator.

    We use ``check_bundle`` (not only the Pydantic models) because it is the
    authoritative implementation of the 16 invariants and it returns *all*
    violations at once, which makes a 422 response actually actionable.

    ``require_analysis`` stays False: invariant 2 explicitly allows Dev 2 to run
    with ``analysis=null`` using the full caption as the query.
    """
    return check_bundle(payload, require_analysis=False)


def upsert_case_revision(session: Session, bundle: dict[str, Any]) -> Revision:
    """Ensure the (case, revision) rows exist and match the submitted input.

    Identity rules from the contract: ``case_id`` is stable across the pipeline,
    and a given ``claim_revision`` pins one specific (claim_text, image) pair.
    Submitting different text or a different image under the same revision is a
    conflict, not an update — otherwise stored history would silently describe
    inputs that no longer exist.
    """
    case_id = bundle["case_id"]
    claim_revision = int(bundle["claim_revision"])
    claim_text = bundle["input"]["claim_text"]
    text_hash = text_sha256(claim_text)
    image = bundle["input"].get("image")
    image_asset_id = image.get("asset_id") if isinstance(image, dict) else None
    analysis = bundle.get("analysis")

    case = session.get(Case, case_id)
    if case is None:
        case = Case(case_id=case_id, mode=bundle.get("mode", "demo"))
        session.add(case)
        session.flush()

    revision = session.scalar(
        select(Revision).where(
            Revision.case_id == case_id, Revision.claim_revision == claim_revision
        )
    )
    if revision is not None:
        if revision.claim_text_sha256 != text_hash or (revision.image_asset_id or None) != (
            image_asset_id or None
        ):
            raise RevisionConflict(
                "case_id/claim_revision yang sama sudah tercatat dengan claim_text atau "
                "gambar berbeda; naikkan claim_revision untuk revisi baru"
            )
        # Analysis may legitimately arrive later for the same revision.
        if isinstance(analysis, dict):
            revision.analysis_json = analysis
            revision.atom_set_id = analysis.get("atom_set_id")
        return revision

    revision = Revision(
        case_id=case_id,
        claim_revision=claim_revision,
        claim_text=claim_text,
        claim_text_sha256=text_hash,
        language=bundle["input"].get("language", "und"),
        image_asset_id=image_asset_id,
        as_of=bundle["input"].get("as_of"),
        atom_set_id=analysis.get("atom_set_id") if isinstance(analysis, dict) else None,
        analysis_json=analysis if isinstance(analysis, dict) else None,
    )
    session.add(revision)
    session.flush()
    return revision


class RevisionConflict(ValueError):
    """Same case/revision submitted with different input; callers map to 409."""


def run_retrieval(
    session: Session,
    *,
    bundle: dict[str, Any],
    payload_hash: str | None = None,
    settings: Settings | None = None,
) -> tuple[dict[str, Any], str]:
    """Execute retrieval and persist the run. Returns ``(bundle, run_status)``.

    ``run_status`` is the contract ``RunInfo.status``: completed | partial |
    failed. Callers map it to the job status; a partial run is still a usable
    result and must not be reported as a failure.
    """
    app_settings = settings or get_settings()
    mode = bundle.get("mode", app_settings.mode_default)

    violations = validate_input_bundle(bundle)
    if violations:
        raise RetrievalInputError(
            "bundle masukan melanggar kontrak v1.0.0", violations=list(violations)
        )

    revision = upsert_case_revision(session, bundle)

    pipeline_settings = build_settings(app_settings, mode=mode)
    try:
        result = execute(bundle, pipeline_settings)
    except ContractViolation as exc:
        # The pipeline refused to emit a bundle it would reject on import. That
        # is a server-side defect, so surface it loudly rather than storing it.
        raise RetrievalInputError(
            "pipeline menghasilkan bundle yang tidak valid terhadap kontrak",
            violations=list(exc.violations),
        ) from exc

    retrieval = result["retrieval"]
    run_status = str(retrieval["run"]["status"])

    session.add(
        RetrievalRun(
            run_id=retrieval["run"]["run_id"],
            revision_id=revision.id,
            mode=mode,
            status=run_status,
            retrieval_json=retrieval,
            run_inputs_hash=payload_hash,
        )
    )
    return result, run_status


def list_runs(session: Session, *, case_id: str | None = None, limit: int = 50) -> list[dict]:
    """Case/run history for the UI. Newest first."""
    statement = (
        select(RetrievalRun, Revision)
        .join(Revision, RetrievalRun.revision_id == Revision.id)
        .order_by(RetrievalRun.created_at.desc())
        .limit(limit)
    )
    if case_id:
        statement = statement.where(Revision.case_id == case_id)
    rows = session.execute(statement).all()
    history: list[dict] = []
    for run, revision in rows:
        retrieval = run.retrieval_json or {}
        history.append(
            {
                "run_id": run.run_id,
                "case_id": revision.case_id,
                "claim_revision": revision.claim_revision,
                "claim_text": revision.claim_text,
                "mode": run.mode,
                "status": run.status,
                "created_at": run.created_at.isoformat() if run.created_at else None,
                "evidence_count": len(retrieval.get("evidence_list", [])),
                "signal_count": len(retrieval.get("forensic_signals", [])),
            }
        )
    return history
