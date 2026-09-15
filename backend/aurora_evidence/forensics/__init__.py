"""AI detector branch (forensic signals).

Kept strictly separate from the factual evidence branch. Detector output goes to
``Retrieval.forensic_signals`` only: it is never evidence, never a stance, and
never a vote in a factual decision. A camera/human indication is likewise not
proof that a caption is true.
"""

from aurora_evidence.forensics.interfaces import (
    DetectionOutcome,
    DetectorCapability,
    DetectorError,
    ImageAIDetector,
    ImageDetectionRequest,
    RawScale,
    TextAIDetector,
    TextDetectionRequest,
)
from aurora_evidence.forensics.normalization import (
    AssessmentBands,
    NormalizationError,
    build_signal,
    derive_assessment,
    normalize_score,
)
from aurora_evidence.forensics.registry import DetectorRegistry, RegistryError, load_profiles

__all__ = [
    "AssessmentBands",
    "DetectionOutcome",
    "DetectorCapability",
    "DetectorError",
    "DetectorRegistry",
    "ImageAIDetector",
    "ImageDetectionRequest",
    "NormalizationError",
    "RawScale",
    "RegistryError",
    "TextAIDetector",
    "TextDetectionRequest",
    "build_signal",
    "derive_assessment",
    "load_profiles",
    "normalize_score",
]
