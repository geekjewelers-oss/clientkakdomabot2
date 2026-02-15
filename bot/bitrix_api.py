import logging

import requests

from bot.bitrix_fields import BITRIX_DEAL_FIELDS
from config import BITRIX_WEBHOOK_URL

logger = logging.getLogger(__name__)


def bitrix_call(method, params):
    """
    Simple wrapper: expects BITRIX_WEBHOOK_URL like https://yourdomain/rest/1/yourhook/
    and will POST to {BITRIX_WEBHOOK_URL}{method}.json
    """
    if not BITRIX_WEBHOOK_URL:
        logger.warning("BITRIX_WEBHOOK_URL не задана")
        return None
    url = BITRIX_WEBHOOK_URL.rstrip("/") + f"/{method}.json"
    try:
        r = requests.post(url, json=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.exception("Bitrix call failed: %s", e)
        return None


def create_lead_and_deal(client_data):
    """
    client_data: dict with keys: surname, given_names, passport_number, phone, address, etc.
    Возвращает (lead_id, deal_id)
    """
    lead_fields = {
        "TITLE": f"Лид: {client_data.get('surname', '')} {client_data.get('given_names','')}",
        "NAME": client_data.get('given_names',''),
        "LAST_NAME": client_data.get('surname',''),
        "PHONE": [{"VALUE": client_data.get('phone',''), "VALUE_TYPE": "WORK"}],
        "COMMENTS": "Авто-лид из Telegram-бота"
    }
    res_lead = bitrix_call("crm.lead.add", {"fields": lead_fields})
    lead_id = None
    if res_lead and 'result' in res_lead:
        lead_id = res_lead['result']

    deal_fields = {
        "TITLE": f"Сделка аренда: {client_data.get('surname','')}",
        "CATEGORY_ID": 0,
        "OPPORTUNITY": client_data.get('amount',''),
        "CURRENCY_ID": "RUB",
        "LEAD_ID": lead_id,
    }

    for client_key, bitrix_field in BITRIX_DEAL_FIELDS.items():
        value = client_data.get(client_key)
        if value:
            deal_fields[bitrix_field] = value

    res_deal = bitrix_call("crm.deal.add", {"fields": deal_fields})
    deal_id = None
    if res_deal and 'result' in res_deal:
        deal_id = res_deal['result']

    if lead_id is None:
        logger.error("Не удалось создать лид в Bitrix. response=%s", res_lead)
    if deal_id is None:
        logger.error("Не удалось создать сделку в Bitrix. response=%s", res_deal)

    return lead_id, deal_id
