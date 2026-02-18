from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class ChecklistBaseModel(BaseModel):
    """Base model with strict validation and forbidden unknown fields."""

    model_config = ConfigDict(extra="forbid", strict=True)


class DocumentSource(str, Enum):
    OCR = "ocr"
    MANUAL = "manual"
    MANAGER_OVERRIDE = "manager_override"


class ResidentDocument(ChecklistBaseModel):
    resident_id: str
    doc_type: str
    country_code: str = Field(min_length=2, max_length=3)
    document_url: HttpUrl
    passport_hash: str = Field(min_length=3)
    mrz_hash: str = Field(min_length=3)
    extracted_fields: dict[str, Any]
    ocr_confidence: float = Field(ge=0.0, le=1.0)
    verified_flag: bool = False
    source: DocumentSource


class ChecklistItem(ChecklistBaseModel):
    code: str
    description: str
    required: bool
    satisfied: bool = False
    satisfied_by_doc_type: str | None = None
    blocking: bool = False


class DecisionTraceEntry(ChecklistBaseModel):
    rule: str
    input: dict[str, Any]
    decision: str
    timestamp: datetime


class ChecklistResult(ChecklistBaseModel):
    all_required_satisfied: bool
    blocking_items: list[ChecklistItem]
    satisfied_items: list[ChecklistItem]
    missing_items: list[ChecklistItem]
    manager_override_used: bool = False
    decision_trace: list[DecisionTraceEntry]


class ResidentProfile(ChecklistBaseModel):
    resident_id: str
    nationality: str


class MultiResidentDeal(ChecklistBaseModel):
    deal_id: str
    residents: list[ResidentProfile]
    checklist_per_resident: dict[str, ChecklistResult]
    global_blocking_flag: bool


class OverrideRequest(ChecklistBaseModel):
    manager_role: str
    override_reason: str = Field(min_length=3)


FSMStatus = Literal["OK", "BLOCKED", "NEED_MANAGER"]
