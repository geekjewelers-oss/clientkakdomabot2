import os

from dotenv import load_dotenv

load_dotenv()


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
BITRIX_WEBHOOK_URL = os.getenv("BITRIX_WEBHOOK_URL")
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY")
S3_BUCKET = os.getenv("S3_BUCKET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
UPSTASH_REDIS_REST_URL = os.getenv("UPSTASH_REDIS_REST_URL")
UPSTASH_REDIS_REST_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")
USE_REDIS = _bool_env("USE_REDIS", False)

YANDEX_VISION_API_KEY = os.getenv("YANDEX_VISION_API_KEY")  # deprecated, используется только как последний fallback
YANDEX_VISION_FOLDER_ID = os.getenv("YANDEX_VISION_FOLDER_ID")  # deprecated, используется только как последний fallback

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")  # deprecated, используется только как последний fallback
OCR_SPACE_API_KEY = os.getenv("OCR_SPACE_API_KEY", "")
AZAPI_API_KEY = os.getenv("AZAPI_API_KEY", "")
OCR_FALLBACK_ENABLED = _bool_env("OCR_FALLBACK_ENABLED", True)
AZAPI_ENABLED = _bool_env("AZAPI_ENABLED", True)
PADDLE_LANG = os.getenv("PADDLE_LANG", "multilingual")
MIN_CONFIDENCE = _float_env("MIN_CONFIDENCE", 0.85)

OCR_SLA_MAX_LOCAL_ATTEMPTS = _int_env("OCR_SLA_MAX_LOCAL_ATTEMPTS", 2)
OCR_SLA_FALLBACK_AFTER_FAILURES = _int_env("OCR_SLA_FALLBACK_AFTER_FAILURES", 2)
OCR_SLA_FALLBACK_PROVIDER = os.getenv("OCR_SLA_FALLBACK_PROVIDER", "yandex_vision")
OCR_SLA_FALLBACK_ATTEMPTS = _int_env("OCR_SLA_FALLBACK_ATTEMPTS", 1)
OCR_SLA_FALLBACK_TIMEOUT_SECONDS = _int_env("OCR_SLA_FALLBACK_TIMEOUT_SECONDS", 5)
OCR_SLA_TOTAL_TIMEOUT_SECONDS = _int_env("OCR_SLA_TOTAL_TIMEOUT_SECONDS", 8)
OCR_SLA_FALLBACK_THRESHOLD_CONFIDENCE = _float_env("OCR_SLA_FALLBACK_THRESHOLD_CONFIDENCE", 0.55)
OCR_SLA_AUTO_ACCEPT_CONFIDENCE = _float_env("OCR_SLA_AUTO_ACCEPT_CONFIDENCE", 0.80)
OCR_SLA_MANUAL_INPUT_AFTER_SECOND_CYCLE = _bool_env("OCR_SLA_MANUAL_INPUT_AFTER_SECOND_CYCLE", True)
OCR_SLA_BREACH_THRESHOLD_RATIO = _float_env("OCR_SLA_BREACH_THRESHOLD_RATIO", 0.9)

OCR_LOG_METRICS_ENABLED = _bool_env("OCR_LOG_METRICS_ENABLED", False)
OCR_METRICS_BACKEND = os.getenv("OCR_METRICS_BACKEND", "noop")
