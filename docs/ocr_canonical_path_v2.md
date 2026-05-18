# OCR Canonical Path v2

## 1) Canonical OCR pipeline (engine order)
1. **Step 1 (primary, local):** PaddleOCR + OpenCV + MRZ parser via `ocr_service/pipeline.py` → `run_ocr_pipeline_v2`.
2. **Step 2 (fallback 1):** OCR.space API.
3. **Step 3 (fallback 2):** Yandex Vision.

## 2) Auto-acceptance conditions
The decision logic in `pipeline.py` uses all three conditions:

- `avg_confidence >= MIN_CONFIDENCE` (configured via settings; default `0.85`).
- `validation.all_three_ok == True` (all 3 MRZ checksums are valid).
- `cross_validate(mrz_fields, full_page_fields) == True`.

Decision rules:

- If **all three** conditions are true → `auto_accepted = True`, `manual_check = False`.
- If **at least one** condition is false → move to fallback chain.
- If fallback chain also does not produce `auto_accepted` result → `manual_check = True`.

## 3) OCR result contract (`run_ocr_pipeline_v2`)
`run_ocr_pipeline_v2` returns a dictionary with:

- `success: bool`
- `auto_accepted: bool`
- `manual_check: bool`
- `confidence_score: float`
- `parsing_source: "paddle" | "ocr_space" | "yandex_vision"`
- `fields: {surname, given_names, date_of_birth, nationality, passport_number, passport_hash, full_name_cyr}`
- `mrz: str` (two MRZ lines)
- `warnings: list[str]` (`low_confidence`, `checksum_failed`, `cross_validation_failed`, `mrz_not_found`)
- `sla_breach: bool`
- `correlation_id: str`

## 4) OCR metrics fields (persist to `client_passports`)
Backend persistence mapping after OCR:

- `ocr_confidence` → `confidence_score`
- `auto_accepted` → `auto_accepted`
- `manual_check` → `manual_check`
- `fallback_stage_reached` → `parsing_source` (when source is not `paddle`)
- `final_source` → `parsing_source`
- `mrz_raw` → `fields.mrz`
- `passport_hash` → `fields.passport_hash`

## 5) Legacy OCR paths (do not use in new backend)
- `bot/ocr_client.py` — Telegram-specific legacy OCR client.
- `bot/ocr_fallback.py` — legacy fallback logic duplicated from `pipeline.py`.
- `bot/mrz_parser.py` — legacy parser duplicated from `ocr_service/mrz_parser.py`.
- `ocr_service/pipeline.py::_run_yandex_vision` previously called `bot.vision_fallback`; this coupling is removed in v2 path.

## 6) Target integration path for new backend
- `backend/ocr/router.py` receives passport file.
- It calls OCR directly through `run_ocr_pipeline_v2(image_bytes, correlation_id)`.
- **No HTTP between services for MVP** (`ocr_service` is embedded, not a separate microservice in MVP).
- Result is saved to `intake_resident.ocr_data` and then to `client_passports` after dedup.
