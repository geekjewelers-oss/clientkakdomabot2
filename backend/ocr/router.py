from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from backend.ocr.metrics import get_metrics_summary
from backend.ocr.schemas import OCRPassportResult
from backend.ocr.service import process_passport_ocr

router = APIRouter(tags=["ocr"])

ALLOWED_MIME_TYPES = {"image/jpeg", "image/png"}
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024


@router.get("/health")
def ocr_health() -> dict[str, str]:
    return {"status": "ok", "engine": "paddle+fallback"}


@router.post("/passport", response_model=OCRPassportResult)
async def ocr_passport(
    file: UploadFile = File(...),
    correlation_id: str | None = Query(default=None),
) -> OCRPassportResult:
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(status_code=400, detail="Only image/jpeg and image/png are supported")

    image_bytes = await file.read()
    if len(image_bytes) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="File size exceeds 10MB limit")

    try:
        result = await process_passport_ocr(image_bytes=image_bytes, correlation_id=correlation_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"OCR processing failed: {exc}") from exc

    return OCRPassportResult(
        correlation_id=result["correlation_id"],
        auto_accepted=result["auto_accepted"],
        manual_check=result["manual_check"],
        confidence_score=result["confidence_score"],
        parsing_source=result["parsing_source"],
        fallback_used=result["fallback_used"],
        warnings=result["warnings"],
        sla_breach=result["sla_breach"],
        mrz_raw=result["mrz_raw"],
        fields=result.get("fields", {}),
    )


@router.get("/metrics/summary")
def ocr_metrics_summary() -> dict[str, Any]:
    # internal, add auth in WAVE 8
    return get_metrics_summary()
