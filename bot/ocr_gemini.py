import base64
import json
import logging
import re

import httpx

import config

logger = logging.getLogger(__name__)

_GEMINI_PROMPT = (
    "You are a passport MRZ reader. Extract MRZ data from this passport image. "
    "Return ONLY valid JSON with these fields: surname, given_names, "
    "passport_number, nationality, birth_date, expiry_date, sex, country_code. "
    "No other text, no markdown, no explanation."
)

_REQUIRED_FIELDS = [
    "surname",
    "given_names",
    "passport_number",
    "nationality",
    "birth_date",
    "expiry_date",
    "sex",
    "country_code",
]


def gemini_vision_extract(image_bytes: bytes) -> dict:
    logger.info("GEMINI_CALLED: starting vision extract")
    logger.info("GEMINI_KEY_SET: %s", bool(config.GEMINI_API_KEY))
    if not config.GEMINI_API_KEY:
        return {**{field: "" for field in _REQUIRED_FIELDS}, "confidence_score": 0.0}

    encoded = base64.b64encode(image_bytes).decode("utf-8")
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.0-flash:generateContent?key={config.GEMINI_API_KEY}"
    )
    logger.info("GEMINI_URL: %s", url.split("?")[0])
    payload = {
        "contents": [{
            "parts": [
                {"text": _GEMINI_PROMPT},
                {"inline_data": {
                    "mime_type": "image/jpeg",
                    "data": encoded,
                }},
            ],
        }],
    }

    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            body = response.json()

        content = body["candidates"][0]["content"]["parts"][0]["text"]
        logger.info("gemini_raw_response: %s", content[:200])
        # Убираем markdown обёртку разными способами
        content = content.strip()
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        # Ищем JSON объект если есть лишний текст
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            content = json_match.group()
        parsed = json.loads(content)
        result = {field: parsed.get(field, "") for field in _REQUIRED_FIELDS}
        result["confidence_score"] = 0.95
        return result
    except Exception as exc:
        logger.warning("gemini_vision_extract_failed: %s", exc, exc_info=True)
        return {**{field: "" for field in _REQUIRED_FIELDS}, "confidence_score": 0.0}
