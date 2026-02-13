import logging
from typing import Any

from bot.mrz_parser import extract_mrz_from_image_bytes, extract_text_from_image_bytes, parse_td3_mrz
from bot.ocr_fallback import easyocr_extract_text
from bot.vision_fallback import vision_extract_text

logger = logging.getLogger(__name__)


def ocr_pipeline_extract(img_bytes: bytes) -> dict[str, Any]:
    line1, line2, mrz_text, _mode = extract_mrz_from_image_bytes(img_bytes)
    if line1 and line2:
        parsed = parse_td3_mrz(line1, line2)
        checksum_ok = parsed.get("_mrz_checksum_ok", False)
        confidence = "high" if checksum_ok else "medium"
        text_value = mrz_text or ""
        logger.info("[OCR] OCR stage: mrz, text_len=%s", len(text_value))
        return {
            "text": text_value,
            "source": "mrz",
            "confidence": confidence,
            "parsed": parsed,
            "mrz_lines": (line1, line2),
        }

    text = extract_text_from_image_bytes(img_bytes)
    logger.info("[OCR] OCR stage: tesseract, text_len=%s", len(text or ""))

    easy_text = easyocr_extract_text(img_bytes)
    logger.info("[OCR] OCR stage: easyocr, text_len=%s", len(easy_text or ""))
    if easy_text and len(easy_text) > 40:
        return {
            "text": easy_text,
            "source": "easyocr",
            "confidence": "medium",
            "parsed": {},
            "mrz_lines": None,
        }

    vision_text = vision_extract_text(img_bytes, current_text=easy_text or text, min_len_for_skip=60)
    logger.info("[OCR] OCR stage: vision, text_len=%s", len(vision_text or ""))

    if vision_text:
        return {
            "text": vision_text,
            "source": "vision",
            "confidence": "medium",
            "parsed": {},
            "mrz_lines": None,
        }

    return {
        "text": easy_text or text or "",
        "source": "tesseract",
        "confidence": "low",
        "parsed": {},
        "mrz_lines": None,
    }
