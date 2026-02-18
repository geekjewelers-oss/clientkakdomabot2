import os
from dataclasses import dataclass


@dataclass
class OCRSettings:
    local_attempts: int = int(os.getenv("OCR_LOCAL_ATTEMPTS", "2"))
    fallback_attempts: int = int(os.getenv("OCR_FALLBACK_ATTEMPTS", "1"))
    fallback_timeout: int = int(os.getenv("OCR_FALLBACK_TIMEOUT", "5"))
    total_timeout: int = int(os.getenv("OCR_TOTAL_TIMEOUT", "8"))
    fallback_threshold: float = float(os.getenv("OCR_FALLBACK_THRESHOLD", "0.55"))
    auto_accept: float = float(os.getenv("OCR_AUTO_ACCEPT", "0.80"))
    manual_after_second_cycle: bool = os.getenv("OCR_MANUAL_AFTER_SECOND_CYCLE", "true").lower() in {"1", "true", "yes"}
    sla_breach_flag: bool = os.getenv("OCR_SLA_BREACH_FLAG", "true").lower() in {"1", "true", "yes"}
    local_timeout: int = int(os.getenv("OCR_LOCAL_TIMEOUT", "2"))
    crm_retry_attempts: int = int(os.getenv("OCR_CRM_RETRY_ATTEMPTS", "3"))
    crm_retry_backoff_seconds: float = float(os.getenv("OCR_CRM_RETRY_BACKOFF_SECONDS", "0.1"))


settings = OCRSettings()
