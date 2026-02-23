import io
import logging

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

_reader = None


def _get_reader():
    global _reader
    if _reader is None:
        import easyocr
        _reader = easyocr.Reader(["en"])
    return _reader


def easyocr_extract_text(image_bytes):
    logger.info("fallback started")

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    image_np = np.array(image)

    reader = _get_reader()
    result = reader.readtext(image_np)

    texts = [item[1] for item in result if len(item) > 1 and item[1]]
    joined_text = " ".join(texts).strip()

    logger.info("number of boxes found: %s", len(result))
    logger.info("fallback text length: %s", len(joined_text))

    return joined_text
