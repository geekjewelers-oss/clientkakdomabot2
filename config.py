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

YANDEX_VISION_API_KEY = os.getenv("YANDEX_VISION_API_KEY")
YANDEX_VISION_FOLDER_ID = os.getenv("YANDEX_VISION_FOLDER_ID")
