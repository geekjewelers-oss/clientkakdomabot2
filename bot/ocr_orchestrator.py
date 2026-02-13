import logging
from typing import Any

import cv2
import numpy as np

from bot.mrz_parser import extract_mrz_from_image_bytes, extract_text_from_image_bytes, parse_td3_mrz
from bot.ocr_fallback import easyocr_extract_text
from bot.ocr_quality import blur_score, build_ocr_quality_report, exposure_score
from bot.vision_fallback import vision_extract_text

logger = logging.getLogger(__name__)


def _decode_gray_image(img_bytes: bytes) -> np.ndarray | None:
    np_buf = np.frombuffer(img_bytes, dtype=np.uint8)
    img = cv2.imdecode(np_buf, cv2.IMREAD_COLOR)
    if img is None:
        return None
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return cv2.equalizeHist(gray)


def _attach_quality(result: dict[str, Any], gray: np.ndarray | None) -> dict[str, Any]:
    parsed = result.get("parsed") or {}
    if gray is None:
        quality = build_ocr_quality_report(parsed, blur=0.0, exposure=0.0)
    else:
        blur = blur_score(gray)
        exposure = exposure_score(gray)
        quality = build_ocr_quality_report(parsed, blur=blur, exposure=exposure)
    result["quality"] = quality
    return result


def ocr_pipeline_extract(img_bytes: bytes) -> dict[str, Any]:
    gray = _decode_gray_image(img_bytes)

    line1, line2, mrz_text, _mode = extract_mrz_from_image_bytes(img_bytes)
    if line1 and line2:
        parsed = parse_td3_mrz(line1, line2)
        checksum_ok = parsed.get("_mrz_checksum_ok", False)
        confidence = "high" if checksum_ok else "medium"
        parsed["mrz_confidence_score"] = 0.9 if checksum_ok else 0.6
        text_value = mrz_text or ""
        logger.info("[OCR] OCR stage: mrz, text_len=%s", len(text_value))
        return _attach_quality({
            "text": text_value,
            "source": "mrz",
            "confidence": confidence,
            "parsed": parsed,
            "mrz_lines": (line1, line2),
        }, gray)

    text = extract_text_from_image_bytes(img_bytes)
    logger.info("[OCR] OCR stage: tesseract, text_len=%s", len(text or ""))

    easy_text = easyocr_extract_text(img_bytes)
    logger.info("[OCR] OCR stage: easyocr, text_len=%s", len(easy_text or ""))
    if easy_text and len(easy_text) > 40:
        return _attach_quality({
            "text": easy_text,
            "source": "easyocr",
            "confidence": "medium",
            "parsed": {},
            "mrz_lines": None,
        }, gray)

    vision_text = vision_extract_text(img_bytes, current_text=easy_text or text, min_len_for_skip=60)
    logger.info("[OCR] OCR stage: vision, text_len=%s", len(vision_text or ""))

    if vision_text:
        return _attach_quality({
            "text": vision_text,
            "source": "vision",
            "confidence": "medium",
            "parsed": {},
            "mrz_lines": None,
        }, gray)

    return _attach_quality({
        "text": easy_text or text or "",
        "source": "tesseract",
        "confidence": "low",
        "parsed": {},
        "mrz_lines": None,
    }, gray)
