from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from paddleocr import PaddleOCR

from config import MIN_CONFIDENCE, PADDLE_LANG
from .preprocess import preprocess_image


@dataclass
class OCRLine:
    text: str
    confidence: float


class PaddleEngine:
    def __init__(self, *, lang: str | None = None, min_confidence: float | None = None):
        self.lang = (lang or PADDLE_LANG or "multilingual").strip()
        self.min_confidence = float(min_confidence if min_confidence is not None else MIN_CONFIDENCE)
        self._ocr = PaddleOCR(use_angle_cls=True, lang=self.lang)

    def full_page(self, image_bytes: bytes) -> dict[str, object]:
        processed = preprocess_image(image_bytes)
        lines = self._run_ocr(processed)
        text = "\n".join(line.text for line in lines)
        return {
            "text": text,
            "lines": [line.__dict__ for line in lines],
            "avg_confidence": self._average_confidence(lines),
        }

    def mrz_crop(self, image_bytes: bytes) -> dict[str, object]:
        processed = preprocess_image(image_bytes)
        h, w = processed.shape[:2]

        y_start = int(h * 0.65)
        mrz_region = processed[y_start:h, 0:w]
        lines = self._run_ocr(mrz_region)

        # keep MRZ-like lines only
        mrz_lines = [line for line in lines if self._looks_like_mrz(line.text)]
        text = "\n".join(line.text for line in mrz_lines)
        return {
            "text": text,
            "lines": [line.__dict__ for line in mrz_lines],
            "avg_confidence": self._average_confidence(mrz_lines),
        }

    def _run_ocr(self, image: np.ndarray) -> list[OCRLine]:
        if len(image.shape) == 2:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)

        result = self._ocr.ocr(image, cls=True)
        lines: list[OCRLine] = []

        for block in result or []:
            if not block:
                continue
            for _, pred in block:
                text, conf = pred
                confidence = float(conf)
                if confidence < self.min_confidence:
                    continue
                cleaned = (text or "").strip()
                if not cleaned:
                    continue
                lines.append(OCRLine(text=cleaned, confidence=confidence))

        return lines

    @staticmethod
    def _average_confidence(lines: list[OCRLine]) -> float:
        if not lines:
            return 0.0
        return sum(line.confidence for line in lines) / len(lines)

    @staticmethod
    def _looks_like_mrz(text: str) -> bool:
        normalized = text.replace(" ", "")
        if len(normalized) < 25:
            return False
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<")
        ratio = sum(ch in allowed for ch in normalized.upper()) / len(normalized)
        return ratio > 0.9
