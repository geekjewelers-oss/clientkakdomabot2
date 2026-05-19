from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import uuid


@dataclass
class OCRRunMetric:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    intake_resident_id: str | None = None
    correlation_id: str = ""
    primary_engine: str = "paddle"
    primary_confidence: float = 0.0
    checksums_passed: bool = False
    cross_validation_passed: bool = False
    auto_accepted: bool = False
    fallback_stage_reached: str | None = None
    final_source: str = "paddle"
    manual_check: bool = False
    manual_review_outcome: str | None = None
    sla_breach: bool = False
    warnings: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))


_metrics_store: list[OCRRunMetric] = []


def record_ocr_metric(
    *,
    correlation_id: str,
    ocr_result: dict[str, Any],
    intake_resident_id: str | None = None,
) -> OCRRunMetric:
    """Записать метрику после OCR run. Вызывать из backend/ocr/service.py."""
    parsing_source = ocr_result.get("parsing_source", "paddle")
    fallback_stage = parsing_source if parsing_source != "paddle" else None
    warnings = ocr_result.get("warnings", [])

    metric = OCRRunMetric(
        intake_resident_id=intake_resident_id,
        correlation_id=correlation_id,
        primary_engine="paddle",
        primary_confidence=float(ocr_result.get("confidence_score") or 0.0),
        checksums_passed="checksum_failed" not in warnings,
        cross_validation_passed="cross_validation_failed" not in warnings,
        auto_accepted=bool(ocr_result.get("auto_accepted")),
        fallback_stage_reached=fallback_stage,
        final_source=parsing_source,
        manual_check=bool(ocr_result.get("manual_check")),
        sla_breach=bool(ocr_result.get("sla_breach")),
        warnings=warnings,
    )
    _metrics_store.append(metric)
    return metric


def get_metrics_summary() -> dict[str, Any]:
    """Агрегированная сводка для OCR quality dashboard."""
    total = len(_metrics_store)
    if total == 0:
        return {"total": 0}

    auto_accepted = sum(1 for m in _metrics_store if m.auto_accepted)
    manual_check = sum(1 for m in _metrics_store if m.manual_check)
    fallback_used = sum(1 for m in _metrics_store if m.fallback_stage_reached is not None)
    by_source: dict[str, int] = {}
    for m in _metrics_store:
        by_source[m.final_source] = by_source.get(m.final_source, 0) + 1

    return {
        "total": total,
        "auto_accepted_rate": round(auto_accepted / total, 3),
        "manual_check_rate": round(manual_check / total, 3),
        "fallback_rate": round(fallback_used / total, 3),
        "by_final_source": by_source,
    }
