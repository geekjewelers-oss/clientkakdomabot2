import io
import logging
import re
from datetime import datetime
from typing import Any

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardRemove

from bot.fsm.states import Form
from bot.keyboards.registration_kb import (
    ADD_ANOTHER_NO_TEXT,
    ADD_ANOTHER_YES_TEXT,
    CANCEL_TEXT,
    CONFIRM_TEXT,
    DISTRICTS,
    MANAGERS,
    NO_TEXT,
    YES_TEXT,
    add_another_keyboard,
    confirm_keyboard,
    district_keyboard,
    manager_keyboard,
    yes_no_keyboard,
)
from bot.mrz_parser import extract_text_from_image_bytes, find_mrz_from_text, parse_td3_mrz
from bot.ocr_fallback import easyocr_extract_text

logger = logging.getLogger(__name__)
router = Router(name="registration")


def _new_session() -> dict[str, Any]:
    return {
        "flow": "registration",
        "manager_id": None,
        "district": None,
        "address": None,
        "num_people_expected": 0,
        "passports": [],
        "current_passport_index": 1,
        "phone": None,
        "move_in_date": None,
        "payment": {},
    }


def _session_summary(session: dict[str, Any]) -> str:
    payment = session.get("payment", {})
    passports = session.get("passports", [])
    lines = [
        "Проверьте данные перед отправкой:",
        f"• Менеджер: {session.get('manager_id')}",
        f"• Район: {session.get('district')}",
        f"• Адрес: {session.get('address')}",
        f"• Жильцов: {session.get('num_people_expected')}",
        f"• Паспортов подтверждено: {len(passports)}",
        f"• Телефон: {session.get('phone')}",
        f"• Дата заезда: {session.get('move_in_date')}",
        f"• Аренда: {payment.get('rent')}",
        f"• Депозит: {payment.get('deposit')}",
        f"• Комиссия: {payment.get('commission')}",
    ]
    return "\n".join(lines)


def _is_valid_phone(phone: str) -> bool:
    return bool(re.fullmatch(r"\+?[0-9()\-\s]{10,20}", phone.strip()))


@router.message(CommandStart())
async def start_registration(message: Message, state: FSMContext) -> None:
    session = _new_session()
    await state.set_data({"session": session})
    await state.set_state(Form.choosing_manager)
    logger.info("FSM step entered: choosing_manager")
    await message.answer(
        "Здравствуйте! Начнем регистрацию арендатора. Выберите менеджера:",
        reply_markup=manager_keyboard(),
    )


@router.message(Form.choosing_manager)
async def process_manager(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text not in MANAGERS:
        await message.answer("Выберите менеджера с клавиатуры ниже.", reply_markup=manager_keyboard())
        return

    data = await state.get_data()
    session = data.get("session", _new_session())
    session["manager_id"] = text
    await state.update_data(session=session)

    await state.set_state(Form.ask_district)
    logger.info("FSM step entered: ask_district")
    await message.answer("Укажите район объекта:", reply_markup=district_keyboard())


@router.message(Form.ask_district)
async def process_district(message: Message, state: FSMContext) -> None:
    district = (message.text or "").strip()
    if not district:
        await message.answer("Район не должен быть пустым.")
        return

    if district not in DISTRICTS:
        await message.answer("Выберите район из списка или нажмите 'Другой район'.", reply_markup=district_keyboard())
        return

    data = await state.get_data()
    session = data.get("session", _new_session())
    session["district"] = district
    await state.update_data(session=session)

    await state.set_state(Form.ask_address)
    logger.info("FSM step entered: ask_address")
    await message.answer("Введите полный адрес:", reply_markup=ReplyKeyboardRemove())


@router.message(Form.ask_address)
async def process_address(message: Message, state: FSMContext) -> None:
    address = (message.text or "").strip()
    if not address:
        await message.answer("Адрес не должен быть пустым. Введите адрес еще раз.")
        return

    data = await state.get_data()
    session = data.get("session", _new_session())
    session["address"] = address
    await state.update_data(session=session)

    await state.set_state(Form.ask_num_people)
    logger.info("FSM step entered: ask_num_people")
    await message.answer("Сколько человек будет проживать?")


@router.message(Form.ask_num_people)
async def process_num_people(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if not value.isdigit() or int(value) <= 0:
        await message.answer("Введите целое число больше 0.")
        return

    num_people = int(value)
    data = await state.get_data()
    session = data.get("session", _new_session())
    session["num_people_expected"] = num_people
    session["current_passport_index"] = 1
    session["passports"] = []
    await state.update_data(session=session)

    await state.set_state(Form.ask_passport_photo)
    logger.info("FSM step entered: ask_passport_photo | passport index=%s", session["current_passport_index"])
    await message.answer(
        f"Пришлите фото паспорта №{session['current_passport_index']} (как фото, не файл)."
    )


@router.message(Form.ask_passport_photo, ~F.photo)
async def process_passport_not_photo(message: Message) -> None:
    await message.answer("На этом шаге нужно отправить фотографию паспорта.")


@router.message(Form.ask_passport_photo, F.photo)
async def process_passport_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    session = data.get("session", _new_session())
    passport_index = session.get("current_passport_index", 1)
    logger.info("FSM step entered: ask_passport_photo | passport index=%s", passport_index)

    photo = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    buf = io.BytesIO()
    await message.bot.download(file, destination=buf)
    img_bytes = buf.getvalue()

    text = extract_text_from_image_bytes(img_bytes)
    logger.info("OCR result length=%s | passport index=%s", len(text or ""), passport_index)

    line1, line2 = find_mrz_from_text(text)
    parsed = {}
    if line1 and line2:
        parsed = parse_td3_mrz(line1, line2)
    else:
        fallback_text = easyocr_extract_text(img_bytes)
        logger.info("Fallback OCR result length=%s | passport index=%s", len(fallback_text or ""), passport_index)
        line1, line2 = find_mrz_from_text(fallback_text)
        if line1 and line2:
            parsed = parse_td3_mrz(line1, line2)

    if not parsed:
        await message.answer(
            "Не удалось распознать паспортные данные. Отправьте более четкое фото этого же паспорта."
        )
        return

    passport_entry = {
        "index": passport_index,
        "photo_file_id": photo.file_id,
        "parsed": parsed,
        "confirmed": False,
    }

    passports = [p for p in session.get("passports", []) if p.get("index") != passport_index]
    passports.append(passport_entry)
    passports.sort(key=lambda x: x["index"])
    session["passports"] = passports
    await state.update_data(session=session)

    parsed_text = "\n".join(
        [
            f"Фамилия: {parsed.get('surname', '—')}",
            f"Имя: {parsed.get('given_names', '—')}",
            f"Номер паспорта: {parsed.get('passport_number', '—')}",
            f"Гражданство: {parsed.get('nationality', '—')}",
            f"Дата рождения: {parsed.get('birth_date', '—')}",
            f"Срок действия: {parsed.get('expiry_date', '—')}",
        ]
    )

    await state.set_state(Form.confirm_passport_fields)
    logger.info("FSM step entered: confirm_passport_fields | passport index=%s", passport_index)
    await message.answer(
        f"Паспорт №{passport_index} распознан:\n\n{parsed_text}\n\nВсе верно?",
        reply_markup=yes_no_keyboard(),
    )


@router.message(Form.confirm_passport_fields)
async def process_passport_confirmation(message: Message, state: FSMContext) -> None:
    answer = (message.text or "").strip()
    if answer not in {YES_TEXT, NO_TEXT}:
        await message.answer("Пожалуйста, выберите Да или Нет.", reply_markup=yes_no_keyboard())
        return

    data = await state.get_data()
    session = data.get("session", _new_session())
    passport_index = session.get("current_passport_index", 1)
    passports = session.get("passports", [])

    for passport in passports:
        if passport.get("index") == passport_index:
            passport["confirmed"] = answer == YES_TEXT
            break

    logger.info(
        "confirmation result=%s | passport index=%s",
        answer,
        passport_index,
    )

    session["passports"] = passports
    await state.update_data(session=session)

    if answer == NO_TEXT:
        await state.set_state(Form.ask_passport_photo)
        logger.info("FSM step entered: ask_passport_photo | passport index=%s", passport_index)
        await message.answer(
            f"Хорошо, отправьте новое фото для паспорта №{passport_index}.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    await state.set_state(Form.ask_add_another_passport)
    logger.info("FSM step entered: ask_add_another_passport | passport index=%s", passport_index)
    await message.answer("Добавить еще один паспорт?", reply_markup=add_another_keyboard())


@router.message(Form.ask_add_another_passport)
async def process_add_another_passport(message: Message, state: FSMContext) -> None:
    answer = (message.text or "").strip()
    if answer not in {ADD_ANOTHER_YES_TEXT, ADD_ANOTHER_NO_TEXT}:
        await message.answer("Выберите вариант на клавиатуре.", reply_markup=add_another_keyboard())
        return

    data = await state.get_data()
    session = data.get("session", _new_session())

    confirmed_count = sum(1 for p in session.get("passports", []) if p.get("confirmed"))
    expected = session.get("num_people_expected", 0)

    if answer == ADD_ANOTHER_YES_TEXT:
        session["current_passport_index"] = session.get("current_passport_index", 1) + 1
        await state.update_data(session=session)
        await state.set_state(Form.ask_passport_photo)
        logger.info(
            "FSM step entered: ask_passport_photo | passport index=%s",
            session["current_passport_index"],
        )
        await message.answer(
            f"Пришлите фото паспорта №{session['current_passport_index']}.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    if confirmed_count < expected:
        await message.answer(
            f"Подтверждено паспортов: {confirmed_count} из {expected}. Добавьте оставшиеся.",
            reply_markup=add_another_keyboard(),
        )
        return

    await state.set_state(Form.ask_contacts)
    logger.info("FSM step entered: ask_contacts")
    await message.answer("Введите контактный телефон:", reply_markup=ReplyKeyboardRemove())


@router.message(Form.ask_contacts)
async def process_contacts(message: Message, state: FSMContext) -> None:
    phone = (message.text or "").strip()
    if not _is_valid_phone(phone):
        await message.answer("Введите корректный телефон, например +79991234567")
        return

    data = await state.get_data()
    session = data.get("session", _new_session())
    session["phone"] = phone
    await state.update_data(session=session)

    await state.set_state(Form.ask_move_in_date)
    logger.info("FSM step entered: ask_move_in_date")
    await message.answer("Введите дату заезда в формате YYYY-MM-DD")


@router.message(Form.ask_move_in_date)
async def process_move_in_date(message: Message, state: FSMContext) -> None:
    date_text = (message.text or "").strip()
    try:
        datetime.strptime(date_text, "%Y-%m-%d")
    except ValueError:
        await message.answer("Неверный формат даты. Используйте YYYY-MM-DD")
        return

    data = await state.get_data()
    session = data.get("session", _new_session())
    session["move_in_date"] = date_text
    await state.update_data(session=session)

    await state.set_state(Form.ask_payment_details)
    logger.info("FSM step entered: ask_payment_details")
    await message.answer("Введите платежи в формате: аренда, депозит, комиссия")


@router.message(Form.ask_payment_details)
async def process_payment_details(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    chunks = [c.strip().replace(" ", "") for c in raw.split(",")]
    if len(chunks) != 3 or not all(re.fullmatch(r"\d+(\.\d+)?", c) for c in chunks):
        await message.answer("Нужен формат: аренда, депозит, комиссия. Например: 50000, 30000, 25000")
        return

    data = await state.get_data()
    session = data.get("session", _new_session())
    session["payment"] = {
        "rent": float(chunks[0]),
        "deposit": float(chunks[1]),
        "commission": float(chunks[2]),
    }
    await state.update_data(session=session)

    await state.set_state(Form.final_confirmation)
    logger.info("FSM step entered: final_confirmation")
    await message.answer(_session_summary(session), reply_markup=confirm_keyboard())


@router.message(Form.final_confirmation)
async def process_final_confirmation(message: Message, state: FSMContext) -> None:
    answer = (message.text or "").strip()
    if answer not in {CONFIRM_TEXT, CANCEL_TEXT}:
        await message.answer("Выберите Подтвердить или Отменить.", reply_markup=confirm_keyboard())
        return

    data = await state.get_data()
    session = data.get("session", _new_session())

    if answer == CANCEL_TEXT:
        await state.clear()
        await message.answer("Регистрация отменена. Можно начать заново командой /start", reply_markup=ReplyKeyboardRemove())
        return

    logger.info("confirmation result=%s | flow=%s", answer, session.get("flow"))
    await state.set_state(Form.done)
    logger.info("FSM step entered: done")
    await message.answer("Спасибо! Регистрация завершена ✅", reply_markup=ReplyKeyboardRemove())
    await state.clear()
