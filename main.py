"""Single-file Telegram bot for OCR + MRZ parsing + S3 upload + Bitrix lead creation."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import requests
from dotenv import load_dotenv

boto3: Any
try:
    import boto3 as _boto3

    boto3 = _boto3
except ModuleNotFoundError:  # pragma: no cover
    boto3 = None

Image: Any
pytesseract: Any
try:
    from PIL import Image as _Image
    import pytesseract as _pytesseract

    Image = _Image
    pytesseract = _pytesseract
except ModuleNotFoundError:  # pragma: no cover
    Image = None
    pytesseract = None


# ====== LOGGING ======
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("mrz_bot")


# ====== CONFIG ======
@dataclass(frozen=True)
class BotConfig:
    telegram_token: str
    bitrix_webhook_url: str
    s3_endpoint_url: str
    s3_bucket: str
    s3_access_key: str
    s3_secret_key: str
    s3_region: str = "us-east-1"
    s3_presign_ttl_seconds: int = 604800

    @staticmethod
    def required_env_keys() -> tuple[str, ...]:
        return (
            "TELEGRAM_TOKEN",
            "BITRIX_WEBHOOK_URL",
            "S3_ENDPOINT_URL",
            "S3_BUCKET",
            "S3_ACCESS_KEY",
            "S3_SECRET_KEY",
        )

    @classmethod
    def from_env(cls) -> BotConfig:
        load_dotenv()
        missing = [key for key in cls.required_env_keys() if not os.getenv(key)]
        if missing:
            raise ValueError(
                f"Missing required environment variables: {', '.join(sorted(missing))}"
            )
        return cls(
            telegram_token=os.environ["TELEGRAM_TOKEN"],
            bitrix_webhook_url=os.environ["BITRIX_WEBHOOK_URL"],
            s3_endpoint_url=os.environ["S3_ENDPOINT_URL"],
            s3_bucket=os.environ["S3_BUCKET"],
            s3_access_key=os.environ["S3_ACCESS_KEY"],
            s3_secret_key=os.environ["S3_SECRET_KEY"],
            s3_region=os.getenv("S3_REGION", "us-east-1"),
            s3_presign_ttl_seconds=int(os.getenv("S3_PRESIGN_TTL_SECONDS", "604800")),
        )


# ====== MRZ ======
_MRZ_CHAR_VALUES: dict[str, int] = {
    **{str(i): i for i in range(10)},
    **{chr(i + 55): i for i in range(10, 36)},
    "<": 0,
}
_MRZ_WEIGHTS: tuple[int, int, int] = (7, 3, 1)


def normalize_mrz_line(line: str, expected_len: int = 44) -> str:
    cleaned = re.sub(r"[^A-Z0-9<]", "", line.upper().strip())
    if len(cleaned) >= expected_len:
        return cleaned[:expected_len]
    return cleaned + "<" * (expected_len - len(cleaned))


def mrz_checksum(payload: str) -> int:
    total = 0
    for index, char in enumerate(payload):
        if char not in _MRZ_CHAR_VALUES:
            return -1
        total += _MRZ_CHAR_VALUES[char] * _MRZ_WEIGHTS[index % 3]
    return total % 10


def validate_mrz_checksum(payload: str, check_char: str) -> bool:
    return check_char.isdigit() and mrz_checksum(payload) == int(check_char)


def _decode_name(field: str) -> tuple[str, str]:
    primary, _, secondary = field.partition("<<")
    return primary.replace("<", " ").strip(), secondary.replace("<", " ").strip()


def _find_td3_lines(text: str) -> tuple[str, str] | None:
    lines = [normalize_mrz_line(line) for line in text.splitlines() if line.strip()]
    for index in range(len(lines) - 1):
        if (
            lines[index].startswith("P<")
            and len(lines[index]) == 44
            and len(lines[index + 1]) == 44
        ):
            return lines[index], lines[index + 1]
    return None


def parse_mrz(text: str) -> dict[str, str] | None:
    lines = _find_td3_lines(text)
    if lines is None:
        return None

    line1, line2 = lines
    passport_number = line2[0:9]
    passport_check = line2[9]
    birth_date = line2[13:19]
    birth_check = line2[19]
    expiry_date = line2[21:27]
    expiry_check = line2[27]
    personal_number = line2[28:42]
    personal_check = line2[42]
    final_check = line2[43]

    surname, given_names = _decode_name(line1[5:44])
    composite = (
        passport_number
        + passport_check
        + birth_date
        + birth_check
        + expiry_date
        + expiry_check
        + personal_number
        + personal_check
    )

    return {
        "mrz_line_1": line1,
        "mrz_line_2": line2,
        "document_code": line1[0:2],
        "issuing_country": line1[2:5],
        "surname": surname,
        "given_names": given_names,
        "passport_number": passport_number.replace("<", ""),
        "nationality": line2[10:13],
        "birth_date_yyMMdd": birth_date,
        "sex": line2[20],
        "expiry_date_yyMMdd": expiry_date,
        "personal_number": personal_number.replace("<", ""),
        "passport_number_checksum_ok": str(
            validate_mrz_checksum(passport_number, passport_check)
        ),
        "birth_date_checksum_ok": str(validate_mrz_checksum(birth_date, birth_check)),
        "expiry_date_checksum_ok": str(
            validate_mrz_checksum(expiry_date, expiry_check)
        ),
        "personal_number_checksum_ok": str(
            personal_check == "<"
            or validate_mrz_checksum(personal_number, personal_check)
        ),
        "final_checksum_ok": str(validate_mrz_checksum(composite, final_check)),
    }


# ====== OCR ======
def ocr_image_bytes(img_bytes: bytes) -> str:
    if Image is None or pytesseract is None:
        raise RuntimeError("Pillow and pytesseract are required for OCR")
    with Image.open(io.BytesIO(img_bytes)) as img:
        rgb = img.convert("RGB")
        return str(pytesseract.image_to_string(rgb, config="--oem 3 --psm 6"))


# ====== INTEGRATIONS ======
class S3Client:
    def __init__(self, config: BotConfig, boto3_session: Any | None = None) -> None:
        if boto3 is None:
            raise RuntimeError("boto3 is required for S3Client")
        self._config = config
        session = boto3_session or boto3.session.Session()
        self._client = session.client(
            "s3",
            endpoint_url=config.s3_endpoint_url,
            aws_access_key_id=config.s3_access_key,
            aws_secret_access_key=config.s3_secret_key,
            region_name=config.s3_region,
        )

    def upload_file(self, path: Path) -> str:
        key = path.name
        with path.open("rb") as file_obj:
            self._client.upload_fileobj(file_obj, self._config.s3_bucket, key)
        return str(
            self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._config.s3_bucket, "Key": key},
                ExpiresIn=self._config.s3_presign_ttl_seconds,
            )
        )


class BitrixClient:
    def __init__(
        self, webhook_url: str, session: requests.Session | None = None
    ) -> None:
        self._webhook_url = webhook_url.rstrip("/")
        self._session = session or requests.Session()

    def create_lead(self, data: dict[str, Any]) -> dict[str, Any]:
        response = self._session.post(
            f"{self._webhook_url}/crm.lead.add.json", json={"fields": data}, timeout=15
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Unexpected Bitrix response format")
        return payload


# ====== FLOW ======
@dataclass(frozen=True)
class ProcessingResult:
    ocr_text: str
    mrz_data: dict[str, str] | None
    file_url: str
    bitrix_response: dict[str, Any]


def process_passport_photo(
    *,
    photo_bytes: bytes,
    filename: str,
    s3_client: Any,
    bitrix_client: Any,
    ocr_func: Callable[[bytes], str] = ocr_image_bytes,
) -> ProcessingResult:
    ocr_text = ocr_func(photo_bytes)
    mrz_data = parse_mrz(ocr_text)

    temp_path = Path(filename)
    temp_path.write_bytes(photo_bytes)
    try:
        file_url = str(s3_client.upload_file(temp_path))
    finally:
        temp_path.unlink(missing_ok=True)

    lead_payload: dict[str, Any] = {
        "TITLE": "Passport OCR submission",
        "UF_CRM_FILE_URL": file_url,
        "COMMENTS": ocr_text[:2000],
    }
    if mrz_data is not None:
        lead_payload["UF_CRM_MRZ_JSON"] = str(mrz_data)

    bitrix_response = bitrix_client.create_lead(lead_payload)
    return ProcessingResult(
        ocr_text=ocr_text,
        mrz_data=mrz_data,
        file_url=file_url,
        bitrix_response=bitrix_response,
    )


# ====== TELEGRAM ======
def create_router() -> Any:
    from aiogram import F, Router
    from aiogram.filters import Command
    from aiogram.types import (
        CallbackQuery,
        InlineKeyboardButton,
        InlineKeyboardMarkup,
        Message,
    )

    router = Router()
    callback_confirm = "confirm_mrz"

    def keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Confirm", callback_data=callback_confirm
                    )
                ]
            ]
        )

    @router.message(Command("start"))
    async def start_handler(message: Message) -> None:
        await message.answer(
            "Send a passport photo. I will OCR it, parse MRZ, upload to S3, and create a Bitrix lead."
        )

    @router.message(F.photo)
    async def photo_handler(
        message: Message,
        bot: Any,
        s3_client: S3Client,
        bitrix_client: BitrixClient,
    ) -> None:
        if not message.photo:
            await message.answer("Please send a photo.")
            return

        tg_file = await bot.get_file(message.photo[-1].file_id)
        file_buffer = io.BytesIO()
        await bot.download(tg_file, destination=file_buffer)

        try:
            result = process_passport_photo(
                photo_bytes=file_buffer.getvalue(),
                filename=f"passport_{message.from_user.id if message.from_user else 'unknown'}.jpg",
                s3_client=s3_client,
                bitrix_client=bitrix_client,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Photo processing failed")
            await message.answer(f"Processing failed: {exc}")
            return

        parsed = result.mrz_data or {}
        await message.answer(
            "OCR done.\n"
            f"MRZ found: {'yes' if result.mrz_data else 'no'}\n"
            f"Passport number: {parsed.get('passport_number', '-')}\n"
            f"Birth date: {parsed.get('birth_date_yyMMdd', '-')}\n"
            f"Expiry date: {parsed.get('expiry_date_yyMMdd', '-')}\n"
            f"File URL: {result.file_url}",
            reply_markup=keyboard(),
        )

    @router.callback_query(F.data == callback_confirm)
    async def callback_handler(callback: CallbackQuery) -> None:
        await callback.answer("Confirmed")
        if callback.message is not None:
            await callback.message.answer("Thanks, data confirmed.")

    return router


# ====== APP ENTRY ======
async def run_bot() -> None:
    from aiogram import Bot, Dispatcher

    config = BotConfig.from_env()
    bot = Bot(token=config.telegram_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(create_router())

    s3_client = S3Client(config)
    bitrix_client = BitrixClient(config.bitrix_webhook_url)

    await dispatcher.start_polling(
        bot, s3_client=s3_client, bitrix_client=bitrix_client
    )


def main() -> None:
    try:
        asyncio.run(run_bot())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped")


if __name__ == "__main__":
    main()
