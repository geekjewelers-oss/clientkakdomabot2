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
