import asyncio
import io
import logging
import os
import uuid
from datetime import timedelta

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

from mrz_parser import run_ocr_pipeline

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
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

SEEN_HASHES_KEY = "seen_hashes"
SEEN_HASHES_LOCAL: set[str] = set()


class PassportFlow(StatesGroup):
    waiting_confirmation = State()


async def cmd_start(message: Message) -> None:
    await message.answer("Привет! Отправьте фото паспорта, и я попробую распознать MRZ-данные.")


async def handle_photo(message: Message, bot: Bot, state: FSMContext) -> None:
    correlation_id = str(uuid.uuid4())
    photo = message.photo[-1]

    try:
        file = await bot.get_file(photo.file_id)
        buf = io.BytesIO()
        await bot.download(file, destination=buf)
        image_bytes = buf.getvalue()
    except Exception as exc:
        logger.error("{\"event\":\"download_failed\",\"correlation_id\":\"%s\",\"error\":\"%s\"}", correlation_id, exc)
        await message.answer("Не удалось обработать фото. Попробуйте ещё раз.")
        return

    ocr_result = await run_ocr_pipeline(image_bytes=image_bytes, correlation_id=correlation_id)
    fields = ocr_result.get("fields", {})

    if not fields:
        await message.answer("Не удалось распознать MRZ. Пожалуйста, отправьте более чёткое фото.")
        return

    passport_hash = fields.get("passport_hash", "")
    presigned_url = await upload_to_s3(
        image_bytes=image_bytes,
        correlation_id=correlation_id,
        passport_hash=passport_hash,
    )

    await state.update_data(
        correlation_id=correlation_id,
        fields=fields,
        confidence_score=ocr_result.get("confidence_score", 0.0),
        parsing_source=ocr_result.get("parsing_source", "MRZ_local"),
        auto_accepted=ocr_result.get("auto_accepted", False),
        sla_breach=ocr_result.get("sla_breach", False),
        passport_hash=passport_hash,
        presigned_url=presigned_url,
    )
    await state.set_state(PassportFlow.waiting_confirmation)

    response = (
        "Распознанные данные:\n"
        f"Фамилия: {fields.get('surname', '')}\n"
        f"Имя: {fields.get('given_names', '')}\n"
        f"Дата рождения: {fields.get('date_of_birth', '')}\n"
        f"Гражданство: {fields.get('nationality', '')}\n\n"
        "Проверьте, пожалуйста, корректность данных."
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Всё верно", callback_data="all_correct")]]
    )
    await message.answer(response, reply_markup=keyboard)


async def on_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    if not data:
        await callback.message.answer("Сессия устарела. Пожалуйста, отправьте фото паспорта заново.")
        await state.clear()
        await callback.answer()
        return

    correlation_id = data.get("correlation_id", str(uuid.uuid4()))
    passport_hash = data.get("passport_hash", "")

    if not passport_hash:
        await callback.message.answer("Недостаточно данных для регистрации. Отправьте фото ещё раз.")
        await state.clear()
        return

    duplicate = await is_duplicate_hash(bot, passport_hash)
    if duplicate:
        await callback.message.answer("Этот документ уже зарегистрирован")
        await state.clear()
        return

    await remember_hash(bot, passport_hash)

    bitrix_payload = {
        "NAME": data.get("fields", {}).get("given_names", ""),
        "LAST_NAME": data.get("fields", {}).get("surname", ""),
        "BIRTHDATE": data.get("fields", {}).get("date_of_birth", ""),
        "UF_CRM_PASSPORT_HASH": passport_hash,
        "UF_CRM_PASSPORT_DOC_URL": data.get("presigned_url", ""),
        "UF_CRM_CORRELATION_ID": correlation_id,
        "UF_CRM_OCR_SOURCE": data.get("parsing_source", "MRZ_local"),
        "UF_CRM_CONFIDENCE": str(data.get("confidence_score", 0.0)),
        "SOURCE_ID": "TELEGRAM_BOT",
    }

    lead_id = await create_bitrix_lead(bitrix_payload, correlation_id)
    if not lead_id:
        await callback.message.answer("Не удалось создать заявку. Попробуйте позже.")
        await state.clear()
        return

    await create_bitrix_deal(lead_id, correlation_id)
    await callback.message.answer("Спасибо! Данные подтверждены и отправлены.")
    await state.clear()


async def on_confirm_stale(callback: CallbackQuery) -> None:
    """Handle 'all_correct' press outside of valid FSM state (stale message)."""
    await callback.answer(
        "Эта кнопка уже неактуальна. Отправьте новое фото паспорта.",
        show_alert=True,
    )


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
        redis_client = Redis.from_url(REDIS_URL)
        storage = RedisStorage(redis=redis_client)

    bot = Bot(token=BOT_TOKEN)
    if redis_client is not None:
        setattr(bot, "redis_client", redis_client)

    dp = Dispatcher(storage=storage)

    dp.message.register(cmd_start, CommandStart())
    dp.message.register(handle_photo, F.photo)
    dp.callback_query.register(on_confirm, F.data == "all_correct", PassportFlow.waiting_confirmation)
    dp.callback_query.register(on_confirm_stale, F.data == "all_correct")

    try:
        await dp.start_polling(bot)
    finally:
        if redis_client is not None:
            await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
