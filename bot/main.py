import asyncio
import os
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv

from mrz_parser import extract_text_from_image_bytes, find_mrz_from_text, parse_td3_mrz

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DOWNLOADS_DIR = Path("downloads")
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)


def send_to_bitrix_stub(data: dict) -> None:
    """Stub for future Bitrix integration."""
    _ = data


def upload_to_s3_stub(file_path: Path) -> None:
    """Stub for future S3 integration."""
    _ = file_path


async def cmd_start(message: Message) -> None:
    await message.answer(
        "Привет! Отправьте фото паспорта, и я попробую распознать MRZ-данные."
    )


async def handle_photo(message: Message, bot: Bot) -> None:
    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)

    destination = DOWNLOADS_DIR / f"{photo.file_id}.jpg"
    await bot.download_file(file_info.file_path, destination)

    image_bytes = destination.read_bytes()
    text = extract_text_from_image_bytes(image_bytes)
    mrz_lines = find_mrz_from_text(text)

    if not mrz_lines:
        await message.answer("Не удалось найти MRZ в изображении. Попробуйте более четкое фото.")
        return

    parsed = parse_td3_mrz(mrz_lines)

    upload_to_s3_stub(destination)
    send_to_bitrix_stub(parsed)

    response = (
        "Распознанные данные:\n"
        f"Фамилия: {parsed.get('surname', '')}\n"
        f"Имя: {parsed.get('name', '')}\n"
        f"Номер паспорта: {parsed.get('passport_number', '')}\n"
        f"Дата рождения: {parsed.get('birth_date', '')}\n"
        f"Срок действия: {parsed.get('expiry_date', '')}\n"
        f"Гражданство: {parsed.get('nationality', '')}"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Все верно", callback_data="all_correct")]]
    )

    await message.answer(response, reply_markup=keyboard)


async def main() -> None:
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is not set. Fill .env file first.")

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    dp.message.register(cmd_start, CommandStart())
    dp.message.register(handle_photo, F.photo)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
