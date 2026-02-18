# clientkakdomabot

Базовая production-структура Python Telegram-бота для распознавания MRZ из фото паспорта.

## Структура проекта

```text
bot/
  main.py
  mrz_parser.py
.env.example
.gitignore
requirements.txt
README.md
```

## 1) Создание и активация виртуального окружения

```bash
python3 -m venv .venv
source .venv/bin/activate
```

## 2) Установка зависимостей

```bash
pip install -r requirements.txt
```

## 3) Создание .env

Скопируйте шаблон и заполните значения:

```bash
cp .env.example .env
```

Пример содержимого `.env`:

```env
BOT_TOKEN=PUT_TOKEN
BITRIX_WEBHOOK=PUT_WEBHOOK
S3_ENDPOINT_URL=PUT_ENDPOINT
S3_ACCESS_KEY=PUT_KEY
S3_SECRET_KEY=PUT_SECRET
S3_BUCKET=PUT_BUCKET
OPENAI_API_KEY=PUT_KEY
```

## 4) Запуск бота

```bash
python bot/main.py
```

## Что умеет бот сейчас

- Команда `/start`.
- Прием фото документа.
- OCR + поиск MRZ.
- Парсинг основных полей паспорта (TD3).
- Показ распознанных данных и кнопка **"Все верно"**.
- Заглушки для будущей интеграции с Bitrix и S3.

## OCR SLA logging and metrics

- OCR SLA decisions are emitted as structured JSON with `logger=OCR_SLA_DECISION` and `logger_version=ocr_sla_v1`.
- Sensitive MRZ values are never logged directly; use `passport_hash` and `passport_mrz_len` only.
- Metrics are disabled by default and controlled by:
  - `OCR_LOG_METRICS_ENABLED` (`false` by default)
  - `OCR_METRICS_BACKEND` (`noop`, `prometheus`, `statsd`)
  - `OCR_SLA_BREACH_THRESHOLD_RATIO` (`0.9` by default)

See `SECURITY.md` for privacy rules.

## OCR Orchestrator API (FastAPI)

Добавлен production-ready модуль `ocr_service` с endpoint'ами:
- `POST /v1/ocr/submit`
- `GET /v1/ocr/job/{job_id}`
- `POST /v1/ocr/manual-review/{job_id}`
- `POST /internal/webhooks/ocr-result`

Запуск:

```bash
uvicorn ocr_service.app:app --reload
```

## Bitrix24 CRM integration (async, production)

Добавлен production-коннектор `BitrixConnector` для OCR/Telegram registration потока:

- Файлы:
  - `connectors/bitrix_connector.py`
  - `schemas/bitrix_models.py`
  - `tests/test_bitrix_connector.py`
- Python 3.11 async + `httpx.AsyncClient` only.
- Retry: exponential backoff + jitter, обработка `429` + `Retry-After`, retry для `5xx` и network ошибок.
- Multi-tenant credentials через `BitrixTenantCredentials` и `tenant_id`.
- Correlation ID: заголовок `X-Correlation-ID` добавляется в каждый запрос.
- Idempotency: поддержка `Idempotency-Key` + in-process cache для безопасных повторов.
- Structured logs: через `structlog`.
- Security:
  - В CRM отправляется только `passport_hash` (`UF_PASSPORT_HASH`).
  - Raw номер паспорта не используется.
  - Логи маскируют `UF_PASSPORT_HASH`.

### OCRResult → Bitrix UF_* strict mapping

- `UF_PASSPORT_HASH`
- `UF_NATIONALITY`
- `UF_BIRTH_DATE`
- `UF_DOC_EXPIRY`
- `UF_OCR_CONFIDENCE`
- `UF_DUPLICATE_FLAG`

Маппинг реализован в `OCRBitrixFields.to_bitrix_uf_fields()` и запрещает лишние поля (`extra="forbid"`).

### Пример использования

```python
from connectors.bitrix_connector import BitrixConnector
from schemas.bitrix_models import BitrixTenantCredentials, OCRBitrixFields, ResidentData

connector = BitrixConnector(
    tenants={
        "tenant-a": BitrixTenantCredentials(
            tenant_id="tenant-a",
            webhook_base_url="https://<portal>/rest/<user>/<webhook>",
        )
    }
)

contact_id = await connector.create_contact(
    ResidentData(
        tenant_id="tenant-a",
        correlation_id="corr-12345678",
        idempotency_key="registration-<unique>",
        first_name="Ivan",
        last_name="Petrov",
        phone="+79001112233",
        ocr=OCRBitrixFields(
            passport_hash="sha256:...",
            nationality="RU",
            birth_date="1990-01-01",
            doc_expiry="2030-01-01",
            ocr_confidence=0.97,
            duplicate_flag=False,
        ),
    )
)
```
