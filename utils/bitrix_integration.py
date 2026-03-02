from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from pybitrix24 import Bitrix24

from config import BITRIX_API_KEY, BITRIX_API_URL

logger = logging.getLogger(__name__)


class BitrixIntegrationError(RuntimeError):
    """Raised when Bitrix24 integration request fails."""


def _build_client() -> Bitrix24:
    if not BITRIX_API_URL or not BITRIX_API_KEY:
        raise BitrixIntegrationError("BITRIX_API_URL или BITRIX_API_KEY не настроены")
    return Bitrix24(BITRIX_API_URL, BITRIX_API_KEY)


def _normalize_date(value: str) -> str:
    if not value:
        return ""
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return value


def _call_method_sync(client: Bitrix24, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    call_method = getattr(client, "callMethod", None) or getattr(client, "call_method", None)
    if call_method is None:
        raise BitrixIntegrationError("pybitrix24 не поддерживает callMethod/call_method")

    result = call_method(method, payload)
    if isinstance(result, dict) and result.get("error"):
        raise BitrixIntegrationError(f"Bitrix error: {result.get('error_description') or result.get('error')}")
    return result or {}


async def create_contact(resident: dict[str, Any]) -> int:
    client = _build_client()
    fields = {
        "NAME": resident.get("given_names", ""),
        "LAST_NAME": resident.get("surname", ""),
        "BIRTHDATE": _normalize_date(resident.get("date_of_birth", "")),
        "UF_CRM_PASSPORT": resident.get("passport_number", ""),
        "UF_CRM_PASSPORT_PHOTO": resident.get("presigned_url", ""),
        "UF_CRM_CITIZENSHIP": resident.get("nationality", ""),
        "UF_CRM_OCR_CONFIDENCE": str(resident.get("confidence_score", "")),
    }
    response = await asyncio.to_thread(_call_method_sync, client, "crm.contact.add", {"fields": fields})
    contact_id = (response or {}).get("result")
    if not contact_id:
        raise BitrixIntegrationError("Bitrix не вернул ID контакта")
    return int(contact_id)


async def create_deal(deal_data: dict[str, Any], contact_id: int) -> int:
    client = _build_client()
    fields = {
        "TITLE": f"Аренда: {deal_data.get('address', 'без адреса')}",
        "CONTACT_ID": contact_id,
        "UF_CRM_APARTMENT_ADDRESS": deal_data.get("address", ""),
        "UF_CRM_RENT_AMOUNT": deal_data.get("rent_amount", ""),
        "UF_CRM_DEPOSIT": deal_data.get("deposit", ""),
        "UF_CRM_COMMISSION": deal_data.get("commission", ""),
        "UF_CRM_MANAGER_VERIFIED": "N",
        "UF_CRM_MOVE_IN_DATE": _normalize_date(deal_data.get("move_date", "")),
        "UF_CRM_REGISTRATION_TERM": deal_data.get("registration_term", ""),
        "UF_CRM_MEDIA_FIXATION": "",
        "UF_CRM_LEGALITY_STATUS": "Проверено",
    }
    response = await asyncio.to_thread(_call_method_sync, client, "crm.deal.add", {"fields": fields})
    deal_id = (response or {}).get("result")
    if not deal_id:
        raise BitrixIntegrationError("Bitrix не вернул ID сделки")
    return int(deal_id)


async def create_bitrix_contact_and_deal(data: dict[str, Any]) -> tuple[list[int], list[int]]:
    residents = data.get("residents", [])
    contact_ids: list[int] = []
    deal_ids: list[int] = []

    for resident in residents:
        contact_id = await create_contact(resident)
        contact_ids.append(contact_id)

        deal_id = await create_deal(data, contact_id)
        deal_ids.append(deal_id)

    logger.info(
        '{"event":"bitrix_contact_deal_created","contacts":%s,"deals":%s}',
        contact_ids,
        deal_ids,
    )
    return contact_ids, deal_ids
