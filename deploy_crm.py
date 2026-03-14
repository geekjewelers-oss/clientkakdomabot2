import requests
import json

# === НАСТРОЙКИ (ПОЛЬЗОВАТЕЛЬ, ЗАМЕНИ ЭТИ ДАННЫЕ НА СВОИ) ===
DIRECTUS_URL = "http://your-directus-url.com" # Укажи URL твоего Directus
ADMIN_TOKEN = "your_admin_static_token_here"  # Укажи статический токен админа

HEADERS = {
    "Authorization": f"Bearer {ADMIN_TOKEN}",
    "Content-Type": "application/json"
}

# === АРХИТЕКТУРА БД: КОЛЛЕКЦИИ, ПОЛЯ, СВЯЗИ И ФЛАГИ КОНТРОЛЯ ===
SCHEMA = {
    "owners": {
        "meta": {"icon": "person", "note": "Собственники квартир"},
        "fields": [
            {"field": "full_name", "type": "string"},
            {"field": "phone", "type": "string"},
            {"field": "document_info", "type": "text"}
        ]
    },
    "apartments": {
        "meta": {"icon": "apartment", "note": "Объекты проживания"},
        "fields": [
            {"field": "address", "type": "string"},
            {"field": "status", "type": "string", "meta": {"options": {"choices": [{"text": "Свободна", "value": "free"}, {"text": "Занята", "value": "occupied"}]}}},
            {"field": "owner_id", "type": "uuid", "relation": "owners"}
        ]
    },
    "residents": {
        "meta": {"icon": "group", "note": "Клиенты / Жильцы"},
        "fields": [
            {"field": "full_name", "type": "string"},
            {"field": "phone", "type": "string"},
            {"field": "citizenship", "type": "string"},
            {"field": "telegram_id", "type": "string"}
        ]
    },
    "passports": {
        "meta": {"icon": "badge", "note": "Документы и OCR Валидация"},
        "fields": [
            {"field": "resident_id", "type": "uuid", "relation": "residents"},
            {"field": "mrz_raw", "type": "text"},
            {"field": "ocr_confidence", "type": "integer", "meta": {"note": "Процент уверенности распознавания (0-100)"}},
            # Флаги контроля OCR:
            {"field": "needs_manual_review", "type": "boolean", "meta": {"note": "Авто-флаг: если OCR < 90%, требуется проверка менеджера"}},
            {"field": "manager_approved", "type": "boolean", "meta": {"note": "Ручное подтверждение паспорта менеджером"}}
        ]
    },
    "deals": {
        "meta": {"icon": "real_estate_agent", "note": "Сделки и Чек-листы"},
        "fields": [
            {"field": "apartment_id", "type": "uuid", "relation": "apartments"},
            {"field": "resident_id", "type": "uuid", "relation": "residents"},
            {"field": "manager_id", "type": "uuid", "relation": "directus_users"},
            {"field": "stage", "type": "string", "meta": {"options": {"choices": [
                {"text": "1. Лид", "value": "lead"},
                {"text": "2. Сбор Документов", "value": "docs"},
                {"text": "3. Проверка Менеджером", "value": "manager_review"},
                {"text": "4. Подписание Договора", "value": "signing"},
                {"text": "5. Ожидание Оплаты", "value": "payment"},
                {"text": "6. Заселение", "value": "move_in"}
            ]}}},
            {"field": "rent_amount", "type": "integer"},

            # === ФЛАГИ КОНТРОЛЯ МЕНЕДЖЕРА (Блокираторы стадий) ===
            {"field": "check_passport_valid", "type": "boolean", "meta": {"note": "Менеджер проверил паспорт"}},
            {"field": "check_registration_allowed", "type": "boolean", "meta": {"note": "Менеджер одобрил регистрацию"}},
            {"field": "check_contract_signed", "type": "boolean", "meta": {"note": "Договор подписан обеими сторонами"}},
            {"field": "check_payment_received", "type": "boolean", "meta": {"note": "Оплата поступила на счет"}},

            # Файлы
            {"field": "contract_pdf_url", "type": "string"}
        ]
    }
}


def create_schema():
    print("🚀 Начинаем развертывание CRM-архитектуры в Directus...")

    for collection, data in SCHEMA.items():
        # 1. Пытаемся создать коллекцию
        payload = {"collection": collection, "meta": data.get("meta", {}), "schema": {}}
        payload["fields"] = [{"field": "id", "type": "uuid", "schema": {"is_primary_key": True}, "meta": {"hidden": True}}]

        res = requests.post(f"{DIRECTUS_URL}/collections", headers=HEADERS, json=payload)

        if res.status_code in [200, 204]:
            print(f"✅ Коллекция '{collection}' успешно создана.")
        elif res.status_code == 400 and "already exists" in res.text:
            print(f"⏩ Коллекция '{collection}' уже существует, пропускаем.")
        else:
            print(f"⚠️ Ошибка создания '{collection}': {res.text}")

        # 2. Создаем поля для коллекции
        for field in data.get("fields", []):
            field_payload = {"field": field["field"], "type": field["type"]}
            if "meta" in field:
                field_payload["meta"] = field["meta"]

            res_field = requests.post(f"{DIRECTUS_URL}/fields/{collection}", headers=HEADERS, json=field_payload)

            if res_field.status_code in [200, 204]:
                print(f"   ➕ Поле '{field['field']}' добавлено.")
            elif res_field.status_code == 400 and "already exists" in res_field.text:
                pass  # Поле уже есть, молча пропускаем
            else:
                print(f"   ⚠️ Ошибка поля '{field['field']}': {res_field.text}")

            # 3. Настраиваем связи (Relations)
            if "relation" in field:
                rel_payload = {
                    "collection": collection,
                    "field": field["field"],
                    "related_collection": field["relation"]
                }
                res_rel = requests.post(f"{DIRECTUS_URL}/relations", headers=HEADERS, json=rel_payload)
                if res_rel.status_code in [200, 204]:
                    print(f"   🔗 Создана связь: {collection}.{field['field']} -> {field['relation']}")

    print("🎉 Развертывание завершено!")


if __name__ == "__main__":
    create_schema()
