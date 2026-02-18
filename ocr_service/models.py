from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class JobStatus(str, Enum):
    submitted = "submitted"
    processing = "processing"
    auto_accepted = "auto_accepted"
    fallback_used = "fallback_used"
    manual_review = "manual_review"
    duplicate_detected = "duplicate_detected"
    failed = "failed"


class SubmitOCRRequest(BaseModel):
    media_url: HttpUrl
    correlation_id: str = Field(min_length=8)


class SubmitOCRResponse(BaseModel):
    job_id: str


class ManualReviewRequest(BaseModel):
    correlation_id: str = Field(min_length=8)
    corrections: dict[str, Any]


class MRZData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    document_type: str | None = None
    issuing_country: str | None = None
    surname: str | None = None
    given_names: str | None = None
    passport_hash: str | None = None
    nationality: str | None = None
    birth_date: str | None = None
    sex: str | None = None
    expiry_date: str | None = None
    checksum_ok: bool = False
    confidence: float = 0.0
    format: str | None = None


class OCRQuality(BaseModel):
    blur_score: float
    exposure_score: float
    lighting_ok: bool
    normalized_confidence: float


class OCRResult(BaseModel):
    quality: OCRQuality
    mrz: MRZData
    text: str = ""
    duplicate_detected: bool = False


class JobRecord(BaseModel):
    job_id: str = Field(default_factory=lambda: str(uuid4()))
    media_url: str
    correlation_id: str
    status: JobStatus = JobStatus.submitted
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    cycle_count: int = 0
    result: OCRResult | None = None
    audit_trail: list[dict[str, Any]] = Field(default_factory=list)
    content_hash: str | None = None


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    correlation_id: str
    cycle_count: int
    result: OCRResult | None = None
    audit_trail: list[dict[str, Any]]


class WebhookResult(BaseModel):
    job_id: str
    correlation_id: str
    status: JobStatus
    payload: dict[str, Any]
