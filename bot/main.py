import asyncio
import io
import logging
import os
import re
import uuid
from datetime import date, datetime, timedelta

import aiohttp
import boto3
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv
from redis.asyncio import Redis

from ocr_service.pipeline import run_ocr_pipeline_v2
from utils.bitrix_integration import BitrixIntegrationError, create_bitrix_contact_and_deal

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BITRIX_WEBHOOK = os.getenv("BITRIX_WEBHOOK", "").rstrip("/")
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "")
S3_BUCKET = os.getenv("S3_BUCKET", "")
USE_REDIS = os.getenv("USE_REDIS", "false").lower() == "true"
UPSTASH_REDIS_REST_URL = os.getenv("UPSTASH_REDIS_REST_URL", "")
UPSTASH_REDIS_REST_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")

SEEN_HASHES_KEY = "seen_hashes"
SEEN_HASHES_LOCAL: set[str] = set()


class PassportFlow(StatesGroup):
    waiting_manager_code = State()
    waiting_district = State()
    waiting_district_text = State()
    waiting_address = State()
    waiting_resident_count = State()
    waiting_resident_count_text = State()
    waiting_move_date = State()
    waiting_phone = State()
    waiting_passport_photo = State()
    waiting_passport_confirm = State()
    waiting_final_confirm = State()
    waiting_final_answer = State()
    waiting_confirmation = State()


def district_keyboard() -> InlineKeyboardMarkup:
    districts = ["Центр", "Северный", "Южный", "Западный", "Восточный", "Другой"]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=name, callback_data=f"district_{name}")] for name in districts
        ]
    )


def resident_count_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="1", callback_data="count_1")],
            [InlineKeyboardButton(text="2", callback_data="count_2")],
            [InlineKeyboardButton(text="3", callback_data="count_3")],
            [InlineKeyboardButton(text="4", callback_data="count_4")],
            [InlineKeyboardButton(text="5+", callback_data="count_5+")],
        ]
    )


def passport_confirm_keyboard(low_confidence: bool) -> InlineKeyboardMarkup:
    confirm_text = "✅ Подтверждено" if low_confidence else "✅ Верно"
    rows = [[InlineKeyboardButton(text=confirm_text, callback_data="all_correct_passport")]]
    if low_confidence:
        rows.append([InlineKeyboardButton(text="✏️ Исправить", callback_data="edit_passport")])
    rows.append([InlineKeyboardButton(text="🔄 Переснять", callback_data="retake_passport")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def final_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить и отправить", callback_data="final_confirm")],
            [InlineKeyboardButton(text="❌ Начать заново", callback_data="restart")],
        ]
    )


def mask_passport_number(value: str) -> str:
    if len(value) >= 4:
        return f"{value[:2]}***{value[-2:]}"
    return value


async def ask_manager_code(message: Message, state: FSMContext) -> None:
    await state.set_state(PassportFlow.waiting_manager_code)
    await message.answer("Введите код менеджера:")


async def ask_district(message: Message, state: FSMContext) -> None:
    await state.set_state(PassportFlow.waiting_district)
    await message.answer("Укажите район объекта:", reply_markup=district_keyboard())


async def ask_address(message: Message, state: FSMContext) -> None:
    await state.set_state(PassportFlow.waiting_address)
    await message.answer("Введите адрес объекта (улица, дом, квартира):")


async def ask_resident_count(message: Message, state: FSMContext) -> None:
    await state.set_state(PassportFlow.waiting_resident_count)
    await message.answer("Сколько жильцов будет проживать?", reply_markup=resident_count_keyboard())


async def ask_move_date(message: Message, state: FSMContext) -> None:
    await state.set_state(PassportFlow.waiting_move_date)
    await message.answer("Дата заезда (ДД.ММ.ГГГГ):")


async def ask_phone(message: Message, state: FSMContext) -> None:
    await state.set_state(PassportFlow.waiting_phone)
    await message.answer("Телефон основного жильца (+7XXXXXXXXXX):")


async def ask_passport_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    resident_count = int(data.get("resident_count", 1))
    index = int(data.get("current_resident_index", 0))
    await state.set_state(PassportFlow.waiting_passport_photo)
    await message.answer(f"Жилец {index + 1} из {resident_count}. Отправьте фото паспорта.")


async def send_final_summary(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    residents = data.get("residents", [])
    low_quality_count = sum(1 for resident in residents if float(resident.get("confidence_score", 0.0)) < 0.80)

    lines = [
        "Проверьте итоговые данные:",
        f"Код менеджера: {data.get('manager_code', '')}",
        f"Район: {data.get('district', '')}",
        f"Адрес: {data.get('address', '')}",
        f"Дата заезда: {data.get('move_date', '')}",
        f"Телефон: {data.get('phone', '')}",
        "",
        "Жильцы:",
    ]

    for idx, resident in enumerate(residents, start=1):
        lines.extend(
            [
                f"{idx}) {resident.get('surname', '')} {resident.get('given_names', '')}",
                f"   Гражданство: {resident.get('nationality', '')}",
                f"   Дата рождения: {resident.get('date_of_birth', '')}",
                f"   Паспорт: {mask_passport_number(resident.get('passport_number', ''))}",
            ]
        )

    if low_quality_count:
        lines.append(f"\nВнимание: {low_quality_count} паспортов с низким качеством ⚠️")
    else:
        lines.append("\nВсе паспорта распознаны ✅")

    await state.set_state(PassportFlow.waiting_final_answer)
    await message.answer("\n".join(lines), reply_markup=final_keyboard())


async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await ask_manager_code(message, state)


async def handle_manager_code(message: Message, state: FSMContext) -> None:
    manager_code = (message.text or "").strip()
    if not manager_code or not re.fullmatch(r"[A-Za-z0-9]{4,12}", manager_code):
        await message.answer("Неверный код менеджера. Попробуйте ещё раз.")
        return

    await state.update_data(manager_code=manager_code)
    await ask_district(message, state)


async def handle_district_select(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if await state.get_state() != PassportFlow.waiting_district.state:
        return

    district = (callback.data or "").replace("district_", "", 1)
    if district == "Другой":
        await state.set_state(PassportFlow.waiting_district_text)
        await callback.message.answer("Введите район объекта текстом:")
        return

    await state.update_data(district=district)
    await ask_address(callback.message, state)


async def handle_district_text(message: Message, state: FSMContext) -> None:
    district = (message.text or "").strip()
    if not district:
        await message.answer("Неверный район. Попробуйте ещё раз.")
        return

    await state.update_data(district=district)
    await ask_address(message, state)


async def handle_address(message: Message, state: FSMContext) -> None:
    address = (message.text or "").strip()
    if len(address) < 5:
        await message.answer("Адрес слишком короткий. Введите минимум 5 символов.")
        return

    await state.update_data(address=address)
    await ask_resident_count(message, state)


async def handle_count_select(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if await state.get_state() != PassportFlow.waiting_resident_count.state:
        return

    value = (callback.data or "").replace("count_", "", 1)
    if value == "5+":
        await state.set_state(PassportFlow.waiting_resident_count_text)
        await callback.message.answer("Введите количество жильцов (от 5 до 20):")
        return

    resident_count = int(value)
    await state.update_data(resident_count=resident_count, residents=[], current_resident_index=0, retry_count=0)
    await ask_move_date(callback.message, state)


async def handle_resident_count_text(message: Message, state: FSMContext) -> None:
    raw_value = (message.text or "").strip()
    if not raw_value.isdigit():
        await message.answer("Введите целое число от 5 до 20.")
        return

    resident_count = int(raw_value)
    if resident_count < 5 or resident_count > 20:
        await message.answer("Введите целое число от 5 до 20.")
        return

    await state.update_data(resident_count=resident_count, residents=[], current_resident_index=0, retry_count=0)
    await ask_move_date(message, state)


async def handle_move_date(message: Message, state: FSMContext) -> None:
    raw_date = (message.text or "").strip()
    try:
        parsed_date = datetime.strptime(raw_date, "%d.%m.%Y").date()
    except ValueError:
        await message.answer("Неверный формат даты. Используйте ДД.ММ.ГГГГ.")
        return

    if parsed_date < date.today():
        await message.answer("Дата заезда не может быть в прошлом.")
        return

    await state.update_data(move_date=raw_date)
    await ask_phone(message, state)


async def handle_phone(message: Message, state: FSMContext) -> None:
    raw_phone = re.sub(r"\s+", "", message.text or "")
    if not re.fullmatch(r"(\+7|8)\d{10}", raw_phone):
        await message.answer("Неверный формат телефона. Используйте +7XXXXXXXXXX или 8XXXXXXXXXX.")
        return

    if raw_phone.startswith("8"):
        raw_phone = "+7" + raw_phone[1:]

    await state.update_data(phone=raw_phone)
    await ask_passport_photo(message, state)


async def handle_passport_photo(message: Message, bot: Bot, state: FSMContext) -> None:
    data = await state.get_data()
    correlation_id = str(uuid.uuid4())
    photo = message.photo[-1]

    try:
        file = await bot.get_file(photo.file_id)
        buf = io.BytesIO()
        await bot.download(file, destination=buf)
        image_bytes = buf.getvalue()
    except Exception as exc:
        logger.error('{"event":"download_failed","correlation_id":"%s","error":"%s"}', correlation_id, exc)
        await message.answer("Не удалось обработать фото. Попробуйте ещё раз.")
        return

    ocr_result = await run_ocr_pipeline_v2(image_bytes=image_bytes, correlation_id=correlation_id)
    fields = ocr_result.get("fields", {})

    if not fields:
        retry_count = int(data.get("retry_count", 0)) + 1
        await state.update_data(retry_count=retry_count)
        if retry_count >= 3:
            await message.answer("Не удалось распознать документ после 3 попыток. Пожалуйста, отправьте более чёткое фото.")
        else:
            await message.answer("Не удалось распознать MRZ. Пожалуйста, отправьте более чёткое фото.")
        return

    passport_hash = fields.get("passport_hash", "")
    presigned_url = await upload_to_s3(
        image_bytes=image_bytes,
        correlation_id=correlation_id,
        passport_hash=passport_hash,
    )

    resident_entry = {
        "surname": fields.get("surname", ""),
        "given_names": fields.get("given_names", ""),
        "date_of_birth": fields.get("date_of_birth", ""),
        "nationality": fields.get("nationality", ""),
        "passport_number": fields.get("passport_number", ""),
        "passport_hash": passport_hash,
        "presigned_url": presigned_url,
        "confidence_score": float(ocr_result.get("confidence_score", 0.0)),
        "parsing_source": ocr_result.get("parsing_source", "MRZ_local"),
        "auto_accepted": bool(ocr_result.get("auto_accepted", False)),
        "correlation_id": correlation_id,
        "confirmed": False,
    }

    residents = data.get("residents", [])
    current_index = int(data.get("current_resident_index", 0))
    if len(residents) <= current_index:
        residents.append(resident_entry)
    else:
        residents[current_index] = resident_entry

    await state.update_data(residents=residents, retry_count=0)
    await state.set_state(PassportFlow.waiting_passport_confirm)

    preview = (
        "Распознанные данные:\n"
        f"Фамилия: {resident_entry.get('surname', '')}\n"
        f"Имя: {resident_entry.get('given_names', '')}\n"
        f"Дата рождения: {resident_entry.get('date_of_birth', '')}\n"
        f"Гражданство: {resident_entry.get('nationality', '')}"
    )

    low_confidence = resident_entry["confidence_score"] < 0.80
    if low_confidence:
        preview += "\n\n⚠️ Низкое качество распознавания. Проверьте данные."

    await message.answer(preview, reply_markup=passport_confirm_keyboard(low_confidence))


async def on_confirm_passport(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    action = callback.data or ""
    data = await state.get_data()
    residents = data.get("residents", [])
    current_index = int(data.get("current_resident_index", 0))
    resident_count = int(data.get("resident_count", 1))

    if action == "retake_passport":
        await state.update_data(retry_count=0)
        await ask_passport_photo(callback.message, state)
        return

    if current_index >= len(residents):
        await callback.message.answer("Сессия устарела. Пожалуйста, начните заново командой /start.")
        return

    residents[current_index]["confirmed"] = True
    current_index += 1
    await state.update_data(residents=residents, current_resident_index=current_index, retry_count=0)

    if current_index < resident_count:
        await ask_passport_photo(callback.message, state)
        return

    await state.set_state(PassportFlow.waiting_final_confirm)
    await send_final_summary(callback.message, state)


async def on_edit_passport(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer("Исправление данных будет доступно в следующей версии. Нажмите Переснять.")


async def on_final_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await callback.answer()
    data = await state.get_data()
    residents = data.get("residents", [])

    for resident in residents:
        resident_hash = resident.get("passport_hash", "")
        if resident_hash and await is_duplicate_hash(bot, resident_hash):
            await callback.message.answer("Этот документ уже зарегистрирован")
            return

    try:
        await create_bitrix_contact_and_deal(data)
    except BitrixIntegrationError as exc:
        logger.exception("Bitrix integration failed: %s", exc)
        await callback.message.answer("❌ Ошибка отправки в Bitrix24. Попробуйте позже.")
        return
    except Exception as exc:
        logger.exception("Unexpected Bitrix integration error: %s", exc)
        await callback.message.answer("❌ Временная ошибка интеграции. Попробуйте позже.")
        return

    for resident in residents:
        resident_hash = resident.get("passport_hash", "")
        if resident_hash:
            await remember_hash(bot, resident_hash)

    await callback.message.answer("✅ Данные отправлены! Менеджер свяжется с вами.")
    await state.clear()


async def on_restart(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await ask_manager_code(callback.message, state)


async def wrong_input_photo_expected(message: Message) -> None:
    await message.answer("Пожалуйста, отправьте фото паспорта.")


async def wrong_input_text_expected(message: Message) -> None:
    await message.answer("Пожалуйста, введите текст, а не фото.")


async def on_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await callback.answer()


async def on_confirm_stale(callback: CallbackQuery) -> None:
    await callback.answer()


async def upload_to_s3(image_bytes: bytes, correlation_id: str, passport_hash: str) -> str:
    if not all([S3_ENDPOINT_URL, S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET, passport_hash]):
        return ""

    key = f"passports/{correlation_id}/{passport_hash}.jpg"

    def _upload() -> str:
        client = boto3.client(
            "s3",
            endpoint_url=S3_ENDPOINT_URL,
            aws_access_key_id=S3_ACCESS_KEY,
            aws_secret_access_key=S3_SECRET_KEY,
        )
        client.put_object(Bucket=S3_BUCKET, Key=key, Body=image_bytes, ContentType="image/jpeg")
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET, "Key": key},
            ExpiresIn=94608000,
        )

    try:
        return await asyncio.to_thread(_upload)
    except Exception as exc:
        logger.error("{\"event\":\"s3_upload_failed\",\"correlation_id\":\"%s\",\"error\":\"%s\"}", correlation_id, exc)
        return ""


async def create_bitrix_lead(fields: dict, correlation_id: str) -> int | None:
    payload = {"fields": fields}
    response = await bitrix_post("crm.lead.add", payload, correlation_id)
    if not response:
        return None
    lead_id = response.get("result")
    logger.info("{\"event\":\"bitrix_lead_response\",\"correlation_id\":\"%s\",\"lead_id\":\"%s\"}", correlation_id, lead_id)
    return int(lead_id) if lead_id else None


async def create_bitrix_deal(lead_id: int, correlation_id: str) -> int | None:
    payload = {"fields": {"TITLE": f"Telegram Lead {lead_id}", "LEAD_ID": lead_id}}
    response = await bitrix_post("crm.deal.add", payload, correlation_id)
    if not response:
        return None
    deal_id = response.get("result")
    logger.info("{\"event\":\"bitrix_deal_response\",\"correlation_id\":\"%s\",\"deal_id\":\"%s\"}", correlation_id, deal_id)
    return int(deal_id) if deal_id else None


async def bitrix_post(method: str, payload: dict, correlation_id: str) -> dict | None:
    if not BITRIX_WEBHOOK:
        return None

    url = f"{BITRIX_WEBHOOK}/{method}.json"
    for attempt in range(2):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.post(url, json=payload) as response:
                    response.raise_for_status()
                    body = await response.json()
                    return body
        except Exception as exc:
            logger.error(
                "{\"event\":\"bitrix_request_failed\",\"correlation_id\":\"%s\",\"method\":\"%s\",\"attempt\":%s,\"error\":\"%s\"}",
                correlation_id,
                method,
                attempt + 1,
                exc,
            )
            if attempt == 0:
                await asyncio.sleep(2)
    return None


async def is_duplicate_hash(bot: Bot, passport_hash: str) -> bool:
    if USE_REDIS:
        redis: Redis | None = getattr(bot, "redis_client", None)
        if redis is None:
            return False
        return bool(await redis.sismember(SEEN_HASHES_KEY, passport_hash))
    return passport_hash in SEEN_HASHES_LOCAL


async def remember_hash(bot: Bot, passport_hash: str) -> None:
    if USE_REDIS:
        redis: Redis | None = getattr(bot, "redis_client", None)
        if redis is not None:
            await redis.sadd(SEEN_HASHES_KEY, passport_hash)
            await redis.expire(SEEN_HASHES_KEY, int(timedelta(days=3650).total_seconds()))
            return
    SEEN_HASHES_LOCAL.add(passport_hash)


async def main() -> None:
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is not set. Fill .env file first.")

    storage = MemoryStorage()
    redis_client = None
    if USE_REDIS:
        try:
            if not UPSTASH_REDIS_REST_URL:
                raise ValueError("UPSTASH_REDIS_REST_URL is empty")
            redis_client = Redis.from_url(UPSTASH_REDIS_REST_URL)
            await redis_client.ping()
            storage = RedisStorage(redis=redis_client)
        except Exception as exc:
            logger.exception("Failed to initialize Redis storage, fallback to MemoryStorage: %s", exc)
            storage = MemoryStorage()
            redis_client = None

    bot = Bot(token=BOT_TOKEN)
    if redis_client is not None:
        setattr(bot, "redis_client", redis_client)

    dp = Dispatcher(storage=storage)

    dp.message.register(cmd_start, CommandStart())
    dp.message.register(handle_manager_code, PassportFlow.waiting_manager_code)
    dp.message.register(handle_address, PassportFlow.waiting_address)
    dp.message.register(handle_district_text, PassportFlow.waiting_district_text)
    dp.message.register(handle_resident_count_text, PassportFlow.waiting_resident_count_text)
    dp.message.register(handle_move_date, PassportFlow.waiting_move_date)
    dp.message.register(handle_phone, PassportFlow.waiting_phone)
    dp.message.register(handle_passport_photo, PassportFlow.waiting_passport_photo, F.photo)
    dp.message.register(wrong_input_photo_expected, PassportFlow.waiting_passport_photo)

    dp.message.register(
        wrong_input_text_expected,
        F.photo,
        PassportFlow.waiting_manager_code,
        PassportFlow.waiting_district_text,
        PassportFlow.waiting_address,
        PassportFlow.waiting_resident_count_text,
        PassportFlow.waiting_move_date,
        PassportFlow.waiting_phone,
    )

    dp.callback_query.register(handle_district_select, F.data.startswith("district_"))
    dp.callback_query.register(handle_count_select, F.data.startswith("count_"))
    dp.callback_query.register(
        on_confirm_passport,
        F.data.in_({"all_correct_passport", "retake_passport"}),
        PassportFlow.waiting_passport_confirm,
    )
    dp.callback_query.register(on_edit_passport, F.data == "edit_passport", PassportFlow.waiting_passport_confirm)
    dp.callback_query.register(on_final_confirm, F.data == "final_confirm", PassportFlow.waiting_final_answer)
    dp.callback_query.register(on_restart, F.data == "restart")

    dp.callback_query.register(on_confirm, F.data == "all_correct", PassportFlow.waiting_confirmation)
    dp.callback_query.register(on_confirm_stale, F.data == "all_correct")

    try:
        await dp.start_polling(bot)
    finally:
        if redis_client is not None:
            await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
