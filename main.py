# main.py
import os
import logging
import tempfile
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram import F
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.state import StatesGroup, State

import boto3
from botocore.client import Config
from docxtpl import DocxTemplate

from bot.bitrix_api import bitrix_call, create_lead_and_deal
from bot.mrz_parser import (
    extract_mrz_from_image_bytes,
    extract_text_from_image_bytes,
    parse_td3_mrz,
)
from bot.ocr_fallback import easyocr_extract_text
from bot.vision_fallback import yandex_vision_extract_text
from config import (
    BITRIX_WEBHOOK_URL,
    OPENAI_API_KEY,
    S3_ACCESS_KEY,
    S3_BUCKET,
    S3_ENDPOINT_URL,
    S3_SECRET_KEY,
    TELEGRAM_TOKEN,
    YANDEX_VISION_API_KEY,
    YANDEX_VISION_FOLDER_ID,
)

# ----------------- Настройка логирования -----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ----------------- Загрузка env -----------------

if not TELEGRAM_TOKEN:
    logger.error("TELEGRAM_TOKEN не задан в .env")
    raise SystemExit("TELEGRAM_TOKEN required")

# ----------------- Создание бота -----------------
bot = Bot(token=TELEGRAM_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ----------------- FSM состояния -----------------
class Form(StatesGroup):
    waiting_checklist_confirmation = State()
    waiting_passport_photo = State()
    waiting_field_corrections = State()
    waiting_final_confirmation = State()

# ----------------- Утилиты: S3 (Backblaze/DigitalOcean) -----------------
def get_s3_client():
    session = boto3.session.Session()
    s3 = session.client(
        's3',
        endpoint_url=S3_ENDPOINT_URL,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        config=Config(signature_version='s3v4'),
        region_name='us-east-1'  # можно поменять
    )
    return s3

def upload_fileobj_to_s3(fileobj, filename, content_type="application/octet-stream"):
    s3 = get_s3_client()
    s3.upload_fileobj(
        Fileobj=fileobj,
        Bucket=S3_BUCKET,
        Key=filename,
        ExtraArgs={"ContentType": content_type, "ACL": "private"}
    )
    # Генерируем presigned url (по умолчанию expiry 7 дней)
    presigned = s3.generate_presigned_url(
        ClientMethod='get_object',
        Params={"Bucket": S3_BUCKET, "Key": filename},
        ExpiresIn=60*60*24*7
    )
    return presigned

# ----------------- OCR / MRZ extraction -----------------

def ocr_pipeline_extract(img_bytes) -> dict:
    line1, line2, mrz_text, _mode = extract_mrz_from_image_bytes(img_bytes)
    if line1 and line2:
        parsed = parse_td3_mrz(line1, line2)
        checksum_ok = parsed.get("_mrz_checksum_ok", False)
        confidence = "high" if checksum_ok else "medium"
        source = "mrz"
        text_value = mrz_text
        logger.info("[OCR] OCR stage: mrz, text_len=%s", len(text_value or ""))
        return {
            "text": text_value or "",
            "source": source,
            "confidence": confidence,
            "parsed": parsed,
            "mrz_lines": (line1, line2),
        }

    text = extract_text_from_image_bytes(img_bytes)
    logger.info("[OCR] OCR stage: tesseract, text_len=%s", len(text or ""))

    easy_text = easyocr_extract_text(img_bytes)
    logger.info("[OCR] OCR stage: easyocr, text_len=%s", len(easy_text or ""))
    if easy_text and len(easy_text) > 40:
        return {
            "text": easy_text,
            "source": "easyocr",
            "confidence": "medium",
            "parsed": {},
            "mrz_lines": None,
        }

    vision_text = vision_extract_text(img_bytes, current_text=easy_text or text, min_len_for_skip=60)
    logger.info("[OCR] OCR stage: vision, text_len=%s", len(vision_text or ""))

    if vision_text:
        return {
            "text": vision_text,
            "source": "vision",
            "confidence": "medium",
            "parsed": {},
            "mrz_lines": None,
        }

    return {
        "text": easy_text or text or "",
        "source": "tesseract",
        "confidence": "low",
        "parsed": {},
        "mrz_lines": None,
    }

# ----------------- Bitrix helper (webhook-based) -----------------

# ----------------- Документы: генерация docx по шаблону -----------------
def generate_contract_docx(template_path, out_path, context: dict):
    doc = DocxTemplate(template_path)
    doc.render(context)
    doc.save(out_path)
    return out_path

# ----------------- Telegram handlers -----------------

# клавиатура подтверждения
def yes_no_keyboard():
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Да, у меня есть все документы")],
        [KeyboardButton(text="Нет, хочу отправить позже")]
    ], resize_keyboard=True)
    return kb

@dp.message(Command(commands=["start"]))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Привет! Я помогу загрузить документы для заселения.\n"
        "Сначала проверим, есть ли у вас все необходимые документы.",
        reply_markup=yes_no_keyboard()
    )
    await state.set_state(Form.waiting_checklist_confirmation)

@dp.message(Form.waiting_checklist_confirmation, F.text == "Да, у меня есть все документы")
async def got_checklist_yes(message: types.Message, state: FSMContext):
    await message.answer("Отлично. Пришлите, пожалуйста, фотографию паспорта (главная страница или MRZ).", reply_markup=ReplyKeyboardRemove())
    await state.set_state(Form.waiting_passport_photo)

@dp.message(Form.waiting_checklist_confirmation, F.text == "Нет, хочу отправить позже")
async def got_checklist_no(message: types.Message, state: FSMContext):
    await message.answer("Хорошо. Напишите /start когда будете готовы.", reply_markup=ReplyKeyboardRemove())
    await state.clear()

@dp.message(Form.waiting_passport_photo, content_types=types.ContentType.ANY)
async def passport_received(message: types.Message, state: FSMContext):
    # Проверяем есть ли фото / документ
    photo_file = None
    if message.photo:
        # берем наибольшее фото
        photo_file = message.photo[-1]
        file_id = photo_file.file_id
    elif message.document and message.document.mime_type and message.document.mime_type.startswith("image"):
        file_id = message.document.file_id
    else:
        await message.answer("Пожалуйста, отправьте фото паспорта в виде фото или image-файла.")
        return

    # скачиваем файл
    file = await bot.get_file(file_id)
    file_bytes = await bot.download_file(file.file_path)
    img_bytes = file_bytes.read()  # bytes

    await message.answer("Получил фото. Пытаюсь распознать MRZ и извлечь данные... Пару секунд.")
    # извлекаем текст (local OCR)
    text = extract_text_from_image_bytes(img_bytes)
    l1, l2 = find_mrz_from_text(text)
    parsed = {}
    if l1 and l2:
        logger.info("MRZ found")
        parsed = parse_td3_mrz(l1, l2)
        parsed['_mrz_raw'] = (l1, l2)
        parsed['_ocr_text_sample'] = text[:400]
        # сохраняем промежуточно
    else:
        logger.info("MRZ not found — running EasyOCR")
        fallback_text = easyocr_extract_text(img_bytes)
        if len(fallback_text.strip()) < 20:
            logger.info("EasyOCR weak — running Vision API")
            if YANDEX_VISION_API_KEY and YANDEX_VISION_FOLDER_ID:
                fallback_text = yandex_vision_extract_text(img_bytes) or fallback_text

        parsed['_mrz_raw'] = None
        parsed['_ocr_text_sample'] = (fallback_text or text)[:400]

    # Сохраним временные данные в state
    tmp = {"parsed": parsed}
    await state.update_data(tmp)

    # Формируем сообщение для подтверждения (выводим ключевые поля)
    def format_parsed(p):
        lines = []
        lines.append(f"Фамилия: {p.get('surname','(не найдено)')}")
        lines.append(f"Имя: {p.get('given_names','(не найдено)')}")
        lines.append(f"Номер паспорта: {p.get('passport_number','(не найдено)')}")
        lines.append(f"Гражданство: {p.get('nationality','(не найдено)')}")
        lines.append(f"Дата рождения (YYMMDD): {p.get('birth_date','(не найдено)')}")
        lines.append(f"Срок действия (YYMMDD): {p.get('expiry_date','(не найдено)')}")
        return "\n".join(lines)

    msg = "Вот что я нашёл:\n\n" + format_parsed(parsed) + "\n\nЕсли что-то неверно — пришлите исправления в формате `поле: значение` (например `Номер паспорта: AB12345`), или нажмите кнопку 'Всё верно'."
    # клавиатура "Всё верно"
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Всё верно")]], resize_keyboard=True)
    await message.answer(msg, reply_markup=kb)
    await state.set_state(Form.waiting_field_corrections)

@dp.message(Form.waiting_field_corrections)
async def corrections_handler(message: types.Message, state: FSMContext):
    text = message.text.strip()
    data = await state.get_data()
    parsed = data.get('parsed', {})

    if text == "Всё верно":
        # идём дальше: запишем лид / сделку в Bitrix
        await message.answer("Отлично — сохраняю данные и создаю лид в CRM...", reply_markup=ReplyKeyboardRemove())
        # собираем client_data
        client_data = {
            "surname": parsed.get('surname'),
            "given_names": parsed.get('given_names'),
            "passport_number": parsed.get('passport_number'),
            "nationality": parsed.get('nationality'),
            "birth_date": parsed.get('birth_date'),
            # phone/address можно спросить дополнительно
        }
        # Создаём лид и сделку
        lead_id, deal_id = create_lead_and_deal(client_data)
        await message.answer(f"Лид создан: {lead_id}, Сделка: {deal_id}\nДалее сгенерирую договор и загружу файлы.")
        # Генерируем договор (пример: template.docx должен быть в папке)
        template_path = "contract_template.docx"
        tmp_docx = f"contract_{lead_id}.docx"
        # если нет шаблона, создаём простой документ в runtime
        context = {
            "surname": client_data.get('surname',''),
            "given_names": client_data.get('given_names',''),
            "passport_number": client_data.get('passport_number',''),
            "address": client_data.get('address',''),
        }
        try:
            if os.path.exists(template_path):
                generate_contract_docx(template_path, tmp_docx, context)
                # загрузка в S3
                with open(tmp_docx, "rb") as f:
                    presigned = upload_fileobj_to_s3(f, f"contracts/{tmp_docx}", content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
                # добавляем в комментарий сделки/лида
                if deal_id:
                    bitrix_call("crm.activity.add", {"fields": {"OWNER_ID": deal_id, "OWNER_TYPE_ID": 2, "SUBJECT": "Договор", "DESCRIPTION": presigned}})
                await message.answer("Договор сгенерирован и загружен. Ссылка доступна в CRM.")
            else:
                await message.answer("Шаблон договора не найден (contract_template.docx). Пропускаю генерацию.")
        except Exception as e:
            logger.exception("Error generating/uploading contract: %s", e)
            await message.answer("Ошибка при генерации/загрузке договора. Смотри логи на сервере.")
        await state.clear()
        return

    # Обработка корректировок в формате "Поле: значение"
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
            "срок действия": "expiry_date"
        }
        if key in field_map:
            parsed[field_map[key]] = val
            await state.update_data({"parsed": parsed})
            await message.answer(f"Поле `{key}` обновлено на `{val}`. Если всё готово — нажмите 'Всё верно' или введите другие исправления.")
        else:
            await message.answer("Не распознал поле для правки. Попробуй: 'Фамилия: Иванов' или 'Номер паспорта: AB1234567'.")
    else:
        await message.answer("Не понял. Для подтверждения нажмите 'Всё верно' или пришлите исправление в формате `Поле: значение`.")

# ----------------- Запуск -----------------
if __name__ == "__main__":
    from aiogram import executor
    logger.info("Запускаю бота...")
    executor.start_polling(dp, skip_updates=True)
