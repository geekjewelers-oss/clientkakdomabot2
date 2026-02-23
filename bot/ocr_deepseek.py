import base64
import json
import logging

import httpx

import config

logger = logging.getLogger(__name__)


_DEEPSEEK_PROMPT = (
    "You are a passport MRZ reader. Extract MRZ data from this passport image. "
    "Return ONLY valid JSON with these fields: surname, given_names, passport_number, "
    "nationality, birth_date, expiry_date, sex, country_code. No other text."
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


def deepseek_vision_extract(image_bytes: bytes) -> dict:
    if not config.DEEPSEEK_API_KEY:
        return {**{field: "" for field in _REQUIRED_FIELDS}, "confidence_score": 0.0}

    encoded = base64.b64encode(image_bytes).decode("utf-8")
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _DEEPSEEK_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}},
                ],
            }
        ],
        "temperature": 0,
    }
    headers = {
        "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=12.0) as client:
            response = client.post("https://api.deepseek.com/v1/chat/completions", json=payload, headers=headers)
            response.raise_for_status()
            body = response.json()

        content = body["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        result = {field: parsed.get(field, "") for field in _REQUIRED_FIELDS}
        result["confidence_score"] = 0.95
        return result
    except Exception as exc:
        logger.warning("deepseek_vision_extract_failed: %s", exc)
        return {**{field: "" for field in _REQUIRED_FIELDS}, "confidence_score": 0.0}
