from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BitrixTenantCredentials(BaseModel):
    """Per-tenant Bitrix webhook credentials."""

    tenant_id: str = Field(min_length=1)
    webhook_base_url: str = Field(min_length=1)


class OCRBitrixFields(BaseModel):
    """Strict OCR -> Bitrix UF_* mapping payload."""

    passport_hash: str = Field(min_length=1)
    nationality: str | None = None
    birth_date: str | None = None
    doc_expiry: str | None = None
    ocr_confidence: float = Field(ge=0.0, le=1.0)
    duplicate_flag: bool = False

    model_config = ConfigDict(extra="forbid")

    @field_validator("passport_hash")
    @classmethod
    def ensure_no_raw_passport_number(cls, value: str) -> str:
        if value.isdigit() and len(value) >= 6:
            raise ValueError("passport_hash must be hashed, not raw passport number")
        return value

    def to_bitrix_uf_fields(self) -> dict[str, Any]:
        return {
            "UF_PASSPORT_HASH": self.passport_hash,
            "UF_NATIONALITY": self.nationality,
            "UF_BIRTH_DATE": self.birth_date,
            "UF_DOC_EXPIRY": self.doc_expiry,
            "UF_OCR_CONFIDENCE": self.ocr_confidence,
            "UF_DUPLICATE_FLAG": "Y" if self.duplicate_flag else "N",
        }


class ResidentData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=8)
    idempotency_key: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    ocr: OCRBitrixFields


class DealData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=8)
    idempotency_key: str | None = None
    title: str = Field(min_length=1)
    amount: float | None = None
    currency: str = "RUB"
    stage_id: str | None = None
    contact_id: int | None = None
    ocr: OCRBitrixFields
