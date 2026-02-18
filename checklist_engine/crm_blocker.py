from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .models import ChecklistResult


class CRMStageBlocker(Protocol):
    async def block_stage(self, *, tenant_id: str, correlation_id: str, lead_id: int, reason: str) -> None:
        ...

    async def unblock_stage(self, *, tenant_id: str, correlation_id: str, lead_id: int) -> None:
        ...

    async def write_checklist_snapshot(
        self,
        *,
        tenant_id: str,
        correlation_id: str,
        lead_id: int,
        checklist_result: ChecklistResult,
    ) -> None:
        ...


class BitrixLikeConnector(Protocol):
    async def update_stage_with_checklist_block(
        self,
        *,
        tenant_id: str,
        correlation_id: str,
        lead_id: int,
        stage_id: str,
        checklist_block: str,
        idempotency_key: str | None = None,
    ) -> bool:
        ...

    async def manager_verification_required_flag(
        self,
        *,
        tenant_id: str,
        correlation_id: str,
        lead_id: int,
        required: bool,
        idempotency_key: str | None = None,
    ) -> bool:
        ...

    async def attach_document_link(
        self,
        entity_id: int,
        url: str,
        *,
        tenant_id: str,
        correlation_id: str,
        idempotency_key: str | None = None,
        entity_type: str = "lead",
    ) -> bool:
        ...


@dataclass
class ChecklistCRMBlocker(CRMStageBlocker):
    connector: BitrixLikeConnector
    blocked_stage_id: str
    unblocked_stage_id: str

    async def block_stage(self, *, tenant_id: str, correlation_id: str, lead_id: int, reason: str) -> None:
        await self.connector.update_stage_with_checklist_block(
            tenant_id=tenant_id,
            correlation_id=correlation_id,
            lead_id=lead_id,
            stage_id=self.blocked_stage_id,
            checklist_block=reason,
        )
        await self.connector.manager_verification_required_flag(
            tenant_id=tenant_id,
            correlation_id=correlation_id,
            lead_id=lead_id,
            required=True,
        )

    async def unblock_stage(self, *, tenant_id: str, correlation_id: str, lead_id: int) -> None:
        await self.connector.update_stage_with_checklist_block(
            tenant_id=tenant_id,
            correlation_id=correlation_id,
            lead_id=lead_id,
            stage_id=self.unblocked_stage_id,
            checklist_block="Checklist passed",
        )
        await self.connector.manager_verification_required_flag(
            tenant_id=tenant_id,
            correlation_id=correlation_id,
            lead_id=lead_id,
            required=False,
        )

    async def write_checklist_snapshot(
        self,
        *,
        tenant_id: str,
        correlation_id: str,
        lead_id: int,
        checklist_result: ChecklistResult,
    ) -> None:
        snapshot: dict[str, Any] = {
            "all_required_satisfied": checklist_result.all_required_satisfied,
            "blocking_codes": [item.code for item in checklist_result.blocking_items],
            "missing_codes": [item.code for item in checklist_result.missing_items],
            "satisfied_codes": [item.code for item in checklist_result.satisfied_items],
            "override": checklist_result.manager_override_used,
            "trace": [entry.model_dump(mode="json") for entry in checklist_result.decision_trace],
        }
        await self.connector.attach_document_link(
            entity_id=lead_id,
            url=f"checklist://snapshot/{snapshot}",
            tenant_id=tenant_id,
            correlation_id=correlation_id,
            entity_type="lead",
        )
