"""Deterministic fixture detectors for demo mode.

These exist so the wiring — job queue, normalization, contract validation, UI
panels — can be exercised without any API key, GPU or model download. They are
labelled fixtures everywhere they surface:

* ``provider`` names start with ``demo_fixture_``,
* ``DetectorCapability.is_fixture`` is ``True``,
* every signal carries a limitation saying the number is synthetic.

They are deliberately NOT a fallback. If a live provider is selected but lacks
credentials, the registry reports ``unconfigured``/``unavailable``; it never
quietly substitutes these values, because that would let a demo number be read
as a real forensic result.

The scores are a hash of the input, so they are stable across runs (good for
tests and screenshots) and obviously not a measurement of anything.
"""

from __future__ import annotations

import hashlib

from aurora_evidence.forensics.interfaces import (
    DetectionOutcome,
    DetectorCapability,
    ImageDetectionRequest,
    RawScale,
    TextDetectionRequest,
)

#: Minimum characters before a text verdict is even attempted. Short strings do
#: not carry enough signal for any detector, and the contract requires that to
#: appear as unsupported/inconclusive rather than as "human-written".
FIXTURE_MIN_CHARACTERS = 120

_FIXTURE_SCALE = RawScale(min=0.0, max=1.0, higher_means_ai=True)

_FIXTURE_NOTE = (
    "NILAI DEMO/SINTETIS dari fixture deterministik, bukan hasil detektor nyata. "
    "Tidak boleh dipakai sebagai bukti asal konten."
)


def _stable_unit_score(payload: bytes, salt: str) -> float:
    """Deterministic pseudo-score in [0,1] derived from the input bytes."""
    digest = hashlib.sha256(salt.encode("utf-8") + payload).digest()
    return int.from_bytes(digest[:4], "big") / 0xFFFFFFFF


class FixtureImageDetector:
    """Demo image detector. Deterministic, offline, clearly labelled."""

    provider = "demo_fixture_image"

    def __init__(self, *, min_pixels: int = 64) -> None:
        self._min_pixels = min_pixels

    def capability(self) -> DetectorCapability:
        return DetectorCapability(
            provider=self.provider,
            modality="image",
            capability="image_ai_detection",
            status="ok",
            message="Fixture demo deterministik; tidak memanggil layanan apa pun.",
            model_version="fixture-image-1",
            min_pixels=self._min_pixels,
            upload_method="in_process",
            limitations=(_FIXTURE_NOTE,),
            is_fixture=True,
        )

    def detect(self, request: ImageDetectionRequest) -> DetectionOutcome:
        # A corrupt or absurdly small image is 'unsupported', not 'human'.
        if request.width < self._min_pixels or request.height < self._min_pixels:
            return DetectionOutcome(
                status="unsupported",
                error_code="IMAGE_TOO_SMALL",
                model_version="fixture-image-1",
                limitations=[
                    _FIXTURE_NOTE,
                    f"Gambar {request.width}x{request.height} lebih kecil dari "
                    f"batas {self._min_pixels}px; tidak dinilai.",
                ],
                calibration_status="not_applicable",
            )
        if not request.data:
            return DetectionOutcome(
                status="failed",
                error_code="IMAGE_UNREADABLE",
                model_version="fixture-image-1",
                limitations=[_FIXTURE_NOTE, "Byte gambar kosong/tidak terbaca."],
                calibration_status="not_applicable",
            )

        score = _stable_unit_score(request.data, "aurora-image")
        return DetectionOutcome(
            status="ok",
            raw_score=round(score, 6),
            raw_scale=_FIXTURE_SCALE,
            raw_label="fixture_synthetic_band",
            model_version="fixture-image-1",
            applicable_language=None,
            limitations=[_FIXTURE_NOTE],
            calibration_status="not_applicable",
            diagnostics={
                "preprocessing": request.preprocessing,
                "bytes_modified": request.bytes_modified,
                "sha256_prefix": request.sha256[:12],
            },
        )


class FixtureTextDetector:
    """Demo text detector with realistic refusal behaviour for short input."""

    provider = "demo_fixture_text"

    def __init__(
        self,
        *,
        min_characters: int = FIXTURE_MIN_CHARACTERS,
        supported_languages: tuple[str, ...] = ("en",),
    ) -> None:
        self._min_characters = min_characters
        self._supported = supported_languages

    def capability(self) -> DetectorCapability:
        return DetectorCapability(
            provider=self.provider,
            modality="text",
            capability="text_ai_detection",
            status="ok",
            message="Fixture demo deterministik; tidak memanggil layanan apa pun.",
            model_version="fixture-text-1",
            languages=self._supported,
            min_characters=self._min_characters,
            upload_method="in_process",
            limitations=(
                _FIXTURE_NOTE,
                "Fixture ini hanya 'mendukung' bahasa Inggris agar jalur "
                "unsupported untuk bahasa Indonesia dapat diuji.",
            ),
            is_fixture=True,
        )

    def detect(self, request: TextDetectionRequest) -> DetectionOutcome:
        stripped = request.text.strip()

        # Short text: explicitly unsupported, never "likely human".
        if len(stripped) < self._min_characters:
            return DetectionOutcome(
                status="unsupported",
                error_code="TEXT_TOO_SHORT",
                model_version="fixture-text-1",
                applicable_language=request.language,
                limitations=[
                    _FIXTURE_NOTE,
                    f"Teks {len(stripped)} karakter di bawah batas "
                    f"{self._min_characters}; tidak dinilai. Skor null bukan "
                    "bukti teks ditulis manusia.",
                ],
                calibration_status="not_applicable",
            )

        # Unsupported language: also 'unsupported'.
        language = (request.language or "und").split("-")[0].lower()
        if language not in self._supported:
            return DetectionOutcome(
                status="unsupported",
                error_code="LANGUAGE_NOT_SUPPORTED",
                model_version="fixture-text-1",
                applicable_language=request.language,
                limitations=[
                    _FIXTURE_NOTE,
                    f"Bahasa {request.language!r} tidak didukung provider ini; "
                    "hasil tidak dinilai, bukan dinyatakan manusia.",
                ],
                calibration_status="not_applicable",
            )

        score = _stable_unit_score(stripped.encode("utf-8"), "aurora-text")
        return DetectionOutcome(
            status="ok",
            raw_score=round(score, 6),
            raw_scale=_FIXTURE_SCALE,
            raw_label="fixture_synthetic_band",
            model_version="fixture-text-1",
            applicable_language=request.language,
            limitations=[_FIXTURE_NOTE],
            calibration_status="not_applicable",
            diagnostics={"characters": len(stripped), "target_kind": request.target_kind},
        )
