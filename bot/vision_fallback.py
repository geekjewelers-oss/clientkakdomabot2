import logging

logger = logging.getLogger(__name__)


def vision_extract_text(image_bytes, current_text="", min_len_for_skip=60):
    _ = image_bytes
    if current_text and len(current_text) > min_len_for_skip:
        logger.info("[OCR] Vision skipped: text_len=%s > min_len_for_skip=%s", len(current_text), min_len_for_skip)
        return current_text

    logger.info("[OCR] Vision fallback called")
    return current_text or ""
