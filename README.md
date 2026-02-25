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

## Деплой на Oracle Cloud Free Tier (Always Free ARM 4 OCPU / 24 GB)

Ниже — практический production-сценарий для ARM-инстанса Oracle Cloud (Ampere A1).

### 1) Создание VM

1. Oracle Cloud Console → **Compute** → **Instances** → **Create instance**.
2. Shape: **VM.Standard.A1.Flex**.
3. Выставить ресурсы (Always Free максимум):
   - `4 OCPU`
   - `24 GB RAM`
4. Image: Ubuntu 22.04/24.04 ARM.
5. Открыть ingress порты в Security List / NSG:
   - `22` (SSH)
   - `80` (HTTP)
   - `443` (HTTPS)
   - при прямом запуске API: `8000` (опционально)

### 2) Базовая подготовка сервера

```bash
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y ca-certificates curl git ufw
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
```

### 3) Установка Docker + Buildx (multi-arch)

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

docker version
docker buildx create --name multiarch --use || docker buildx use multiarch
docker buildx inspect --bootstrap
```

### 4) Клонирование и env

```bash
git clone <YOUR_REPO_URL> app
cd app
cp .env.example .env
nano .env
```

Минимальные переменные для новой fallback-цепочки:

```env
OCR_FALLBACK_ENABLED=true
OCR_SPACE_API_KEY=...
AZAPI_API_KEY=...
AZAPI_ENABLED=true
YANDEX_VISION_API_KEY=...  # последний fallback
YANDEX_VISION_FOLDER_ID=... # последний fallback
MIN_CONFIDENCE=0.85
```

### 5) Сборка образа для ARM

На ARM-инстансе можно собирать нативно:

```bash
docker build -t clientkakdomabot:prod .
```

Или multi-arch образ для registry:

```bash
docker buildx build \
  --platform linux/arm64,linux/amd64 \
  -t <registry>/clientkakdomabot:prod \
  --push .
```

### 6) Запуск контейнера

```bash
docker run -d \
  --name clientkakdomabot \
  --restart unless-stopped \
  --env-file .env \
  -p 8000:8000 \
  clientkakdomabot:prod
```

Если используете Telegram polling-only режим без внешнего API, порт можно не публиковать.

### 7) Health-check и логи

```bash
docker ps
docker logs -f clientkakdomabot
```

Для OCR API (если поднят FastAPI):

```bash
curl -f http://127.0.0.1:8000/health || true
```

### 8) Рекомендуемый production hardening

- Использовать reverse-proxy (Nginx/Caddy) + TLS (Let's Encrypt).
- Ограничить доступ к порту приложения только через localhost + proxy.
- Хранить `.env` только на сервере, не коммитить секреты.
- Включить ротацию логов Docker.
- Настроить мониторинг контейнера (CPU/RAM/restarts).
- Делать регулярный `docker pull`/`docker build` + rolling restart.

### 9) Проверка fallback-цепочки в runtime

Ожидаемый порядок провайдеров:
1. `paddle`
2. `ocr_space`
3. `azapi`
4. `yandex_vision`

Если никто не вернул `auto_accepted=true`, итог должен быть `manual_check=true`.
