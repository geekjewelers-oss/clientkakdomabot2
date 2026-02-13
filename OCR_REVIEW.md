# OCR/MRZ Reliability Review

## Key findings

1. Production FSM path still uses legacy OCR chain directly inside `handlers_registration.py`, not the new centralized `ocr_pipeline_extract` from root `main.py`.
2. TD3 parser validates three elementary checksums, but does not validate optional-data checksum and final composite checksum.
3. No explicit normalization layer for OCR confusion symbols (`O/0`, `B/8`, `I/1`, `S/5`, `G/6`) before checksum/date validation.
4. MRZ detection still relies on full-frame OCR first; no lower-band MRZ candidate detection and no deskew/rotation pipeline.
5. `needs_better_photo` UX flag is not consumed in FSM path where users actually upload passports.
6. Logging is present but not structured for telemetry (latency, confidence distribution, engine hit rates, fail reasons).

## Recommendations (priority)

### High
- Move FSM passport handler to shared OCR orchestrator API.
- Add composite checksum validation and weighted MRZ confidence score.
- Add input quality gate (blur + brightness + skew) before OCR.
- Add date/country/field sanity checks to reduce false positives.

### Medium
- Add MRZ band detection and run OCR on crop first, then full-frame fallback.
- Add controlled retry policy per stage with preprocess variants.
- Add symbol confusion normalization per field type.

### Low
- Add per-engine plugin interface and telemetry exporters.
- Add richer user feedback templates based on fail reason.

## Suggested confidence model

`score in [0..1]`:
- +0.35 if MRZ 2 lines pass regex and expected lengths.
- +0.25 if 3 elementary checksums pass.
- +0.15 if composite checksum passes.
- +0.10 for valid country code + nationality.
- +0.10 for valid dates and ranges.
- +0.05 for low edit-distance between raw and normalized fields.

Map:
- high: `score >= 0.80`
- medium: `0.55..0.79`
- low: `< 0.55`

## Test expansion
- MRZ fuzz tests (char flips, inserted `<`, line shifts).
- Corrupted MRZ fixtures with single-checksum fail.
- OCR noise simulation tests for confusion map.
- Date edge cases (leap day, expired passports, future DOB).

