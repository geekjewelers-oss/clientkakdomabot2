# Legacy Boundary v2

Дата фиксации границы: **2026-05-18**.

## LEGACY / НЕ TARGET

Ниже перечислены компоненты, которые **не являются целевым runtime** и не должны оставаться source-of-truth после миграции:

1. **Telegram FSM как источник workflow state**
   - Текущая FSM-логика в `bot/main.py`.
   - Причина исключения: state должен жить в backend, а не в adapter-слое мессенджера.

2. **Bitrix24 layer**
   - `connectors/`
   - `schemas/bitrix_models.py`
   - `utils/bitrix_integration.py`
   - Причина исключения: Bitrix полностью выводится из системы.

3. **`deploy_crm.py` и старая Directus schema**
   - Текущая схема устарела и неполна для target-домена.
   - В ней отсутствуют ключевые целевые сущности: `intake_cases`, `deal_residents`, `occupancies`, `registrations`, `payments_owner`.

4. **Корневой `main.py` как entrypoint**
   - Legacy-агрегация старых подходов (бот-центричная оркестрация).
   - Не должен использоваться в новом runtime.

---

## TARGET RUNTIME

Ниже — целевая архитектурная граница, которую нужно считать canonical:

1. **`backend/`**
   - Главный workflow API.
   - Здесь живет FSM/state intake процесса.
   - Центр оркестрации OCR/checklist/CRM интеграций.

2. **`frontend/`**
   - Client-facing PWA.
   - Mobile-first UX.
   - Основной язык интерфейса: русский.

3. **`directus/`**
   - CRM backoffice для менеджеров и руководителя.
   - Управление case-ами, ручными проверками, операционными статусами.

4. **S3-compatible storage**
   - Единое файловое хранилище для:
     - паспортов,
     - видео,
     - договоров,
     - иных прикрепляемых документов.

5. **`adapters/telegram/`**
   - Тонкий Telegram adapter.
   - Делает proxy шагов в `backend/`.
   - **Не хранит workflow state**.

6. **`adapters/whatsapp/` (post-MVP)**
   - Аналогичный тонкий adapter.
   - Тот же принцип: проксирование шагов в backend без собственного state ownership.

---

## Архитектурные правила границы

1. Любая бизнес-логика intake/state machine должна добавляться в `backend/`, а не в Telegram-слой.
2. Любые CRM-сущности и процессы должны проектироваться под Directus target-модель, а не под Bitrix-поля.
3. Любой adapter (Telegram/WhatsApp) должен быть stateless относительно core workflow.
4. Legacy-модули допускаются только как временный reference до полного decommissioning.

---

## Что это означает для миграции

- До завершения миграции legacy-файлы могут физически оставаться в репозитории.
- Но новые фичи и новые зависимости должны добавляться только в target-контур (`backend/`, `frontend/`, `directus/`, `adapters/*`, S3 layer).
- После готовности replacement-слоя legacy-компоненты удаляются поэтапно.
