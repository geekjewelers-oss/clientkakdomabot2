import logging

logger = logging.getLogger(__name__)


def easyocr_extract_text(image_bytes):
    _ = image_bytes
    logger.info("fallback called")
    return ""
