# Security and Privacy Guidelines

## OCR Logging Rules

- Never log raw MRZ lines.
- Never log full passport numbers.
- Never log source passport images, paths, or raw bytes.
- Structured OCR SLA logs may include only:
  - `passport_hash` (SHA-256 of normalized MRZ lines)
  - `passport_mrz_len`
  - operational SLA/decision fields
- If MRZ is missing, avoid logging unmasked passport identifiers.

## Metrics and Observability

- OCR SLA metrics are optional and disabled by default.
- Enable in staging first using:
  - `OCR_LOG_METRICS_ENABLED=true`
  - `OCR_METRICS_BACKEND=prometheus` or `statsd`
- Keep metric payloads free of sensitive personal data.
