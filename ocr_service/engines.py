from __future__ import annotations

from dataclasses import dataclass

from .models import JobStatus, MRZData, OCRQuality
from .settings import OCRSettings


class QualityAnalyzer:
    def analyze(self, *, content: bytes, confidence: float) -> OCRQuality:
        size = len(content)
        blur = min(1.0, size / 2000)
        exposure = 0.9 if size > 100 else 0.4
        lighting_ok = exposure >= 0.5
        norm_conf = max(0.0, min(1.0, confidence))
        return OCRQuality(
            blur_score=blur,
            exposure_score=exposure,
            lighting_ok=lighting_ok,
            normalized_confidence=norm_conf,
        )


@dataclass
class Decision:
    status: JobStatus
    use_fallback: bool


class SLAEngine:
    def __init__(self, cfg: OCRSettings):
        self.cfg = cfg

    def decide(self, *, mrz: MRZData, cycle_count: int) -> Decision:
        if mrz.confidence >= self.cfg.auto_accept and mrz.checksum_ok:
            return Decision(status=JobStatus.auto_accepted, use_fallback=False)
        if mrz.confidence >= self.cfg.fallback_threshold:
            if cycle_count >= 2 and self.cfg.manual_after_second_cycle:
                return Decision(status=JobStatus.manual_review, use_fallback=False)
            return Decision(status=JobStatus.processing, use_fallback=True)
        if cycle_count >= 2 and self.cfg.manual_after_second_cycle:
            return Decision(status=JobStatus.manual_review, use_fallback=False)
        return Decision(status=JobStatus.processing, use_fallback=True)


class RetryEngine:
    def next_cycle(self, cycle_count: int) -> int:
        return cycle_count + 1
