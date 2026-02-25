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

    should_fallback = bool(config.OCR_FALLBACK_ENABLED) or not bool(paddle_result.get("auto_accepted"))
    final = paddle_result

    if should_fallback:
        fallback_raw = await _run_ocr_space(image_bytes, corr)
        if fallback_raw:
            fallback_text = _extract_ocr_space_text(fallback_raw)
            fallback_result = _build_result_from_text(
                text=fallback_text,
                mrz_text=fallback_text,
                avg_confidence=float(paddle_full.get("avg_confidence") or 0.0),
                source="ocr_space",
                correlation_id=corr,
            )
            if fallback_result.get("auto_accepted") or not paddle_result.get("auto_accepted"):
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
