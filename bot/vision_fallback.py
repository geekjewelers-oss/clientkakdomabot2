import base64
import logging

import requests

from config import YANDEX_VISION_API_KEY, YANDEX_VISION_FOLDER_ID

logger = logging.getLogger(__name__)

YANDEX_VISION_ENDPOINT = "https://vision.api.cloud.yandex.net/vision/v1/batchAnalyze"


def yandex_vision_extract_text(image_bytes):
    if not YANDEX_VISION_API_KEY or not YANDEX_VISION_FOLDER_ID:
        logger.info("Yandex Vision credentials are not configured")
        return ""

    content = base64.b64encode(image_bytes).decode("utf-8")
    payload = {
        "folderId": YANDEX_VISION_FOLDER_ID,
        "analyze_specs": [
            {
                "content": content,
                "features": [
                    {
                        "type": "TEXT_DETECTION",
                        "text_detection_config": {
                            "languageCodes": ["en"]
                        },
                    }
                ],
            }
        ],
    }
    headers = {
        "Authorization": f"Api-Key {YANDEX_VISION_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            YANDEX_VISION_ENDPOINT,
            json=payload,
            headers=headers,
            timeout=20,
        )
        response.raise_for_status()
    except requests.RequestException:
        logger.exception("Yandex Vision request failed")
        return ""

    data = response.json()

    words = []
    for analyzed in data.get("results", []):
        for result in analyzed.get("results", []):
            text_detection = result.get("textDetection", {})
            for page in text_detection.get("pages", []):
                for block in page.get("blocks", []):
                    for line in block.get("lines", []):
                        for word in line.get("words", []):
                            text = word.get("text")
                            if text:
                                words.append(text)

    extracted_text = " ".join(words).strip()
    logger.info("Yandex Vision text length: %s", len(extracted_text))
    return extracted_text
