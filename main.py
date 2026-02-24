# =========================
# Imports
# =========================
import asyncio
import base64
import hashlib
import io
import logging
import os
import re
from pathlib import Path
from typing import Any

import boto3
import cv2
import numpy as np
import pytesseract
import requests
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove
from botocore.client import Config
from dotenv import load_dotenv
from PIL import Image


# =========================
# Config / env loading
# =========================
load_dotenv()


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("BOT_TOKEN", "")
BITRIX_WEBHOOK_URL = os.getenv("BITRIX_WEBHOOK_URL", "")
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "")
S3_BUCKET = os.getenv("S3_BUCKET", "")
YANDEX_VISION_API_KEY = os.getenv("YANDEX_VISION_API_KEY", "")
YANDEX_VISION_FOLDER_ID = os.getenv("YANDEX_VISION_FOLDER_ID", "")

S3_REGION = os.getenv("S3_REGION", "us-east-1")
DOWNLOADS_DIR = Path(os.getenv("DOWNLOADS_DIR", "downloads"))
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

OCR_MIN_EASYOCR_LEN = _int_env("OCR_MIN_EASYOCR_LEN", 40)
OCR_SKIP_VISION_IF_LEN = _int_env("OCR_SKIP_VISION_IF_LEN", 60)

BITRIX_DEAL_FIELDS = {
    "surname": "UF_CRM_PASSPORT_SURNAME",
    "given_names": "UF_CRM_PASSPORT_NAME",
    "passport_number": "UF_CRM_PASSPORT_NUMBER",
    "nationality": "UF_CRM_PASSPORT_NATION",
    "birth_date": "UF_CRM_BIRTH_DATE",
    "expiry_date": "UF_CRM_PASSPORT_EXPIRY",
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =========================
# MRZ parsing functions
# =========================
MRZ_REGEX = re.compile(r"([A-Z0-9<]{20,})\s*[\n\r]+([A-Z0-9<]{20,})", re.MULTILINE)
_CHECKSUM_WEIGHTS = (7, 3, 1)
NUM_MAP = {"O": "0", "Q": "0", "I": "1", "L": "1", "B": "8", "S": "5", "G": "6"}


def compute_mrz_hash(line1: str | None, line2: str | None) -> str | None:
    l1 = (line1 or "").strip()
    l2 = (line2 or "").strip()
    if not l1 and not l2:
        return None
    value = f"{l1}|{l2}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest().lower()


def image_bytes_to_pil(img_bytes: bytes) -> Image.Image:
    return Image.open(io.BytesIO(img_bytes))


def preprocess_for_mrz_cv_mode(image: Image.Image, mode: str = "current") -> np.ndarray:
    """Preprocess image for MRZ OCR using one of supported modes."""
    img = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)

    if mode == "adaptive":
        return cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31,
            2,
        )

    if mode == "morphology":
        _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        return cv2.morphologyEx(th, cv2.MORPH_CLOSE, kernel)

    # current threshold mode
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return th


def extract_text_from_image_bytes(img_bytes: bytes) -> str:
    pil = image_bytes_to_pil(img_bytes)
    return pytesseract.image_to_string(pil, lang="eng")


def find_mrz_from_text(text: str) -> tuple[str | None, str | None]:
    candidates = MRZ_REGEX.findall(text.replace(" ", "").replace("\r", "\n"))
    if candidates:
        for l1, l2 in candidates:
            if len(l1) >= 30 and len(l2) >= 30:
                return l1.strip(), l2.strip()

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for idx in range(len(lines) - 1):
        line_a, line_b = lines[idx], lines[idx + 1]
        if line_a.count("<") >= 3 and line_b.count("<") >= 3 and len(line_a) >= 25 and len(line_b) >= 25:
            return line_a.replace(" ", ""), line_b.replace(" ", "")

    return None, None


def extract_mrz_from_image_bytes(img_bytes: bytes) -> tuple[str | None, str | None, str, str | None]:
    """Run MRZ extraction on multiple preprocess variants until MRZ lines are found."""
    pil = image_bytes_to_pil(img_bytes)
    preprocess_modes = ("current", "adaptive", "morphology")

    for mode in preprocess_modes:
        try:
            processed = preprocess_for_mrz_cv_mode(pil, mode=mode)
            text = pytesseract.image_to_string(processed, lang="eng")
        except Exception as exc:
            logger.warning("[OCR] MRZ preprocess failed: mode=%s, error=%s", mode, exc)
            continue

        line1, line2 = find_mrz_from_text(text)
        if line1 and line2:
            logger.info("[OCR] MRZ found with preprocess=%s", mode)
            return line1, line2, text, mode

    return None, None, "", None


def _mrz_char_value(ch: str) -> int:
    if ch.isdigit():
        return int(ch)
    if "A" <= ch <= "Z":
        return ord(ch) - ord("A") + 10
    if ch == "<":
        return 0
    return 0


def compute_mrz_checksum(value: str) -> int:
    total = 0
    for idx, ch in enumerate(value):
        total += _mrz_char_value(ch) * _CHECKSUM_WEIGHTS[idx % 3]
    return total % 10


def normalize_for_numeric(value: str) -> str:
    value = value.upper()
    return "".join(NUM_MAP.get(ch, ch) for ch in value)


def validate_mrz_checksum(value: str, check_char: str) -> bool:
    if not check_char or not check_char.isdigit():
        return False
    return compute_mrz_checksum(value) == int(check_char)


def validate_td3_composite(line2: str) -> bool:
    if len(line2) < 44:
        line2 = line2 + "<" * (44 - len(line2))

    composite_check = line2[43]
    part_doc = normalize_for_numeric(line2[0:10])
    part_birth = normalize_for_numeric(line2[13:20])
    part_exp = normalize_for_numeric(line2[21:28])
    optional = line2[28:43]

    composite_value = part_doc + part_birth + part_exp + optional
    return validate_mrz_checksum(composite_value, composite_check)


def parse_td3_mrz(line1: str, line2: str) -> dict[str, Any]:
    """Parse TD3 passport MRZ (2 lines, 44 chars each normally)."""
    l1 = line1 + "<" * (44 - len(line1)) if len(line1) < 44 else line1
    l2 = line2 + "<" * (44 - len(line2)) if len(line2) < 44 else line2

    data: dict[str, Any] = {}
    checks: dict[str, bool] = {}

    try:
        data["document_type"] = l1[0]
        data["issuing_country"] = l1[2:5]
        names = l1[5:44].split("<<")
        data["surname"] = names[0].replace("<", " ").strip()
        data["given_names"] = names[1].replace("<", " ").strip() if len(names) > 1 else ""

        passport_number_raw = l2[0:9]
        passport_check = l2[9]
        birth_date_raw = l2[13:19]
        birth_check = l2[19]
        expiry_raw = l2[21:27]
        expiry_check = l2[27]

        passport_number_norm = normalize_for_numeric(passport_number_raw)
        birth_date_norm = normalize_for_numeric(birth_date_raw)
        expiry_norm = normalize_for_numeric(expiry_raw)

        data["passport_number"] = passport_number_raw.replace("<", "").strip()
        data["passport_number_check"] = passport_check
        data["nationality"] = l2[10:13].replace("<", "").strip()
        data["birth_date"] = f"{birth_date_raw[0:2]}{birth_date_raw[2:4]}{birth_date_raw[4:6]}"
        data["sex"] = l2[20]
        data["expiry_date"] = f"{expiry_raw[0:2]}{expiry_raw[2:4]}{expiry_raw[4:6]}"

        checks["passport_number"] = validate_mrz_checksum(passport_number_norm, passport_check)
        checks["birth_date"] = validate_mrz_checksum(birth_date_norm, birth_check)
        checks["expiry_date"] = validate_mrz_checksum(expiry_norm, expiry_check)
        checks["composite"] = validate_td3_composite(l2)
    except Exception:
        logger.exception("[OCR] Error parsing MRZ")
        checks = {"passport_number": False, "birth_date": False, "expiry_date": False, "composite": False}

    check_weights = {
        "passport_number": 0.2,
        "birth_date": 0.2,
        "expiry_date": 0.2,
        "composite": 0.4,
    }
    mrz_confidence_score = sum(weight for key, weight in check_weights.items() if checks.get(key))
    data["_mrz_checksum_ok"] = all(checks.get(key, False) for key in check_weights)
    data["mrz_confidence_score"] = float(mrz_confidence_score)
    return data


# =========================
# OCR functions
# =========================
YANDEX_VISION_ENDPOINT = "https://vision.api.cloud.yandex.net/vision/v1/batchAnalyze"


def yandex_vision_extract_text(image_bytes: bytes) -> str:
    if not YANDEX_VISION_API_KEY or not YANDEX_VISION_FOLDER_ID:
        logger.info("[OCR] Yandex Vision credentials are not configured")
        return ""

    content = base64.b64encode(image_bytes).decode("utf-8")
    payload = {
        "folderId": YANDEX_VISION_FOLDER_ID,
        "analyze_specs": [
            {
                "content": content,
                "features": [{"type": "TEXT_DETECTION", "text_detection_config": {"languageCodes": ["en"]}}],
            }
        ],
    }
    headers = {"Authorization": f"Api-Key {YANDEX_VISION_API_KEY}", "Content-Type": "application/json"}

    try:
        response = requests.post(YANDEX_VISION_ENDPOINT, json=payload, headers=headers, timeout=20)
        response.raise_for_status()
    except requests.RequestException:
        logger.exception("[OCR] Yandex Vision request failed")
        return ""

    words: list[str] = []
    for analyzed in response.json().get("results", []):
        for result in analyzed.get("results", []):
            text_detection = result.get("textDetection", {})
            for page in text_detection.get("pages", []):
                for block in page.get("blocks", []):
                    for line in block.get("lines", []):
                        for word in line.get("words", []):
                            text = word.get("text")
                            if text:
                                words.append(text)

    extracted_text = " ".join(words).strip()
    logger.info("[OCR] Yandex Vision text_len=%s", len(extracted_text))
    return extracted_text


# РЕЗЕРВНЫЙ OCR (Tesseract + MRZ парсинг) — раскомментировать если нужно
# def ocr_pipeline_extract(img_bytes: bytes) -> dict[str, Any]:
#     from bot.mrz_parser import parse_td3_mrz
#
#     line1, line2, mrz_text, _mode = extract_mrz_from_image_bytes(img_bytes)
#     if line1 and line2:
#         parsed = parse_td3_mrz(line1, line2)
#         if parsed.get("_mrz_checksum_ok") is True:
#             return {
#                 "text": mrz_text or "",
#                 "source": "mrz",
#                 "confidence": "high",
#                 "parsed": parsed,
#                 "mrz_lines": (line1, line2),
#             }
#
#         confidence = 0.0
#         logger.info("[OCR] MRZ checksum failed, fallback to Gemini; confidence=%s", confidence)
#
#     text = extract_text_from_image_bytes(img_bytes)
#     logger.info("[OCR] Tesseract text_len=%s", len(text or ""))
#
#     from bot.ocr_gemini import gemini_vision_extract
#
#     image_bytes = img_bytes
#     gemini_data = gemini_vision_extract(image_bytes)
#     if gemini_data.get("confidence_score", 0) > 0:
#         return {
#             "fields": {
#                 "surname": gemini_data.get("surname", ""),
#                 "given_names": gemini_data.get("given_names", ""),
#                 "passport_number": gemini_data.get("passport_number", ""),
#                 "nationality": gemini_data.get("nationality", ""),
#                 "date_of_birth": gemini_data.get("birth_date", ""),
#             },
#             "confidence_score": gemini_data.get("confidence_score", 0),
#             "parsing_source": "gemini",
#             "auto_accepted": True,
#         }
#
#     vision_text = ""
#     if len((text or "").strip()) < OCR_SKIP_VISION_IF_LEN:
#         vision_text = yandex_vision_extract_text(img_bytes)
#
#     if vision_text:
#         return {
#             "text": vision_text,
#             "source": "vision",
#             "confidence": "medium",
#             "parsed": {},
#             "mrz_lines": None,
#         }
#
#     return {
#         "text": text or "",
#         "source": "tesseract",
#         "confidence": "low",
#         "parsed": {},
#         "mrz_lines": None,
#     }


def ocr_pipeline_extract(image_bytes: bytes) -> dict:
    logger.info("OCR_PIPELINE_CALLED: using Gemini")
    from bot.ocr_gemini import gemini_vision_extract

    gemini_data = gemini_vision_extract(image_bytes)
    return {
        "fields": {
            "surname": gemini_data.get("surname", ""),
            "given_names": gemini_data.get("given_names", ""),
            "passport_number": gemini_data.get("passport_number", ""),
            "nationality": gemini_data.get("nationality", ""),
            "date_of_birth": gemini_data.get("birth_date", ""),
        },
        "confidence_score": gemini_data.get("confidence_score", 0.0),
        "parsing_source": "gemini",
        "auto_accepted": gemini_data.get("confidence_score", 0.0) >= 0.7,
    }


# =========================
# S3 upload functions
# =========================
def get_s3_client():
    if not (S3_ENDPOINT_URL and S3_ACCESS_KEY and S3_SECRET_KEY and S3_BUCKET):
        return None

    session = boto3.session.Session()
    return session.client(
        "s3",
        endpoint_url=S3_ENDPOINT_URL,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        config=Config(signature_version="s3v4"),
        region_name=S3_REGION,
    )


def upload_bytes_to_s3(data: bytes, key: str, content_type: str = "application/octet-stream") -> str | None:
    s3 = get_s3_client()
    if s3 is None:
        logger.warning("[S3] S3 credentials are not configured")
        return None

    fileobj = io.BytesIO(data)
    s3.upload_fileobj(
        Fileobj=fileobj,
        Bucket=S3_BUCKET,
        Key=key,
        ExtraArgs={"ContentType": content_type, "ACL": "private"},
    )

    return s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": S3_BUCKET, "Key": key},
        ExpiresIn=60 * 60 * 24 * 7,
    )


# =========================
# Bitrix API functions
# =========================
def bitrix_call(method: str, params: dict[str, Any]) -> dict[str, Any] | None:
    if not BITRIX_WEBHOOK_URL:
        logger.warning("[Bitrix] BITRIX_WEBHOOK_URL is not configured")
        return None

    url = BITRIX_WEBHOOK_URL.rstrip("/") + f"/{method}.json"
    try:
        response = requests.post(url, json=params, timeout=15)
        response.raise_for_status()
        return response.json()
    except Exception:
        logger.exception("[Bitrix] Request failed: method=%s", method)
        return None


def create_lead_and_deal(client_data: dict[str, Any]) -> tuple[Any, Any]:
    lead_fields = {
        "TITLE": f"Лид: {client_data.get('surname', '')} {client_data.get('given_names', '')}",
        "NAME": client_data.get("given_names", ""),
        "LAST_NAME": client_data.get("surname", ""),
        "PHONE": [{"VALUE": client_data.get("phone", ""), "VALUE_TYPE": "WORK"}],
        "COMMENTS": "Авто-лид из Telegram-бота",
    }

    lead_resp = bitrix_call("crm.lead.add", {"fields": lead_fields})
    lead_id = lead_resp.get("result") if lead_resp else None

    deal_fields = {
        "TITLE": f"Сделка аренда: {client_data.get('surname', '')}",
        "CATEGORY_ID": 0,
        "OPPORTUNITY": client_data.get("amount", ""),
        "CURRENCY_ID": "RUB",
        "LEAD_ID": lead_id,
    }

    for client_key, bitrix_field in BITRIX_DEAL_FIELDS.items():
        value = client_data.get(client_key)
        if value:
            deal_fields[bitrix_field] = value

    deal_resp = bitrix_call("crm.deal.add", {"fields": deal_fields})
    deal_id = deal_resp.get("result") if deal_resp else None

    if lead_id is None:
        logger.error("[Bitrix] Failed creating lead")
    if deal_id is None:
        logger.error("[Bitrix] Failed creating deal")

    return lead_id, deal_id


# =========================
# Telegram bot handler functions
# =========================
class Form(StatesGroup):
    waiting_checklist_confirmation = State()
    waiting_passport_photo = State()
    waiting_field_corrections = State()


def yes_no_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Да, у меня есть все документы")],
            [KeyboardButton(text="Нет, хочу отправить позже")],
        ],
        resize_keyboard=True,
    )


def all_correct_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Всё верно")]],
        resize_keyboard=True,
    )


def format_parsed(parsed: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"Фамилия: {parsed.get('surname', '(не найдено)')}",
            f"Имя: {parsed.get('given_names', '(не найдено)')}",
            f"Номер паспорта: {parsed.get('passport_number', '(не найдено)')}",
            f"Гражданство: {parsed.get('nationality', '(не найдено)')}",
            f"Дата рождения (YYMMDD): {parsed.get('birth_date', '(не найдено)')}",
            f"Срок действия (YYMMDD): {parsed.get('expiry_date', '(не найдено)')}",
        ]
    )


def register_handlers(dp: Dispatcher, bot: Bot) -> None:
    @dp.message(Command("start"))
    async def cmd_start(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer(
            "Привет! Я помогу загрузить документы для заселения.\n"
            "Сначала проверим, есть ли у вас все необходимые документы.",
            reply_markup=yes_no_keyboard(),
        )
        await state.set_state(Form.waiting_checklist_confirmation)

    @dp.message(Form.waiting_checklist_confirmation, F.text == "Да, у меня есть все документы")
    async def got_checklist_yes(message: Message, state: FSMContext) -> None:
        await message.answer(
            "Отлично. Пришлите, пожалуйста, фотографию паспорта (главная страница или MRZ).",
            reply_markup=ReplyKeyboardRemove(),
        )
        await state.set_state(Form.waiting_passport_photo)

    @dp.message(Form.waiting_checklist_confirmation, F.text == "Нет, хочу отправить позже")
    async def got_checklist_no(message: Message, state: FSMContext) -> None:
        await message.answer("Хорошо. Напишите /start когда будете готовы.", reply_markup=ReplyKeyboardRemove())
        await state.clear()

    @dp.message(Form.waiting_passport_photo)
    async def passport_received(message: Message, state: FSMContext) -> None:
        file_id = None
        content_type = "image/jpeg"

        if message.photo:
            file_id = message.photo[-1].file_id
        elif message.document and message.document.mime_type and message.document.mime_type.startswith("image"):
            file_id = message.document.file_id
            content_type = message.document.mime_type

        if not file_id:
            await message.answer("Пожалуйста, отправьте фото паспорта в виде фото или image-файла.")
            return

        file_info = await bot.get_file(file_id)
        image_stream = await bot.download_file(file_info.file_path)
        image_bytes = image_stream.read()

        await message.answer("Получил фото. Пытаюсь распознать данные... Пару секунд.")
        ocr_result = ocr_pipeline_extract(image_bytes)
        parsed = ocr_result.get("parsed") or {}
        if not parsed:
            line1, line2 = find_mrz_from_text(ocr_result.get("text", ""))
            if line1 and line2:
                parsed = parse_td3_mrz(line1, line2)

        await state.update_data({"parsed": parsed, "passport_bytes": image_bytes, "passport_content_type": content_type})

        msg = (
            "Вот что я нашёл:\n\n"
            + format_parsed(parsed)
            + "\n\nЕсли что-то неверно — пришлите исправления в формате `поле: значение` "
            + "(например `Номер паспорта: AB12345`), или нажмите кнопку 'Всё верно'."
        )
        await message.answer(msg, reply_markup=all_correct_keyboard(), parse_mode="Markdown")
        await state.set_state(Form.waiting_field_corrections)

    @dp.message(Form.waiting_field_corrections)
    async def corrections_handler(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        data = await state.get_data()
        parsed = data.get("parsed", {})

        if text == "Всё верно":
            await message.answer("Отлично — сохраняю данные и создаю лид в CRM...", reply_markup=ReplyKeyboardRemove())

            client_data = {
                "surname": parsed.get("surname"),
                "given_names": parsed.get("given_names"),
                "passport_number": parsed.get("passport_number"),
                "nationality": parsed.get("nationality"),
                "birth_date": parsed.get("birth_date"),
                "expiry_date": parsed.get("expiry_date"),
            }
            lead_id, deal_id = create_lead_and_deal(client_data)

            passport_bytes = data.get("passport_bytes", b"")
            if passport_bytes:
                s3_key = f"passports/{message.from_user.id}_{message.message_id}.jpg"
                try:
                    file_url = upload_bytes_to_s3(passport_bytes, key=s3_key, content_type=data.get("passport_content_type", "image/jpeg"))
                    if file_url and deal_id:
                        bitrix_call(
                            "crm.activity.add",
                            {
                                "fields": {
                                    "OWNER_ID": deal_id,
                                    "OWNER_TYPE_ID": 2,
                                    "SUBJECT": "Фото паспорта",
                                    "DESCRIPTION": file_url,
                                }
                            },
                        )
                except Exception:
                    logger.exception("[S3] Failed to upload passport image")

            await message.answer(f"Лид создан: {lead_id}, Сделка: {deal_id}")
            await state.clear()
            return

        if ":" in text:
            key, val = text.split(":", 1)
            key = key.strip().lower()
            val = val.strip()
            field_map = {
                "фамилия": "surname",
                "имя": "given_names",
                "номер паспорта": "passport_number",
                "паспорт": "passport_number",
                "гражданство": "nationality",
                "дата рождения": "birth_date",
                "срок действия": "expiry_date",
            }
            if key in field_map:
                parsed[field_map[key]] = val
                await state.update_data({"parsed": parsed})
                await message.answer(
                    f"Поле `{key}` обновлено на `{val}`. Если всё готово — нажмите 'Всё верно'.",
                    parse_mode="Markdown",
                )
            else:
                await message.answer("Не распознал поле для правки. Пример: `Фамилия: Иванов`", parse_mode="Markdown")
            return

        await message.answer(
            "Не понял. Для подтверждения нажмите 'Всё верно' или пришлите исправление в формате `Поле: значение`.",
            parse_mode="Markdown",
        )


# =========================
# Main polling loop
# =========================
async def run_bot() -> None:
    if not TELEGRAM_TOKEN:
        raise SystemExit("TELEGRAM_TOKEN (or BOT_TOKEN) required")

    bot = Bot(token=TELEGRAM_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    register_handlers(dp, bot)

    logger.info("Запускаю Telegram-бота...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(run_bot())
