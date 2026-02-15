import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any

import cv2
import numpy as np

import config
from bot import metrics
from bot.mrz_parser import compute_mrz_hash, extract_mrz_from_image_bytes, extract_text_from_image_bytes, parse_td3_mrz
from bot.ocr_fallback import easyocr_extract_text
from bot.ocr_quality import blur_score, build_ocr_quality_report, exposure_score
from bot.vision_fallback import yandex_vision_extract_text

logger = logging.getLogger(__name__)


def _decode_gray_image(img_bytes: bytes) -> np.ndarray | None:
    np_buf = np.frombuffer(img_bytes, dtype=np.uint8)
    img = cv2.imdecode(np_buf, cv2.IMREAD_COLOR)
    if img is None:
        return None
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return cv2.equalizeHist(gray)


def _attach_quality(result: dict[str, Any], gray: np.ndarray | None) -> dict[str, Any]:
    parsed = result.get("parsed") or {}
    if gray is None:
        quality = build_ocr_quality_report(parsed, blur=0.0, exposure=0.0)
    else:
        blur = blur_score(gray)
        exposure = exposure_score(gray)
        quality = build_ocr_quality_report(parsed, blur=blur, exposure=exposure)
    result["quality"] = quality
    return result


def _local_ocr_attempt(img_bytes: bytes, gray: np.ndarray | None) -> dict[str, Any]:
    line1, line2, mrz_text, _mode = extract_mrz_from_image_bytes(img_bytes)
    if line1 and line2:
        parsed = parse_td3_mrz(line1, line2)
        checksum_ok = parsed.get("_mrz_checksum_ok", False)
        confidence = "high" if checksum_ok else "medium"
        parsed["mrz_confidence_score"] = 0.9 if checksum_ok else 0.6
        text_value = mrz_text or ""
        logger.info("[OCR] OCR stage: mrz, text_len=%s", len(text_value))
        return _attach_quality({
            "text": text_value,
            "source": "mrz",
            "confidence": confidence,
            "parsed": parsed,
            "mrz_lines": (line1, line2),
        }, gray)

    text = extract_text_from_image_bytes(img_bytes)
    logger.info("[OCR] OCR stage: tesseract, text_len=%s", len(text or ""))

    easy_text = easyocr_extract_text(img_bytes)
    logger.info("[OCR] OCR stage: easyocr, text_len=%s", len(easy_text or ""))
    if easy_text and len(easy_text) > 40:
        return _attach_quality({
            "text": easy_text,
            "source": "easyocr",
            "confidence": "medium",
            "parsed": {},
            "mrz_lines": None,
        }, gray)

    return _attach_quality({
        "text": easy_text or text or "",
        "source": "tesseract",
        "confidence": "low",
        "parsed": {},
        "mrz_lines": None,
    }, gray)


def _fallback_ocr_attempt(img_bytes: bytes, current_text: str) -> str:
    if len((current_text or "").strip()) >= 60:
        return current_text
    return yandex_vision_extract_text(img_bytes)


def _run_fallback_with_timeout(img_bytes: bytes, current_text: str) -> tuple[str, bool]:
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_fallback_ocr_attempt, img_bytes, current_text)
        try:
            text = future.result(timeout=config.OCR_SLA_FALLBACK_TIMEOUT_SECONDS)
            logger.info("[OCR] OCR stage: vision, text_len=%s", len(text or ""))
            return text or "", False
        except FutureTimeoutError:
            logger.warning("[OCR] Vision fallback timeout after %ss", config.OCR_SLA_FALLBACK_TIMEOUT_SECONDS)
            return current_text or "", True


def _build_retry_reason_flags(
    quality: dict[str, Any],
    confidence: float,
    timeout_flag: bool,
    fallback_used: bool,
) -> dict[str, bool]:
    return {
        "blur_bad": bool(quality.get("blur_bad", False)),
        "exposure_bad": float(quality.get("exposure_score", 1.0)) < 0.5,
        "checksum_fail": not bool(quality.get("checksum_ok", False)),
        "low_confidence": confidence < config.OCR_SLA_FALLBACK_THRESHOLD_CONFIDENCE,
        "timeout": timeout_flag,
        "fallback_used": fallback_used,
    }


def _soft_fail_response(
    local_count: int,
    fallback_count: int,
    started_at: float,
    timeout_flag: bool,
    used_fallback_provider: str | None,
    correlation_id: str | None,
) -> dict[str, Any]:
    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    sla_threshold_ms = int(config.OCR_SLA_TOTAL_TIMEOUT_SECONDS * 1000 * config.OCR_SLA_BREACH_THRESHOLD_RATIO)
    sla_breach = elapsed_ms >= sla_threshold_ms
    quality = build_ocr_quality_report({}, blur=0.0, exposure=0.0)
    retry_reason_flags = _build_retry_reason_flags(
        quality=quality,
        confidence=float(quality.get("confidence", 0.0)),
        timeout_flag=timeout_flag,
        fallback_used=bool(used_fallback_provider),
    )

    metrics_inc: list[str] = ["ocr.sla.soft_fail"]
    metrics.inc("ocr.sla.soft_fail")
    if used_fallback_provider:
        metrics.inc("ocr.sla.fallback_used")
        metrics_inc.append("ocr.sla.fallback_used")
    if sla_breach:
        metrics.inc("ocr.sla.breach")
        metrics_inc.append("ocr.sla.breach")

    return {
        "text": "",
        "source": "sla_soft_fail",
        "confidence": "low",
        "parsed": {},
        "mrz_lines": None,
        "quality": quality,
        "attempt_local_count": local_count,
        "attempt_fallback_count": fallback_count,
        "total_elapsed_ms": elapsed_ms,
        "decision_branch": "soft_fail",
        "used_fallback_provider": used_fallback_provider,
        "timeout_flag": timeout_flag,
        "retry_reason_flags": retry_reason_flags,
        "sla_breach": sla_breach,
        "correlation_id": correlation_id,
        "passport_hash": None,
        "passport_mrz_len": 0,
        "metrics_inc": metrics_inc,
        "logger_version": "ocr_sla_v1",
    }


def ocr_pipeline_extract(img_bytes: bytes, correlation_id: str | None = None) -> dict[str, Any]:
    started_at = time.monotonic()
    gray = _decode_gray_image(img_bytes)
    local_attempts = 0
    fallback_attempts = 0
    local_failures = 0
    last_result: dict[str, Any] = {}
    used_fallback_provider: str | None = None
    timeout_flag = False

    for _ in range(config.OCR_SLA_MAX_LOCAL_ATTEMPTS):
        local_attempts += 1
        if (time.monotonic() - started_at) > config.OCR_SLA_TOTAL_TIMEOUT_SECONDS:
            timeout_flag = True
            return _soft_fail_response(
                local_count=local_attempts,
                fallback_count=fallback_attempts,
                started_at=started_at,
                timeout_flag=timeout_flag,
                used_fallback_provider=used_fallback_provider,
                correlation_id=correlation_id,
            )

        result = _local_ocr_attempt(img_bytes, gray)
        last_result = result
        quality = result.get("quality") or {}
        parsed = result.get("parsed") or {}
        conf = float(quality.get("confidence", 0.0))

        local_failed = not parsed or bool(quality.get("needs_retry", False)) or conf < config.OCR_SLA_FALLBACK_THRESHOLD_CONFIDENCE
        if local_failed:
            local_failures += 1
        if not local_failed:
            break

    should_use_fallback = (
        local_attempts >= config.OCR_SLA_MAX_LOCAL_ATTEMPTS
        or local_failures >= config.OCR_SLA_FALLBACK_AFTER_FAILURES
    )

    if should_use_fallback:
        current_text = (last_result.get("text") if last_result else "") or ""
        for _ in range(config.OCR_SLA_FALLBACK_ATTEMPTS):
            fallback_attempts += 1
            used_fallback_provider = config.OCR_SLA_FALLBACK_PROVIDER

            if (time.monotonic() - started_at) > config.OCR_SLA_TOTAL_TIMEOUT_SECONDS:
                timeout_flag = True
                return _soft_fail_response(
                    local_count=local_attempts,
                    fallback_count=fallback_attempts,
                    started_at=started_at,
                    timeout_flag=timeout_flag,
                    used_fallback_provider=used_fallback_provider,
                    correlation_id=correlation_id,
                )

            fallback_text, fallback_timeout = _run_fallback_with_timeout(img_bytes, current_text)
            timeout_flag = timeout_flag or fallback_timeout

            if fallback_text:
                last_result = _attach_quality({
                    "text": fallback_text,
                    "source": "vision",
                    "confidence": "medium",
                    "parsed": {},
                    "mrz_lines": None,
                }, gray)
                break

    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    if elapsed_ms > config.OCR_SLA_TOTAL_TIMEOUT_SECONDS * 1000:
        timeout_flag = True

    sla_threshold_ms = int(config.OCR_SLA_TOTAL_TIMEOUT_SECONDS * 1000 * config.OCR_SLA_BREACH_THRESHOLD_RATIO)
    sla_breach = elapsed_ms >= sla_threshold_ms

    if timeout_flag:
        return _soft_fail_response(
            local_count=local_attempts,
            fallback_count=fallback_attempts,
            started_at=started_at,
            timeout_flag=timeout_flag,
            used_fallback_provider=used_fallback_provider,
            correlation_id=correlation_id,
        )

    if not last_result:
        return _soft_fail_response(
            local_count=local_attempts,
            fallback_count=fallback_attempts,
            started_at=started_at,
            timeout_flag=timeout_flag,
            used_fallback_provider=used_fallback_provider,
            correlation_id=correlation_id,
        )

    quality = last_result.get("quality") or {}
    confidence_value = float(quality.get("confidence", 0.0))
    needs_retry = bool(quality.get("needs_retry", False))

    if needs_retry:
        decision_branch = "soft_fail"
    elif confidence_value >= config.OCR_SLA_AUTO_ACCEPT_CONFIDENCE:
        decision_branch = "auto_accept"
    elif confidence_value >= config.OCR_SLA_FALLBACK_THRESHOLD_CONFIDENCE:
        decision_branch = "preview_required"
    else:
        decision_branch = "soft_fail"

    metrics_inc: list[str] = []
    if used_fallback_provider:
        metrics.inc("ocr.sla.fallback_used")
        metrics_inc.append("ocr.sla.fallback_used")
    if sla_breach:
        metrics.inc("ocr.sla.breach")
        metrics_inc.append("ocr.sla.breach")

    retry_reason_flags = _build_retry_reason_flags(
        quality=quality,
        confidence=confidence_value,
        timeout_flag=timeout_flag,
        fallback_used=bool(used_fallback_provider),
    )

    if decision_branch == "auto_accept":
        metrics.inc("ocr.sla.auto_accept")
        metrics_inc.append("ocr.sla.auto_accept")
    elif decision_branch == "soft_fail":
        metrics.inc("ocr.sla.soft_fail")
        metrics_inc.append("ocr.sla.soft_fail")

    mrz_lines = last_result.get("mrz_lines")
    line1 = mrz_lines[0] if mrz_lines else None
    line2 = mrz_lines[1] if mrz_lines else None
    passport_hash = compute_mrz_hash(line1, line2)
    passport_mrz_len = len((line1 or "").strip() + (line2 or "").strip())

    last_result.update({
        "attempt_local_count": local_attempts,
        "attempt_fallback_count": fallback_attempts,
        "total_elapsed_ms": elapsed_ms,
        "decision_branch": decision_branch,
        "used_fallback_provider": used_fallback_provider,
        "timeout_flag": timeout_flag,
        "retry_reason_flags": retry_reason_flags,
        "sla_breach": sla_breach,
        "correlation_id": correlation_id,
        "passport_hash": passport_hash,
        "passport_mrz_len": passport_mrz_len,
        "metrics_inc": metrics_inc,
        "logger_version": "ocr_sla_v1",
    })
    return last_result
