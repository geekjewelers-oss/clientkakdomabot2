# Repo Migration Inventory v2

Дата фиксации: **2026-05-18**.

## 1) Reuse directly (брать как есть)

> Эти модули уже соответствуют целевому направлению (backend-first, OCR/checklist как сервисы), и их можно переносить в новый runtime без функциональных изменений.

### ocr_service/*
- `ocr_service/app.py` — готовый FastAPI entrypoint OCR-orchestrator с endpoint-ами submit/job/manual-review/webhook; хорошо ложится в `backend/` как отдельный bounded context OCR.
- `ocr_service/models.py` — актуальные Pydantic-модели OCR job/result/webhook; годятся как канонический API-контракт OCR слоя.
- `ocr_service/repository.py` — изолированный репозиторий job-состояния и дедупликации hash; полезный слой для OCR workflow.
- `ocr_service/engines.py` — SLA/Retry/Quality decision-логика отделена от транспорта, легко встраивается в backend.
- `ocr_service/orchestrator.py` — orchestration core (cycle, fallback, manual review, duplicate); это уже «сердце» OCR бизнес-процесса.
- `ocr_service/preprocess.py` — низкоуровневые image pre-processing утилиты, переиспользуемые как shared OCR pipeline assets.
- `ocr_service/mrz_parser.py` — зрелый MRZ parser/validator/checksum слой.
- `ocr_service/logging.py` — полезные базовые функции структурного логирования и маскирования чувствительных полей.
- `ocr_service/settings.py` — централизованные OCR настройки/пороги/SLA-параметры.
- `ocr_service/__init__.py` — нейтральный package marker, можно оставить.

### checklist_engine/*
- `checklist_engine/models.py` — доменные модели checklist-движка, полезны для backend rules-domain.
- `checklist_engine/rules.py` — декларативные rule-наборы по гражданству/документам.
- `checklist_engine/engine.py` — core rule evaluation engine.
- `checklist_engine/multi_passport.py` — полезная логика обработки multi-passport кейсов.
- `checklist_engine/exceptions.py` — стандартизированные domain exceptions.
- `checklist_engine/audit.py` — аудит результатов checklist (важно для backoffice/Directus traceability).
- `checklist_engine/__init__.py` — package marker.

### Telegram/OCR вспомогательные
- `bot/ocr_client.py` — можно переиспользовать как тонкий API-клиент к OCR backend для `adapters/telegram/`.
- `bot/mrz_parser.py` — можно использовать как fallback/reference parser на этапе миграции (если временно нужна совместимость формата).

---

## 2) Reuse after refactor (переиспользовать после правок)

> Модули полезны, но требуют точечной переработки под целевой runtime (backend + adapters + Directus + S3, без Bitrix).

- `bot/main.py` — **FSM-flow устарел** как source-of-truth состояния; нужно:
  - вырезать бизнес-оркестрацию FSM из Telegram,
  - оставить aiogram bootstrap/handlers wiring,
  - перевести шаги в backend API calls,
  - сохранить полезные части загрузки/отправки файлов в S3,
  - обновить workflow на: `phone auth → ветка A/B → жильцы → паспорта OCR → доп.документы`.
- `bot/ocr_fallback.py` — полезен как fallback-утилита, но нужно перенести fallback-решение в backend OCR policy, а в Telegram оставить только proxy-вызовы.
- `ocr_service/adapters.py` — абстракции хорошие, но `StorageAdapter`/`HttpCRMConnector` надо адаптировать:
  - storage на production S3 abstraction,
  - CRM connector на Directus/backend internal contracts (вместо placeholder-webhook).
- `ocr_service/pipeline.py` — основа сильная, но надо:
  - зафиксировать единую стратегию provider chain,
  - убрать legacy-зависимости/переходные ветки,
  - синхронизировать с backend intake-case сущностями.
- `ocr_service/paddle_engine.py` — production-ценный OCR engine, но требует hardening:
  - dependency isolation,
  - configurable resource limits,
  - унификация с backend observability/metrics.
- `ocr_service/fastapi_compat.py` — полезно только как dev/test shim; в target runtime оставить как fallback для локальной разработки или удалить после стабилизации infra.
- `checklist_engine/crm_blocker.py` — логика блокировок полезна, но надо перецелить с legacy CRM semantics на Directus/backoffice state model.
- `config.py` — оставить как центральный env-config слой, но **убрать Bitrix-переменные** и разнести конфиг по новым доменам (`backend`, `adapters`, `directus`, `s3`).

---

## 3) Legacy / reference only (оставить как справку, не использовать в новом runtime)

> Эти файлы можно хранить как исторический контекст/референс до полного завершения миграции, но не подключать в target execution path.

- `main.py` (корневой) — legacy монолитный entrypoint (Telegram + OCR + Bitrix паттерны), использовать только как источник исторической логики.
- `tests/test_fsm_e2e.py` — legacy e2e сценарии для Telegram FSM как основного runtime; годится только как reference к пользовательским веткам диалога.
- `tests/test_bitrix_connector.py` — справочный тест legacy Bitrix слоя; не часть target quality gate.

---

## 4) Remove later (удалить после готовности замены)

> Эти модули явно относятся к выводимому legacy слою. Удалять после полной функциональной замены в backend/directus/adapters.

- `connectors/bitrix_connector.py` — Bitrix24 connector, подлежит удалению.
- `schemas/bitrix_models.py` — Bitrix Pydantic-схемы, подлежат удалению.
- `utils/bitrix_integration.py` — legacy Bitrix integration, подлежит удалению.
- `deploy_crm.py` — старая Directus schema provisioning логика; схема устарела (нет `intake_cases`, `deal_residents`, `occupancies`, `registrations`, `payments_owner` и др.), файл подлежит удалению.

---

## Отдельная оценка tests/* (каждый файл)

- `tests/test_checklist_engine.py` — **reuse after refactor**: сохранить как базу regression для checklist, но актуализировать под новый backend contract.
- `tests/test_mrz_parser.py` — **reuse directly**: полезный unit-suite для MRZ parsing.
- `tests/test_ocr_service_mrz.py` — **reuse directly**: релевантен OCR/MRZ домену.
- `tests/test_logging_sla.py` — **reuse directly**: важен для SLA/logging гарантий OCR.
- `tests/test_ocr_service_api.py` — **reuse after refactor**: API-тесты нужно синхронизировать с итоговыми endpoint-ами backend/ocr.
- `tests/test_mrz_composite.py` — **reuse directly**: покрытие composite-checksum/валидации.
- `tests/test_mrz_checksum.py` — **reuse directly**: фундаментальные checksum тесты.
- `tests/test_fsm_e2e.py` — **legacy/reference only**: сценарии legacy FSM.
- `tests/test_bitrix_connector.py` — **legacy/reference only → remove later вместе с Bitrix слоем**.

---

## Краткий план безопасного вывода legacy
1. Зафиксировать новые backend API contracts для intake + OCR + checklist.
2. Перевести Telegram в thin adapter (без state ownership).
3. Перевести CRM операции на Directus collections/flows.
4. Перенести и обновить тесты на новый runtime.
5. После green regression удалить Bitrix/deploy_crm legacy слой.
