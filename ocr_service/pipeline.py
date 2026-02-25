from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
import uuid
from typing import Any

import requests

import config
from bot.vision_fallback import yandex_vision_extract_text
from .mrz_parser import MRZParser
from .paddle_engine import PaddleEngine

try:
    import structlog
except ModuleNotFoundError:  # pragma: no cover
    structlog = None


logger = structlog.get_logger("ocr_pipeline_v2") if structlog else logging.getLogger("ocr_pipeline_v2")


def _empty_result(correlation_id: str) -> dict[str, Any]:
    return {
        "success": False,
        "manual_check": True,
        "confidence_score": 0.0,
        "parsing_source": "paddle",
        "fields": {
            "surname": "",
            "given_names": "",
            "date_of_birth": "",
            "nationality": "",
            "passport_number": "",
            "passport_hash": "",
            "full_name_cyr": "",
        },
        "mrz": "",
        "warnings": ["mrz_not_found"],
        "auto_accepted": False,
        "correlation_id": correlation_id,
        "sla_breach": False,
    }


def _normalize_name(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("<", " ").strip().upper())


def _normalize_passport(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def _yyMMdd_to_iso(value: str | None) -> str:
    if not value or len(value) != 6 or not value.isdigit():
        return ""
    yy = int(value[:2])
    mm = value[2:4]
    dd = value[4:6]
    year = 1900 + yy if yy > 30 else 2000 + yy
    return f"{year:04d}-{mm}-{dd}"


def _extract_full_page_fields(text: str) -> dict[str, str]:
    clean = text or ""
    compact = re.sub(r"\s+", " ", clean)

    passport_match = re.search(r"\b([A-Z0-9]{6,9})\b", compact.upper())
    date_match = re.search(r"\b(\d{4}-\d{2}-\d{2}|\d{2}[./-]\d{2}[./-]\d{4})\b", compact)
    cyr_name_match = re.search(r"\b[А-ЯЁ]{2,}(?:\s+[А-ЯЁ]{2,}){1,2}\b", compact)

    surname_guess = ""
    given_guess = ""
    latin_name = re.search(r"\b([A-Z]{2,})\s+([A-Z]{2,}(?:\s+[A-Z]{2,})*)\b", compact.upper())
    if latin_name:
        surname_guess = latin_name.group(1)
        given_guess = latin_name.group(2)

    dob = ""
    if date_match:
        raw = date_match.group(1)
        if "-" in raw and len(raw) == 10:
            dob = raw
        else:
            d, m, y = re.split(r"[./-]", raw)
            dob = f"{y}-{m}-{d}"

    return {
        "surname": surname_guess,
        "given_names": given_guess,
        "date_of_birth": dob,
        "passport_number": passport_match.group(1) if passport_match else "",
        "full_name_cyr": cyr_name_match.group(0) if cyr_name_match else "",
    }


def _cross_validate(mrz_fields: dict[str, str], full_fields: dict[str, str]) -> bool:
    comparisons = [
        (_normalize_name(mrz_fields.get("surname")), _normalize_name(full_fields.get("surname"))),
        (_normalize_name(mrz_fields.get("given_names")), _normalize_name(full_fields.get("given_names"))),
        (_normalize_passport(mrz_fields.get("passport_number")), _normalize_passport(full_fields.get("passport_number"))),
        ((mrz_fields.get("date_of_birth") or ""), (full_fields.get("date_of_birth") or "")),
    ]
    return all(left and right and left == right for left, right in comparisons)


def _extract_ocr_space_text(data: dict[str, Any]) -> str:
    blocks = data.get("ParsedResults") or []
    lines: list[str] = []
    for block in blocks:
        text = (block or {}).get("ParsedText")
        if text:
            lines.append(text)
    return "\n".join(lines)


async def _run_ocr_space(image_bytes: bytes, correlation_id: str) -> dict[str, Any] | None:
    if not config.OCR_SPACE_API_KEY:
        logger.warning("ocr_space_skipped_no_api_key", correlation_id=correlation_id)
        return None

    def _post() -> dict[str, Any]:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        response = requests.post(
            "https://api.ocr.space/parse/image",
            data={
                "apikey": config.OCR_SPACE_API_KEY,
                "language": "rus+eng",
                "isOverlayRequired": "false",
                "base64Image": f"data:image/jpeg;base64,{b64}",
            },
            timeout=15,
        )
        response.raise_for_status()
        return response.json()

    try:
        return await asyncio.to_thread(_post)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ocr_space_failed", correlation_id=correlation_id, error=str(exc))
        return None


def _extract_azapi_text(data: dict[str, Any]) -> str:
    if not isinstance(data, dict):
        return ""
    candidate_keys = ["text", "ocr_text", "result", "raw_text", "full_text"]
    for key in candidate_keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value
    nested = data.get("data")
    if isinstance(nested, dict):
        for key in candidate_keys:
            value = nested.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return ""


async def _run_azapi(image_bytes: bytes, correlation_id: str) -> dict[str, Any] | None:
    if not bool(getattr(config, "AZAPI_ENABLED", True)):
        logger.info("azapi_skipped_disabled", correlation_id=correlation_id)
        return None
    if not getattr(config, "AZAPI_API_KEY", ""):
        logger.warning("azapi_skipped_no_api_key", correlation_id=correlation_id)
        return None

    # NOTE: endpoint may change; замени на актуальный из дашборда.
    endpoint = "https://api.azapi.ai/v1/ocr/passport"

    def _post() -> dict[str, Any]:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        payload = {"image_base64": b64}

        headers_variants = [
            {"X-API-Key": config.AZAPI_API_KEY, "Content-Type": "application/json"},
            {"Authorization": f"Bearer {config.AZAPI_API_KEY}", "Content-Type": "application/json"},
        ]

        last_error: Exception | None = None
        for headers in headers_variants:
            try:
                response = requests.post(endpoint, json=payload, headers=headers, timeout=15)
                response.raise_for_status()
                return response.json()
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        if last_error:
            raise last_error
        raise RuntimeError("azapi_request_failed")

    try:
        return await asyncio.to_thread(_post)
    except Exception as exc:  # noqa: BLE001
        logger.warning("azapi_failed", correlation_id=correlation_id, error=str(exc))
        return None


async def _run_yandex_vision(image_bytes: bytes, correlation_id: str) -> str:
    try:
        return await asyncio.to_thread(yandex_vision_extract_text, image_bytes)
    except Exception as exc:  # noqa: BLE001
        logger.warning("yandex_vision_failed", correlation_id=correlation_id, error=str(exc))
        return ""


async def try_fallback_chain(image_bytes: bytes, correlation_id: str) -> dict[str, Any] | None:
    fallback_steps = ["ocr_space", "azapi", "yandex_vision"]
    for provider in fallback_steps:
        if provider == "ocr_space":
            raw_data = await _run_ocr_space(image_bytes, correlation_id)
            text = _extract_ocr_space_text(raw_data or {}) if raw_data else ""
        elif provider == "azapi":
            raw_data = await _run_azapi(image_bytes, correlation_id)
            text = _extract_azapi_text(raw_data or {}) if raw_data else ""
        else:
            text = await _run_yandex_vision(image_bytes, correlation_id)

        if not text.strip():
            logger.info("fallback_provider_no_text", correlation_id=correlation_id, provider=provider)
            continue

        result = _build_result_from_text(
            text=text,
            mrz_text=text,
            avg_confidence=float(config.MIN_CONFIDENCE),
            source=provider,
            correlation_id=correlation_id,
        )
        if result.get("auto_accepted"):
            logger.info("fallback_provider_success", correlation_id=correlation_id, provider=provider)
            return result

        logger.info("fallback_provider_rejected", correlation_id=correlation_id, provider=provider)

    return None


def _build_result_from_text(*, text: str, mrz_text: str, avg_confidence: float, source: str, correlation_id: str) -> dict[str, Any]:
    parser = MRZParser()
    result = _empty_result(correlation_id)
    result["parsing_source"] = source
    result["confidence_score"] = float(avg_confidence)

    detected = parser.detect_td3_lines(mrz_text or text)
    if not detected:
        result["warnings"] = ["mrz_not_found"]
        return result

    line1, line2 = detected
    validation = parser.parse_td3(line1, line2)

    passport_number = validation.line2[0:9].replace("<", "")
    mrz_fields = {
        "surname": validation.parsed.surname or "",
        "given_names": validation.parsed.given_names or "",
        "date_of_birth": _yyMMdd_to_iso(validation.parsed.birth_date),
        "nationality": validation.parsed.nationality or "",
        "passport_number": passport_number,
        "passport_hash": validation.parsed.passport_hash or "",
    }
    full_page_fields = _extract_full_page_fields(text)
    cross_ok = _cross_validate(mrz_fields, full_page_fields)

    warnings: list[str] = []
    if avg_confidence < float(config.MIN_CONFIDENCE):
        warnings.append("low_confidence")
    if not validation.all_three_ok:
        warnings.append("checksum_failed")
    if not cross_ok:
        warnings.append("cross_validation_failed")

    full_name_cyr = full_page_fields.get("full_name_cyr") or ""
    mrz_fields["full_name_cyr"] = full_name_cyr

    accepted = avg_confidence >= float(config.MIN_CONFIDENCE) and validation.all_three_ok and cross_ok
    result.update(
        {
            "success": True,
            "manual_check": not accepted,
            "fields": mrz_fields,
            "mrz": f"{validation.line1}\n{validation.line2}",
            "warnings": warnings,
            "auto_accepted": accepted,
        }
    )
    return result


async def run_ocr_pipeline_v2(image_bytes: bytes, correlation_id: str | None = None) -> dict[str, Any]:
    corr = correlation_id or str(uuid.uuid4())
    start = time.perf_counter()

    paddle_engine = PaddleEngine(min_confidence=float(config.MIN_CONFIDENCE))
    paddle_full = await asyncio.to_thread(paddle_engine.full_page, image_bytes)
    paddle_mrz = await asyncio.to_thread(paddle_engine.mrz_crop, image_bytes)

    paddle_result = _build_result_from_text(
        text=str(paddle_full.get("text") or ""),
        mrz_text=str(paddle_mrz.get("text") or ""),
        avg_confidence=float(paddle_full.get("avg_confidence") or 0.0),
        source="paddle",
        correlation_id=corr,
    )

    final = paddle_result

    if bool(config.OCR_FALLBACK_ENABLED) and not bool(paddle_result.get("auto_accepted")):
        fallback_result = await try_fallback_chain(image_bytes, corr)
        if fallback_result:
            final = fallback_result

    elapsed = time.perf_counter() - start
    final["sla_breach"] = elapsed > float(getattr(config, "OCR_SLA_TOTAL_TIMEOUT_SECONDS", 8))
    final["correlation_id"] = corr

    logger.info(
        "ocr_pipeline_v2_done",
        correlation_id=corr,
        parsing_source=final.get("parsing_source"),
        confidence=float(final.get("confidence_score", 0.0)),
        auto_accepted=bool(final.get("auto_accepted")),
        manual_check=bool(final.get("manual_check")),
        elapsed_ms=int(elapsed * 1000),
        warnings=final.get("warnings", []),
    )
    return final
