from __future__ import annotations

from pydantic import BaseModel


class OCRFieldsResult(BaseModel):
    surname: str = ""
    given_names: str = ""
    date_of_birth: str = ""
    nationality: str = ""
    passport_number: str = ""
    passport_hash: str | None = None
    full_name_cyr: str = ""


class OCRPassportResult(BaseModel):
    correlation_id: str
    auto_accepted: bool
    manual_check: bool
    confidence_score: float
    parsing_source: str
    fallback_used: bool
    warnings: list[str]
    sla_breach: bool
    mrz_raw: str
    fields: OCRFieldsResult
