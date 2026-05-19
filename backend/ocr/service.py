from __future__ import annotations

import uuid

from backend.ocr.metrics import record_ocr_metric
from ocr_service.pipeline import run_ocr_pipeline_v2


async def process_passport_ocr(image_bytes: bytes, correlation_id: str | None = None) -> dict:
    corr = correlation_id or str(uuid.uuid4())
    result = await run_ocr_pipeline_v2(image_bytes, corr)
    record_ocr_metric(
        correlation_id=corr,
        ocr_result=result,
        intake_resident_id=None,
    )

    fields = result.get("fields") or {}

    return {
        "correlation_id": corr,
        "auto_accepted": result.get("auto_accepted", False),
        "manual_check": result.get("manual_check", True),
        "confidence_score": result.get("confidence_score", 0.0),
        "parsing_source": result.get("parsing_source", "paddle"),
        "fallback_used": result.get("parsing_source") != "paddle",
        "warnings": result.get("warnings", []),
        "sla_breach": result.get("sla_breach", False),
        "fields": fields,
        "mrz_raw": result.get("mrz", ""),
        "passport_hash": fields.get("passport_hash"),
        "surname": fields.get("surname"),
        "given_names": fields.get("given_names"),
        "date_of_birth": fields.get("date_of_birth"),
        "nationality": fields.get("nationality"),
        "passport_number": fields.get("passport_number"),
        "full_name_cyr": fields.get("full_name_cyr"),
    }
