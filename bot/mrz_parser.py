import asyncio
import hashlib
import io
import logging
import os
import re
import uuid
from typing import Any

import aiohttp
import cv2
import numpy as np
import pytesseract
from PIL import Image

logger = logging.getLogger(__name__)

MRZ_REGEX = re.compile(r"([A-Z0-9<]{20,})\s*[\n\r]+([A-Z0-9<]{20,})", re.MULTILINE)
_CHECKSUM_WEIGHTS = (7, 3, 1)
NUM_MAP = {"O": "0", "Q": "0", "I": "1", "L": "1", "B": "8", "S": "5", "G": "6"}


async def run_ocr_pipeline(image_bytes: bytes, correlation_id: str | None = None) -> dict[str, Any]:
    correlation_id = correlation_id or str(uuid.uuid4())
    base_result: dict[str, Any] = {
        "fields": {},
        "confidence_score": 0.0,
        "parsing_source": "MRZ_local",
        "auto_accepted": False,
        "sla_breach": False,
        "correlation_id": correlation_id,
    }

    try:
        return await asyncio.wait_for(_run_ocr_pipeline_impl(image_bytes, correlation_id), timeout=8)
    except asyncio.TimeoutError:
        result = {**base_result, "sla_breach": True}
        _log_pipeline_result(result, passport_hash=None)
        return result


async def _run_ocr_pipeline_impl(image_bytes: bytes, correlation_id: str) -> dict[str, Any]:
    best_result: dict[str, Any] = {
        "fields": {},
        "confidence_score": 0.0,
        "parsing_source": "MRZ_local",
        "auto_accepted": False,
        "sla_breach": False,
        "correlation_id": correlation_id,
    }

    for _ in range(2):
        local_result = await _run_local_ocr_attempt(image_bytes, correlation_id)
        if local_result["confidence_score"] > best_result["confidence_score"]:
            best_result = local_result
        if local_result["confidence_score"] >= 0.55:
            _log_pipeline_result(local_result, local_result["fields"].get("passport_hash"))
            return local_result

    fallback = await _run_yandex_fallback(image_bytes, correlation_id)
    if fallback and fallback["confidence_score"] >= best_result["confidence_score"]:
        _log_pipeline_result(fallback, fallback["fields"].get("passport_hash"))
        return fallback

    best_result["sla_breach"] = True
    _log_pipeline_result(best_result, best_result["fields"].get("passport_hash"))
    return best_result


async def _run_local_ocr_attempt(image_bytes: bytes, correlation_id: str) -> dict[str, Any]:
    text = await asyncio.to_thread(extract_text_from_image_bytes, image_bytes)
    line1, line2 = find_mrz_from_text(text)
    return _build_result_from_lines(line1, line2, correlation_id, parsing_source="MRZ_local")


async def _run_yandex_fallback(image_bytes: bytes, correlation_id: str) -> dict[str, Any] | None:
    api_key = os.getenv("YANDEX_VISION_KEY", "").strip()
    if not api_key:
        return None

    payload = {
        "analyze_specs": [
            {
                "content": base64_encode(image_bytes),
                "features": [{"type": "TEXT_DETECTION", "text_detection_config": {"language_codes": ["en"]}}],
            }
        ]
    }

    headers = {"Authorization": f"Api-Key {api_key}"}
    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                "https://vision.api.cloud.yandex.net/vision/v1/batchAnalyze",
                json=payload,
                headers=headers,
            ) as response:
                response.raise_for_status()
                body = await response.json()
    except Exception as exc:
        logger.warning("{\"event\":\"yandex_fallback_failed\",\"correlation_id\":\"%s\",\"error\":\"%s\"}", correlation_id, exc)
        return None

    text = "\n".join(_extract_text_blocks(body))
    line1, line2 = find_mrz_from_text(text)
    return _build_result_from_lines(line1, line2, correlation_id, parsing_source="cloud_yandex")


def _extract_text_blocks(payload: Any) -> list[str]:
    values: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"text", "fullText"} and isinstance(value, str):
                    values.append(value)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return [v for v in values if v.strip()]


def _build_result_from_lines(line1: str | None, line2: str | None, correlation_id: str, parsing_source: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "fields": {},
        "confidence_score": 0.0,
        "parsing_source": parsing_source,
        "auto_accepted": False,
        "sla_breach": False,
        "correlation_id": correlation_id,
    }
    if not line1 or not line2:
        return result

    fields = parse_td3_mrz(line1, line2)
    confidence = float(fields.get("mrz_confidence_score", 0.0))
    result["fields"] = fields
    result["confidence_score"] = confidence
    result["auto_accepted"] = confidence >= 0.80
    return result


def _log_pipeline_result(result: dict[str, Any], passport_hash: str | None) -> None:
    logger.info(
        "{\"event\":\"ocr_pipeline_result\",\"correlation_id\":\"%s\",\"confidence_score\":%.2f,\"parsing_source\":\"%s\",\"sla_breach\":%s,\"auto_accepted\":%s,\"passport_hash\":\"%s\"}",
        result.get("correlation_id"),
        result.get("confidence_score", 0.0),
        result.get("parsing_source", ""),
        result.get("sla_breach", False),
        result.get("auto_accepted", False),
        passport_hash or "",
    )


def compute_mrz_hash(line1: str | None, line2: str | None) -> str | None:
    l1 = (line1 or "").strip()
    l2 = (line2 or "").strip()
    if not l1 and not l2:
        return None
    value = f"{l1}{l2}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest().lower()[:16]


def image_bytes_to_pil(img_bytes):
    return Image.open(io.BytesIO(img_bytes))


def preprocess_for_mrz_cv(image: Image.Image):
    return preprocess_for_mrz_cv_mode(image, mode="current")


def preprocess_for_mrz_cv_mode(image: Image.Image, mode: str = "current"):
    img = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)

    if mode == "adaptive":
        return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 2)

    if mode == "morphology":
        _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        return cv2.morphologyEx(th, cv2.MORPH_CLOSE, kernel)

    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return th


def extract_text_from_image_bytes(img_bytes):
    pil = image_bytes_to_pil(img_bytes)
    return pytesseract.image_to_string(pil, lang="eng")


def find_mrz_from_text(text):
    candidates = MRZ_REGEX.findall(text.replace(" ", "").replace("\r", "\n"))
    if candidates:
        for l1, l2 in candidates:
            if len(l1) >= 30 and len(l2) >= 30:
                return l1.strip(), l2.strip()

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for i in range(len(lines) - 1):
        a, b = lines[i], lines[i + 1]
        if a.count("<") >= 3 and b.count("<") >= 3 and len(a) >= 25 and len(b) >= 25:
            return a.replace(" ", ""), b.replace(" ", "")
    return None, None


def _mrz_char_value(ch: str) -> int:
    if ch.isdigit():
        return int(ch)
    if "A" <= ch <= "Z":
        return ord(ch) - ord("A") + 10
    if ch == "<":
        return 0
    return 0


def compute_mrz_checksum(value: str) -> int:
    total = 0
    for idx, ch in enumerate(value):
        total += _mrz_char_value(ch) * _CHECKSUM_WEIGHTS[idx % 3]
    return total % 10


def normalize_for_numeric(s: str) -> str:
    s = s.upper()
    return "".join(NUM_MAP.get(ch, ch) for ch in s)


def validate_mrz_checksum(value: str, check_char: str) -> bool:
    if not check_char or not check_char.isdigit():
        return False
    return compute_mrz_checksum(value) == int(check_char)


def validate_td3_composite(l2: str) -> bool:
    if len(l2) < 44:
        l2 = l2 + "<" * (44 - len(l2))

    composite_check = l2[43]
    part_doc = normalize_for_numeric(l2[0:10])
    part_birth = normalize_for_numeric(l2[13:20])
    part_exp = normalize_for_numeric(l2[21:28])
    optional = l2[28:43]
    composite_value = part_doc + part_birth + part_exp + optional
    return validate_mrz_checksum(composite_value, composite_check)


def parse_td3_mrz(line1: str, line2: str):
    l1 = line1 + "<" * (44 - len(line1)) if len(line1) < 44 else line1
    l2 = line2 + "<" * (44 - len(line2)) if len(line2) < 44 else line2
    data = {}
    checks = {}
    try:
        data["document_type"] = l1[0]
        data["issuing_country"] = l1[2:5]
        names = l1[5:44].split("<<")
        data["surname"] = names[0].replace("<", " ").strip()
        data["given_names"] = names[1].replace("<", " ").strip() if len(names) > 1 else ""

        passport_number_raw = l2[0:9]
        passport_check = l2[9]
        birth_date_raw = l2[13:19]
        birth_check = l2[19]
        expiry_raw = l2[21:27]
        expiry_check = l2[27]

        passport_number_norm = normalize_for_numeric(passport_number_raw)
        birth_date_norm = normalize_for_numeric(birth_date_raw)
        expiry_norm = normalize_for_numeric(expiry_raw)

        data["passport_number"] = passport_number_raw.replace("<", "").strip()
        data["passport_number_check"] = passport_check
        data["nationality"] = l2[10:13].replace("<", "").strip()
        data["date_of_birth"] = f"{birth_date_raw[0:2]}{birth_date_raw[2:4]}{birth_date_raw[4:6]}"
        data["birth_date"] = data["date_of_birth"]
        data["sex"] = l2[20]
        data["expiry_date"] = f"{expiry_raw[0:2]}{expiry_raw[2:4]}{expiry_raw[4:6]}"

        checks["passport_number"] = validate_mrz_checksum(passport_number_norm, passport_check)
        checks["birth_date"] = validate_mrz_checksum(birth_date_norm, birth_check)
        checks["expiry_date"] = validate_mrz_checksum(expiry_norm, expiry_check)
        checks["composite"] = validate_td3_composite(l2)
        data["passport_hash"] = compute_mrz_hash(line1, line2)
    except Exception as exc:
        logger.exception("[OCR] error parsing mrz: %s", exc)
        checks = {"passport_number": False, "birth_date": False, "expiry_date": False, "composite": False}

    check_weights = {"passport_number": 0.25, "birth_date": 0.25, "expiry_date": 0.25, "composite": 0.25}
    mrz_confidence_score = sum(weight for key, weight in check_weights.items() if checks.get(key))
    data["_mrz_checksum_ok"] = all(checks.get(key, False) for key in check_weights)
    data["mrz_confidence_score"] = float(mrz_confidence_score)
    return data


def base64_encode(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode("utf-8")
