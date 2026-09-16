"""Capability reporting for ``GET /ready`` and the UI config panel.

Why this exists as its own module: ``/ready`` must be able to answer "what can
this instance actually do right now?" *without* running a retrieval. The
pipeline reports provider status as a side effect of executing; readiness needs
the same information up front.

Honesty rules enforced here (they are contract requirements, not preferences):

* A provider that is switched off reports ``disabled`` — not "ok".
* A provider selected by name but missing credentials/profile reports
  ``unconfigured``. It is never silently replaced by a fixture.
* A capability with no adapter at all reports ``unsupported`` rather than
  pretending to be available.
* Nothing here reads a secret *value*; only whether the required keys are set.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from aurora_evidence.forensics.registry import DetectorRegistry, load_profiles
from aurora_evidence.retrieval.providers.local_corpus import LocalCorpusProvider

from app.config import Settings

#: Env var names that supply credentials, keyed by the config key an adapter
#: profile declares in ``required_config``. Names only — never values.
CREDENTIAL_ENV = {
    "api_user": "AURORA_SIGHTENGINE_API_USER",
    "api_secret": "AURORA_SIGHTENGINE_API_SECRET",
    "api_key": "AURORA_TEXT_DETECTOR_API_KEY / AURORA_GPTZERO_API_KEY",
}


def status_entry(provider: str, capability: str, status: str, message: str | None) -> dict[str, Any]:
    """Contract-shaped ``ProviderStatus`` dict."""
    return {"provider": provider, "capability": capability, "status": status, "message": message}


def detector_credentials(settings: Settings) -> dict[str, str]:
    """Collect detector credentials by profile config-key name.

    Kept in one place so the API, the worker and the CLI resolve credentials
    identically; a mismatch there would make ``/ready`` disagree with what the
    pipeline can actually call.
    """
    values = {
        "api_user": settings.secret("sightengine_user"),
        "api_secret": settings.secret("sightengine_secret"),
        "api_key": settings.secret("gptzero") or settings.secret("copyleaks"),
    }
    return {key: value for key, value in values.items() if value}


def load_provider_profile(path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Return ``(raw_toml, detector_profiles)`` for a profile file.

    A missing file is not fatal: it means "no declarative adapters configured",
    which surfaces downstream as ``unconfigured`` for any named provider.
    """
    if not path.exists():
        return {}, {}
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    return raw, load_profiles(path)


def resolve_selection(settings: Settings) -> tuple[str, str]:
    """Detector provider names, env taking precedence over the profile file.

    Env wins so an operator can switch providers without editing a committed
    config file (and so tests can pin a provider explicitly).
    """
    raw, _ = load_provider_profile(settings.provider_profile)
    selection = raw.get("selection", {}) if isinstance(raw, dict) else {}
    image = settings.image_detector_provider
    text = settings.text_detector_provider
    if image in ("", "none") and selection.get("image_detector"):
        image = str(selection["image_detector"])
    if text in ("", "none") and selection.get("text_detector"):
        text = str(selection["text_detector"])
    return image, text


def retrieval_capabilities(settings: Settings) -> list[dict[str, Any]]:
    """Status of every *retrieval* capability (evidence sources)."""
    entries: list[dict[str, Any]] = []

    # Local corpus BM25 — the real offline path; no credentials needed.
    provider = LocalCorpusProvider(settings.corpus_path)
    provider.load()
    capability = provider.capability()
    entries.append(
        status_entry(
            capability.provider, "local_corpus_search", capability.status, capability.message
        )
    )

    # Optional external sources. No adapter exists yet, so the only honest
    # answers are "disabled" (switched off) or "unsupported" (selected but not
    # implemented). Claiming "ok" here would be a false capability report.
    entries.append(
        _external_source(
            selected=settings.websearch_provider,
            provider_label=settings.websearch_provider or "none",
            capability="web_search",
            env_name="AURORA_WEBSEARCH_PROVIDER",
        )
    )
    entries.append(
        _external_source(
            selected="mafindo" if settings.has_secret("mafindo") else "none",
            provider_label="mafindo",
            capability="fact_check",
            env_name="AURORA_MAFINDO_API_KEY",
        )
    )
    entries.append(
        _external_source(
            selected=settings.image_provenance_provider,
            provider_label=settings.image_provenance_provider or "none",
            capability="image_provenance",
            env_name="AURORA_IMAGE_PROVENANCE_PROVIDER",
        )
    )

    # Dense retrieval: not implemented. Even when the flag is on, the honest
    # status is "unsupported" — a flag cannot conjure an implementation.
    entries.append(
        status_entry(
            "dense",
            "dense_retrieval",
            "unsupported" if settings.dense_enabled else "disabled",
            (
                "AURORA_DENSE_ENABLED=true tetapi dense retrieval belum "
                "diimplementasikan; BM25 lokal tetap tersedia."
                if settings.dense_enabled
                else "Dense retrieval dimatikan; BM25 lokal tetap tersedia."
            ),
        )
    )
    return entries


def _external_source(
    *, selected: str, provider_label: str, capability: str, env_name: str
) -> dict[str, Any]:
    if selected in ("", "none"):
        return status_entry(
            provider_label, capability, "disabled", f"{env_name} belum dipilih/diaktifkan."
        )
    return status_entry(
        provider_label,
        capability,
        "unsupported",
        (
            f"Provider {provider_label!r} dipilih, tetapi adapter konkret belum "
            "diimplementasikan pada build ini. Tidak ada fallback fixture."
        ),
    )


def detector_capabilities(settings: Settings, *, mode: str) -> list[dict[str, Any]]:
    """Status of both AI-detector capabilities.

    ``mode`` matters: the registry refuses a fixture detector when
    ``mode="live"`` (invariant 12 — demo output must not appear in live runs),
    so readiness reported for a live instance differs from a demo instance.
    """
    _, profiles = load_provider_profile(settings.provider_profile)
    image_provider, text_provider = resolve_selection(settings)
    registry = DetectorRegistry(
        profiles=profiles, credentials=detector_credentials(settings), mode=mode
    )
    entries: list[dict[str, Any]] = []
    for capability in registry.capabilities(
        image_provider=image_provider, text_provider=text_provider
    ):
        entries.append(
            status_entry(
                capability.provider, capability.capability, capability.status, capability.message
            )
        )
    return entries


def all_capabilities(settings: Settings, *, mode: str) -> list[dict[str, Any]]:
    """Every capability, retrieval first then forensics."""
    return retrieval_capabilities(settings) + detector_capabilities(settings, mode=mode)


def local_corpus_ready(settings: Settings) -> tuple[bool, str | None]:
    """Whether the mandatory local retrieval path is usable.

    This is the one retrieval dependency that must work: if the corpus is
    missing there is no real evidence source at all, which the contract treats
    as a failed local dependency (``/ready`` -> 503), not mere degradation.
    """
    provider = LocalCorpusProvider(settings.corpus_path)
    provider.load()
    capability = provider.capability()
    return capability.status == "ok", capability.message
