from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx
import structlog

from schemas.bitrix_models import BitrixTenantCredentials, DealData, ResidentData


class BitrixConnector:
    def __init__(
        self,
        tenants: dict[str, BitrixTenantCredentials],
        *,
        timeout_seconds: float = 10.0,
        max_retries: int = 4,
        backoff_base_seconds: float = 0.2,
        backoff_max_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._tenants = tenants
        self._max_retries = max_retries
        self._backoff_base_seconds = backoff_base_seconds
        self._backoff_max_seconds = backoff_max_seconds
        self._logger = structlog.get_logger("bitrix_connector")
        self._client = httpx.AsyncClient(
            timeout=timeout_seconds,
            transport=transport,
            headers={"Content-Type": "application/json"},
        )
        self._idempotent_cache: dict[tuple[str, str, str], Any] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def create_contact(self, resident_data: ResidentData) -> int:
        fields = {
            "NAME": resident_data.first_name,
            "LAST_NAME": resident_data.last_name,
            "PHONE": [{"VALUE": resident_data.phone, "VALUE_TYPE": "WORK"}] if resident_data.phone else [],
            **resident_data.ocr.to_bitrix_uf_fields(),
        }
        response = await self._call(
            tenant_id=resident_data.tenant_id,
            method="crm.contact.add",
            params={"fields": fields},
            correlation_id=resident_data.correlation_id,
            idempotency_key=resident_data.idempotency_key,
            operation="create_contact",
        )
        return int(response["result"])

    async def create_lead(self, deal_data: DealData) -> int:
        fields = {
            "TITLE": deal_data.title,
            "OPPORTUNITY": deal_data.amount,
            "CURRENCY_ID": deal_data.currency,
            "STAGE_ID": deal_data.stage_id,
            "CONTACT_ID": deal_data.contact_id,
            **deal_data.ocr.to_bitrix_uf_fields(),
        }
        response = await self._call(
            tenant_id=deal_data.tenant_id,
            method="crm.lead.add",
            params={"fields": fields},
            correlation_id=deal_data.correlation_id,
            idempotency_key=deal_data.idempotency_key,
            operation="create_lead",
        )
        return int(response["result"])

    async def bind_contact_to_lead(
        self,
        *,
        tenant_id: str,
        correlation_id: str,
        lead_id: int,
        contact_id: int,
        idempotency_key: str | None = None,
    ) -> bool:
        response = await self._call(
            tenant_id=tenant_id,
            method="crm.lead.contact.items.set",
            params={"id": lead_id, "items": [{"CONTACT_ID": contact_id}]},
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            operation="bind_contact_to_lead",
        )
        return bool(response.get("result", True))

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
        response = await self._call(
            tenant_id=tenant_id,
            method="crm.timeline.comment.add",
            params={
                "fields": {
                    "ENTITY_ID": entity_id,
                    "ENTITY_TYPE": entity_type,
                    "COMMENT": f"Document link: {url}",
                }
            },
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            operation="attach_document_link",
        )
        return bool(response.get("result", True))

    async def search_by_passport_hash(
        self,
        *,
        tenant_id: str,
        correlation_id: str,
        passport_hash: str,
    ) -> list[dict[str, Any]]:
        response = await self._call(
            tenant_id=tenant_id,
            method="crm.contact.list",
            params={"filter": {"UF_PASSPORT_HASH": passport_hash}},
            correlation_id=correlation_id,
            operation="search_by_passport_hash",
        )
        return list(response.get("result", []))

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
        response = await self._call(
            tenant_id=tenant_id,
            method="crm.lead.update",
            params={"id": lead_id, "fields": {"STAGE_ID": stage_id, "COMMENTS": checklist_block}},
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            operation="update_stage_with_checklist_block",
        )
        return bool(response.get("result", True))

    async def manager_verification_required_flag(
        self,
        *,
        tenant_id: str,
        correlation_id: str,
        lead_id: int,
        required: bool,
        idempotency_key: str | None = None,
    ) -> bool:
        response = await self._call(
            tenant_id=tenant_id,
            method="crm.lead.update",
            params={"id": lead_id, "fields": {"UF_MANAGER_VERIFICATION_REQUIRED": "Y" if required else "N"}},
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            operation="manager_verification_required_flag",
        )
        return bool(response.get("result", True))

    async def _call(
        self,
        *,
        tenant_id: str,
        method: str,
        params: dict[str, Any],
        correlation_id: str,
        operation: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        credentials = self._get_credentials(tenant_id)
        cache_key = (tenant_id, operation, idempotency_key or "")
        if idempotency_key and cache_key in self._idempotent_cache:
            self._logger.info(
                "bitrix_idempotent_cache_hit",
                operation=operation,
                tenant_id=tenant_id,
                correlation_id=correlation_id,
            )
            return self._idempotent_cache[cache_key]

        url = credentials.webhook_base_url.rstrip("/") + f"/{method}.json"
        headers = {
            "X-Correlation-ID": correlation_id,
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        for attempt in range(self._max_retries + 1):
            try:
                resp = await self._client.post(url, json=params, headers=headers)
            except httpx.HTTPError as exc:
                if attempt >= self._max_retries:
                    raise
                await self._sleep_before_retry(attempt, retry_after=None)
                self._logger.warning(
                    "bitrix_retry_network_error",
                    error=str(exc),
                    attempt=attempt + 1,
                    tenant_id=tenant_id,
                    method=method,
                    correlation_id=correlation_id,
                )
                continue

            if resp.status_code == 429:
                if attempt >= self._max_retries:
                    resp.raise_for_status()
                retry_after = self._parse_retry_after(resp.headers.get("Retry-After"))
                await self._sleep_before_retry(attempt, retry_after=retry_after)
                self._logger.warning(
                    "bitrix_retry_rate_limited",
                    attempt=attempt + 1,
                    tenant_id=tenant_id,
                    method=method,
                    correlation_id=correlation_id,
                    retry_after_seconds=retry_after,
                )
                continue

            if 500 <= resp.status_code <= 599:
                if attempt >= self._max_retries:
                    resp.raise_for_status()
                await self._sleep_before_retry(attempt, retry_after=None)
                self._logger.warning(
                    "bitrix_retry_server_error",
                    attempt=attempt + 1,
                    tenant_id=tenant_id,
                    method=method,
                    correlation_id=correlation_id,
                    status_code=resp.status_code,
                )
                continue

            resp.raise_for_status()
            payload = resp.json()
            if "error" in payload:
                raise RuntimeError(f"Bitrix API error: {payload['error']}")

            self._logger.info(
                "bitrix_request_success",
                operation=operation,
                tenant_id=tenant_id,
                method=method,
                correlation_id=correlation_id,
                payload=self._mask_payload(params),
            )
            if idempotency_key:
                self._idempotent_cache[cache_key] = payload
            return payload

        raise RuntimeError("Unreachable retry loop end")

    def _get_credentials(self, tenant_id: str) -> BitrixTenantCredentials:
        if tenant_id not in self._tenants:
            raise KeyError(f"Unknown tenant_id: {tenant_id}")
        return self._tenants[tenant_id]

    async def _sleep_before_retry(self, attempt: int, *, retry_after: float | None) -> None:
        if retry_after is not None:
            await asyncio.sleep(max(0.0, retry_after))
            return
        base = min(self._backoff_max_seconds, self._backoff_base_seconds * (2**attempt))
        jitter = random.uniform(0.0, base / 4 if base > 0 else 0.001)
        await asyncio.sleep(min(self._backoff_max_seconds, base + jitter))

    @staticmethod
    def _parse_retry_after(value: str | None) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    @staticmethod
    def _mask_payload(payload: dict[str, Any]) -> dict[str, Any]:
        masked = dict(payload)
        fields = dict(masked.get("fields", {}))
        if "UF_PASSPORT_HASH" in fields:
            hashed = str(fields["UF_PASSPORT_HASH"])
            fields["UF_PASSPORT_HASH"] = f"{hashed[:4]}***{hashed[-4:]}" if len(hashed) > 8 else "***"
        masked["fields"] = fields
        if "passport_number" in masked:
            masked["passport_number"] = "***"
        return masked
