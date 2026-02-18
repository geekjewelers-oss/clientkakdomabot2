from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

from .audit import AuditLogger
from .crm_blocker import CRMStageBlocker
from .exceptions import ChecklistBlockingError, DuplicateDocumentError
from .models import (
    ChecklistItem,
    ChecklistResult,
    DecisionTraceEntry,
    OverrideRequest,
    ResidentDocument,
    ResidentProfile,
)
from .rules import NationalityRuleRegistry


class ChecklistEngineSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="forbid")

    confidence_threshold: float = 0.80
    expiry_grace_days: int = 0
    allow_manual_override: bool = True


@dataclass
class ChecklistEngine:
    rules_registry: NationalityRuleRegistry
    settings: ChecklistEngineSettings
    audit_logger: AuditLogger
    checklist_version: str = "v1"

    def build_checklist(self, resident_profile: ResidentProfile) -> list[ChecklistItem]:
        required_docs = self.rules_registry.resolve_required_docs(resident_profile.nationality)
        return [
            ChecklistItem(
                code=f"doc::{doc_type}",
                description=f"Resident must provide {doc_type}",
                required=True,
            )
            for doc_type in required_docs
        ]

    def evaluate_checklist(
        self,
        *,
        resident_docs: list[ResidentDocument],
        checklist: list[ChecklistItem],
        override_request: OverrideRequest | None = None,
    ) -> ChecklistResult:
        decision_trace: list[DecisionTraceEntry] = []
        docs_by_type = {doc.doc_type: doc for doc in resident_docs}
        duplicate_hashes = [h for h, c in Counter(doc.passport_hash for doc in resident_docs).items() if c > 1]
        if duplicate_hashes:
            decision_trace.append(self._trace("duplicate_passport_hash", {"passport_hash": duplicate_hashes[0]}, "blocking"))
            raise DuplicateDocumentError(f"Duplicate passport hash detected: {duplicate_hashes[0]}")

        blocking_items: list[ChecklistItem] = []
        satisfied_items: list[ChecklistItem] = []
        missing_items: list[ChecklistItem] = []

        for item in checklist:
            doc_type = item.code.split("::", maxsplit=1)[1]
            doc = docs_by_type.get(doc_type)
            mutable_item = item.model_copy(deep=True)
            if doc is None:
                mutable_item.blocking = bool(mutable_item.required)
                missing_items.append(mutable_item)
                if mutable_item.blocking:
                    blocking_items.append(mutable_item)
                decision_trace.append(self._trace("required_doc_present", {"doc_type": doc_type}, "missing"))
                continue

            validation_error = self._validate_document(doc)
            if validation_error is not None:
                mutable_item.blocking = True
                mutable_item.satisfied = False
                missing_items.append(mutable_item)
                blocking_items.append(mutable_item)
                decision_trace.append(self._trace(validation_error, {"doc_type": doc_type, "passport_hash": doc.passport_hash}, "blocking"))
                continue

            mutable_item.satisfied = True
            mutable_item.satisfied_by_doc_type = doc.doc_type
            satisfied_items.append(mutable_item)
            decision_trace.append(self._trace("doc_satisfied", {"doc_type": doc_type}, "satisfied"))

        all_required_satisfied = not blocking_items
        manager_override_used = False

        if blocking_items and override_request is not None and self.settings.allow_manual_override:
            if override_request.manager_role != "supervisor":
                decision_trace.append(self._trace("override_denied_role", {"manager_role": override_request.manager_role}, "blocking"))
            else:
                manager_override_used = True
                all_required_satisfied = True
                decision_trace.append(
                    self._trace(
                        "override_approved",
                        {"manager_role": override_request.manager_role, "reason": override_request.override_reason},
                        "allow",
                    )
                )

        return ChecklistResult(
            all_required_satisfied=all_required_satisfied,
            blocking_items=blocking_items,
            satisfied_items=satisfied_items,
            missing_items=missing_items,
            manager_override_used=manager_override_used,
            decision_trace=decision_trace,
        )

    def evaluate_for_fsm(
        self,
        *,
        correlation_id: str,
        resident_profile: ResidentProfile,
        resident_docs: list[ResidentDocument],
        override_request: OverrideRequest | None = None,
    ) -> dict[str, Any]:
        checklist = self.build_checklist(resident_profile)
        result = self.evaluate_checklist(resident_docs=resident_docs, checklist=checklist, override_request=override_request)
        status = "OK" if result.all_required_satisfied else "BLOCKED"
        if not result.all_required_satisfied and self.settings.allow_manual_override:
            status = "NEED_MANAGER"
        audit_id = self.audit_logger.create_record(
            correlation_id=correlation_id,
            resident_id=resident_profile.resident_id,
            checklist_version=self.checklist_version,
            decisions=result.decision_trace,
            override_flag=result.manager_override_used,
        )
        return {
            "status": status,
            "blocking_codes": [item.code for item in result.blocking_items],
            "checklist_snapshot": result.model_dump(mode="json"),
            "audit_id": audit_id,
        }

    async def enforce_crm_stage(
        self,
        *,
        blocker: CRMStageBlocker,
        tenant_id: str,
        correlation_id: str,
        lead_id: int,
        result: ChecklistResult,
    ) -> None:
        await blocker.write_checklist_snapshot(
            tenant_id=tenant_id,
            correlation_id=correlation_id,
            lead_id=lead_id,
            checklist_result=result,
        )
        if result.all_required_satisfied:
            await blocker.unblock_stage(tenant_id=tenant_id, correlation_id=correlation_id, lead_id=lead_id)
            return
        await blocker.block_stage(
            tenant_id=tenant_id,
            correlation_id=correlation_id,
            lead_id=lead_id,
            reason=f"Checklist blocked: {[item.code for item in result.blocking_items]}",
        )
        raise ChecklistBlockingError("Checklist requirements are not satisfied")

    def _validate_document(self, doc: ResidentDocument) -> str | None:
        if doc.ocr_confidence < self.settings.confidence_threshold and not doc.verified_flag:
            return "low_ocr_no_manual_verification"

        checksum_valid = bool(doc.extracted_fields.get("mrz_checksum_valid", True))
        if not checksum_valid:
            return "mrz_checksum_fail"

        if self._is_expired(doc):
            return "doc_expired"

        return None

    def _is_expired(self, doc: ResidentDocument) -> bool:
        expiry_raw = doc.extracted_fields.get("expiry_date")
        if not expiry_raw:
            return False
        expiry_date = date.fromisoformat(str(expiry_raw))
        grace = timedelta(days=self.settings.expiry_grace_days)
        return expiry_date + grace < datetime.now(timezone.utc).date()

    @staticmethod
    def _trace(rule: str, input_data: dict[str, Any], decision: str) -> DecisionTraceEntry:
        return DecisionTraceEntry(rule=rule, input=input_data, decision=decision, timestamp=datetime.now(timezone.utc))
