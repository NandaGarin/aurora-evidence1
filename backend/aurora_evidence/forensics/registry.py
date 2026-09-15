"""Detector registry with configuration-driven selection.

Business logic asks the registry for "the image detector" and "the text
detector"; it never names a vendor. Swapping providers is a config change, which
is what makes the two required configuration profiles meaningful.

Selection rules, in order:

1. ``provider = "none"`` -> capability reported as ``disabled``.
2. ``provider = "fixture"`` -> labelled demo detector (only legitimate in demo mode).
3. any other name -> a declarative HTTP profile of that name must exist in the
   loaded config, otherwise the capability is ``unconfigured`` with a message
   saying which profile is missing. A typo never silently degrades to fixture.

Demo/live separation is enforced here too: selecting a fixture provider while
``mode="live"`` is refused, because the contract forbids demo output appearing in
live results.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from aurora_evidence.forensics.adapters.declarative import (
    DeclarativeDetectorConfig,
    DeclarativeImageDetector,
    DeclarativeTextDetector,
)
from aurora_evidence.forensics.adapters.fixture import (
    FixtureImageDetector,
    FixtureTextDetector,
)
from aurora_evidence.forensics.interfaces import DetectorCapability


class RegistryError(ValueError):
    """Raised when the requested provider cannot be constructed."""


@dataclass
class DisabledDetector:
    """Stand-in for a provider that is switched off on purpose."""

    provider: str
    modality: str
    reason: str = "Provider dimatikan lewat konfigurasi."

    def capability(self) -> DetectorCapability:
        return DetectorCapability(
            provider=self.provider,
            modality=self.modality,  # type: ignore[arg-type]
            capability=f"{self.modality}_ai_detection",
            status="disabled",
            message=self.reason,
        )

    def detect(self, request: Any) -> Any:  # pragma: no cover - never called
        raise RegistryError(
            f"detector {self.provider!r} is disabled; check capability() before detect()"
        )


@dataclass
class UnconfiguredDetector:
    """Stand-in for a named provider whose profile or credentials are missing."""

    provider: str
    modality: str
    reason: str

    def capability(self) -> DetectorCapability:
        return DetectorCapability(
            provider=self.provider,
            modality=self.modality,  # type: ignore[arg-type]
            capability=f"{self.modality}_ai_detection",
            status="unconfigured",
            message=self.reason,
        )

    def detect(self, request: Any) -> Any:  # pragma: no cover - never called
        raise RegistryError(f"detector {self.provider!r} is not configured: {self.reason}")


def load_profiles(path: Path | str) -> dict[str, dict[str, Any]]:
    """Load a TOML detector profile file.

    TOML is read with the standard library ``tomllib``, so no YAML dependency is
    needed. The file is treated as untrusted configuration and validated by
    :meth:`DeclarativeDetectorConfig.from_mapping` on use.
    """
    file_path = Path(path)
    if not file_path.exists():
        return {}
    with file_path.open("rb") as handle:
        data = tomllib.load(handle)
    detectors = data.get("detectors", {})
    profiles: dict[str, dict[str, Any]] = {}
    for modality in ("image", "text"):
        for name, table in (detectors.get(modality, {}) or {}).items():
            table = dict(table)
            table.setdefault("modality", modality)
            profiles[f"{modality}:{name}"] = table
    return profiles


class DetectorRegistry:
    """Resolve configured detector names into detector instances."""

    def __init__(
        self,
        *,
        profiles: Mapping[str, dict[str, Any]] | None = None,
        credentials: Mapping[str, str] | None = None,
        mode: str = "demo",
    ) -> None:
        self._profiles = dict(profiles or {})
        self._credentials = dict(credentials or {})
        self._mode = mode

    # -- factory ------------------------------------------------------------
    def _build(self, modality: str, provider: str) -> Any:
        name = (provider or "none").strip().lower()

        if name in {"none", "", "off", "disabled"}:
            return DisabledDetector(provider=f"{modality}_detector_disabled", modality=modality)

        if name in {"fixture", "demo", "demo_fixture"}:
            if self._mode != "demo":
                # Invariant 12: demo output must not appear in live runs.
                return UnconfiguredDetector(
                    provider=f"demo_fixture_{modality}",
                    modality=modality,
                    reason=(
                        "Provider fixture hanya boleh dipakai pada mode=demo. "
                        "Pada mode=live, pilih provider nyata atau matikan capability ini."
                    ),
                )
            return FixtureImageDetector() if modality == "image" else FixtureTextDetector()

        key = f"{modality}:{name}"
        table = self._profiles.get(key)
        if table is None:
            available = sorted(
                profile.split(":", 1)[1]
                for profile in self._profiles
                if profile.startswith(f"{modality}:")
            )
            return UnconfiguredDetector(
                provider=name,
                modality=modality,
                reason=(
                    f"Profil detector {name!r} tidak ditemukan pada konfigurasi. "
                    f"Profil {modality} yang tersedia: {available or 'tidak ada'}. "
                    "Tidak ada fallback fixture."
                ),
            )
        try:
            config = DeclarativeDetectorConfig.from_mapping(name, table)
        except ValueError as exc:
            return UnconfiguredDetector(provider=name, modality=modality, reason=str(exc))

        credentials = {
            key_name: self._credentials.get(key_name, "")
            for key_name in config.required_config
        }
        if modality == "image":
            return DeclarativeImageDetector(config, credentials)
        return DeclarativeTextDetector(config, credentials)

    def image_detector(self, provider: str) -> Any:
        return self._build("image", provider)

    def text_detector(self, provider: str) -> Any:
        return self._build("text", provider)

    def capabilities(self, *, image_provider: str, text_provider: str) -> list[DetectorCapability]:
        """Capability report for ``GET /ready`` and the UI config panel."""
        return [
            self.image_detector(image_provider).capability(),
            self.text_detector(text_provider).capability(),
        ]
