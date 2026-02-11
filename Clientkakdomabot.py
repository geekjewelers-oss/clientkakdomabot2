# main.py
import os
import re
import io
import logging
import tempfile
import requests
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram import F
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.state import StatesGroup, State

from PIL import Image
import pytesseract
import cv2
import numpy as np
import boto3
from botocore.client import Config
from docxtpl import DocxTemplate

# ----------------- Настройка логирования -----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ----------------- Загрузка env -----------------
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
BITRIX_WEBHOOK_URL = os.getenv("BITRIX_WEBHOOK_URL")
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY")
S3_BUCKET = os.getenv("S3_BUCKET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # опционально

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
MRZ_REGEX = re.compile(r"([A-Z0-9<]{20,})\s*[\n\r]+([A-Z0-9<]{20,})", re.MULTILINE)

def image_bytes_to_pil(img_bytes):
    return Image.open(io.BytesIO(img_bytes))

def preprocess_for_mrz_cv(image: Image.Image):
    """OpenCV preprocessing to enhance MRZ readability"""
    img = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # increase contrast / threshold
    gray = cv2.equalizeHist(gray)
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return th

# Note: use pytesseract directly on the whole image, then search for MRZ lines.
def extract_text_from_image_bytes(img_bytes):
    # PIL -> pytesseract
    pil = image_bytes_to_pil(img_bytes)
    text = pytesseract.image_to_string(pil, lang='eng')  # MRZ uses Latin charset
    return text

def find_mrz_from_text(text):
    # Normalize: remove spaces on MRZ lines
    # We look for two consecutive lines with many '<'
    candidates = MRZ_REGEX.findall(text.replace(" ", "").replace("\r", "\n"))
    if candidates:
        # MRZ_REGEX returns tuples (line1, line2)
        for l1, l2 in candidates:
            # choose first plausible (length check)
            if len(l1) >= 30 and len(l2) >= 30:
                return l1.strip(), l2.strip()
    # fallback: search for sequences with many '<'
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for i in range(len(lines)-1):
        a, b = lines[i], lines[i+1]
        if a.count('<') >= 3 and b.count('<') >= 3 and len(a) >= 25 and len(b) >= 25:
            return a.replace(" ", ""), b.replace(" ", "")
    return None, None

def parse_td3_mrz(line1: str, line2: str):
    """Parse TD3 passport MRZ (2 lines, 44 chars each normally). Returns dict with fields if possible."""
    # pad to expected lengths to avoid IndexError
    l1 = line1 + "<" * (44 - len(line1)) if len(line1) < 44 else line1
    l2 = line2 + "<" * (44 - len(line2)) if len(line2) < 44 else line2
    data = {}
    try:
        # line1
        data['document_type'] = l1[0]
        data['issuing_country'] = l1[2:5]
        names = l1[5:44].split('<<')
        surname = names[0].replace('<', ' ').strip()
        given = names[1].replace('<', ' ').strip() if len(names) > 1 else ""
        data['surname'] = surname
        data['given_names'] = given

        # line2
        data['passport_number'] = l2[0:9].replace('<', '').strip()
        data['passport_number_check'] = l2[9]
        data['nationality'] = l2[10:13].replace('<', '').strip()
        bdate = l2[13:19]
        data['birth_date'] = f"{bdate[0:2]}{bdate[2:4]}{bdate[4:6]}"  # YYMMDD
        data['sex'] = l2[20]
        expiry = l2[21:27]
        data['expiry_date'] = f"{expiry[0:2]}{expiry[2:4]}{expiry[4:6]}"
    except Exception as e:
        logger.exception("Error parsing MRZ: %s", e)
    return data

# ----------------- Bitrix helper (webhook-based) -----------------
def bitrix_call(method, params):
    """
    Simple wrapper: expects BITRIX_WEBHOOK_URL like https://yourdomain/rest/1/yourhook/
    and will POST to {BITRIX_WEBHOOK_URL}{method}.json
    """
    if not BITRIX_WEBHOOK_URL:
        logger.warning("BITRIX_WEBHOOK_URL не задана")
        return None
    url = BITRIX_WEBHOOK_URL.rstrip("/") + f"/{method}.json"
    try:
        r = requests.post(url, json=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.exception("Bitrix call failed: %s", e)
        return None

def create_lead_and_deal(client_data):
    """
    client_data: dict with keys: surname, given_names, passport_number, phone, address, etc.
    Возвращает (lead_id, deal_id)
    """
    lead_fields = {
        "TITLE": f"Лид: {client_data.get('surname', '')} {client_data.get('given_names','')}",
        "NAME": client_data.get('given_names',''),
        "LAST_NAME": client_data.get('surname',''),
        "PHONE": [{"VALUE": client_data.get('phone',''), "VALUE_TYPE": "WORK"}],
        "COMMENTS": "Авто-лид из Telegram-бота"
    }
    res_lead = bitrix_call("crm.lead.add", {"fields": lead_fields})
    lead_id = None
    if res_lead and 'result' in res_lead:
        lead_id = res_lead['result']

    # Создаём сделку и связываем с лидом (ставим флаг проверки менеджера кастомным полем "UF_CHECK_MANAGER")
    deal_fields = {
        "TITLE": f"Сделка аренда: {client_data.get('surname','')}",
        "CATEGORY_ID": 0,
        "OPPORTUNITY": client_data.get('amount',''),
        "CURRENCY_ID": "RUB",
        "LEAD_ID": lead_id,
        # пример кастомного поля для галочки менеджера: UF_CRM_... - нужно создать в Bitrix и подставить название
        # "UF_CHECK_MANAGER": False
    }
    res_deal = bitrix_call("crm.deal.add", {"fields": deal_fields})
    deal_id = None
    if res_deal and 'result' in res_deal:
        deal_id = res_deal['result']
    return lead_id, deal_id

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
        parsed = parse_td3_mrz(l1, l2)
        parsed['_mrz_raw'] = (l1, l2)
        parsed['_ocr_text_sample'] = text[:400]
        # сохраняем промежуточно
    else:
        # не нашлось MRZ — попробуем fallback local OCR (полный распознанный текст)
        parsed['_mrz_raw'] = None
        parsed['_ocr_text_sample'] = text[:400]

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
