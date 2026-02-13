import io
import logging
import re
from datetime import datetime
from typing import Any

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardMarkup, ReplyKeyboardRemove

from bot.fsm.states import Form
from bot.keyboards.registration_kb import (
    ADD_ANOTHER_NO_TEXT,
    ADD_ANOTHER_YES_TEXT,
    BACK_TEXT,
    BAD_PHOTO_TEXT,
    CANCEL_TEXT,
    CONFIRM_TEXT,
    DISTRICTS,
    EDIT_ADDRESS_TEXT,
    GLOBAL_CANCEL_TEXT,
    MANAGERS,
    NO_TEXT,
    RETRY_PASSPORT_TEXT,
    YES_TEXT,
    add_another_keyboard,
    back_kb,
    bad_photo_kb,
    confirm_keyboard,
    district_keyboard,
    manager_keyboard,
    retry_passport_kb,
)
from bot.ocr_orchestrator import ocr_pipeline_extract

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


async def _get_session(state: FSMContext) -> dict[str, Any]:
    data = await state.get_data()
    return data.get("session", _new_session())


async def _go_to_step(
    message: Message,
    state: FSMContext,
    *,
    next_state: Any,
    text: str,
    keyboard: ReplyKeyboardMarkup | ReplyKeyboardRemove | None = None,
    log_step: str,
) -> None:
    await state.set_state(next_state)
    logger.info("FSM step entered: %s", log_step)
    kwargs = {"reply_markup": keyboard} if keyboard is not None else {}
    await message.answer(text, **kwargs)


@router.message(CommandStart())
async def start_registration(message: Message, state: FSMContext) -> None:
    session = _new_session()
    await state.set_data({"session": session})
    await _go_to_step(
        message,
        state,
        next_state=Form.choosing_manager,
        text="Здравствуйте! Начнем регистрацию арендатора. Выберите менеджера:",
        keyboard=manager_keyboard(),
        log_step="choosing_manager",
    )


@router.message(F.text == GLOBAL_CANCEL_TEXT)
async def process_global_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    logger.info("REGISTRATION_CANCELLED")
    await message.answer("Регистрация отменена", reply_markup=ReplyKeyboardRemove())
    await start_registration(message, state)


@router.message(Form.ask_district, F.text == BACK_TEXT)
async def back_from_ask_district(message: Message, state: FSMContext) -> None:
    session = await _get_session(state)
    session["district"] = None
    await state.update_data(session=session)
    logger.info("FSM_BACK_STEP from=ask_district to=choosing_manager")
    await _go_to_step(
        message,
        state,
        next_state=Form.choosing_manager,
        text="Выберите менеджера:",
        keyboard=manager_keyboard(),
        log_step="choosing_manager",
    )


@router.message(Form.ask_address, F.text == BACK_TEXT)
async def back_from_ask_address(message: Message, state: FSMContext) -> None:
    session = await _get_session(state)
    session["address"] = None
    await state.update_data(session=session)
    logger.info("FSM_BACK_STEP from=ask_address to=ask_district")
    await _go_to_step(
        message,
        state,
        next_state=Form.ask_district,
        text="Укажите район объекта:",
        keyboard=district_keyboard(),
        log_step="ask_district",
    )


@router.message(Form.ask_num_people, F.text == BACK_TEXT)
async def back_from_ask_num_people(message: Message, state: FSMContext) -> None:
    session = await _get_session(state)
    session["num_people_expected"] = 0
    session["current_passport_index"] = 1
    session["passports"] = []
    await state.update_data(session=session)
    logger.info("FSM_BACK_STEP from=ask_num_people to=ask_address")
    await _go_to_step(
        message,
        state,
        next_state=Form.ask_address,
        text="Введите полный адрес:",
        keyboard=back_kb(),
        log_step="ask_address",
    )


@router.message(Form.ask_contacts, F.text == BACK_TEXT)
async def back_from_ask_contacts(message: Message, state: FSMContext) -> None:
    session = await _get_session(state)
    session["phone"] = None
    await state.update_data(session=session)
    logger.info("FSM_BACK_STEP from=ask_contacts to=ask_add_another_passport")
    await _go_to_step(
        message,
        state,
        next_state=Form.ask_add_another_passport,
        text="Добавить еще один паспорт?",
        keyboard=add_another_keyboard(),
        log_step="ask_add_another_passport",
    )


@router.message(Form.ask_move_in_date, F.text == BACK_TEXT)
async def back_from_ask_move_in_date(message: Message, state: FSMContext) -> None:
    session = await _get_session(state)
    session["move_in_date"] = None
    await state.update_data(session=session)
    logger.info("FSM_BACK_STEP from=ask_move_in_date to=ask_contacts")
    await _go_to_step(
        message,
        state,
        next_state=Form.ask_contacts,
        text="Введите контактный телефон:",
        keyboard=back_kb(),
        log_step="ask_contacts",
    )


@router.message(Form.ask_payment_details, F.text == BACK_TEXT)
async def back_from_ask_payment_details(message: Message, state: FSMContext) -> None:
    session = await _get_session(state)
    session["payment"] = {}
    await state.update_data(session=session)
    logger.info("FSM_BACK_STEP from=ask_payment_details to=ask_move_in_date")
    await _go_to_step(
        message,
        state,
        next_state=Form.ask_move_in_date,
        text="Введите дату заезда в формате YYYY-MM-DD",
        keyboard=back_kb(),
        log_step="ask_move_in_date",
    )


@router.message(Form.confirm_passport_fields, F.text == RETRY_PASSPORT_TEXT)
async def process_retry_passport(message: Message, state: FSMContext) -> None:
    session = await _get_session(state)
    passport_index = session.get("current_passport_index", 1)
    session["passports"] = [p for p in session.get("passports", []) if p.get("index") != passport_index]
    await state.update_data(session=session)
    logger.info("PASSPORT_RETRY | passport index=%s", passport_index)
    await _go_to_step(
        message,
        state,
        next_state=Form.ask_passport_photo,
        text=f"Отправьте новое фото для паспорта №{passport_index}.",
        keyboard=bad_photo_kb(),
        log_step=f"ask_passport_photo | passport index={passport_index}",
    )


@router.message(Form.ask_passport_photo, F.text == BAD_PHOTO_TEXT)
async def process_bad_photo_hint(message: Message) -> None:
    logger.info("BAD_PHOTO_HINT_SHOWN")
    await message.answer(
        "Подсказка по фото паспорта:\n"
        "• без бликов\n"
        "• весь разворот\n"
        "• читаемая MRZ зона\n"
        "• без обрезки краёв"
    )


@router.message(Form.final_confirmation, F.text == EDIT_ADDRESS_TEXT)
async def process_edit_address(message: Message, state: FSMContext) -> None:
    logger.info("FSM_BACK_STEP from=final_confirmation to=ask_address")
    await _go_to_step(
        message,
        state,
        next_state=Form.ask_address,
        text="Введите полный адрес:",
        keyboard=back_kb(),
        log_step="ask_address",
    )


@router.message(Form.choosing_manager)
async def process_manager(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text not in MANAGERS:
        await message.answer("Выберите менеджера с клавиатуры ниже.", reply_markup=manager_keyboard())
        return

    session = await _get_session(state)
    session["manager_id"] = text
    await state.update_data(session=session)

    await _go_to_step(
        message,
        state,
        next_state=Form.ask_district,
        text="Укажите район объекта:",
        keyboard=district_keyboard(),
        log_step="ask_district",
    )


@router.message(Form.ask_district)
async def process_district(message: Message, state: FSMContext) -> None:
    district = (message.text or "").strip()
    if not district:
        await message.answer("Район не должен быть пустым.")
        return

    if district not in DISTRICTS:
        await message.answer("Выберите район из списка или нажмите 'Другой район'.", reply_markup=district_keyboard())
        return

    session = await _get_session(state)
    session["district"] = district
    await state.update_data(session=session)

    await _go_to_step(
        message,
        state,
        next_state=Form.ask_address,
        text="Введите полный адрес:",
        keyboard=back_kb(),
        log_step="ask_address",
    )


@router.message(Form.ask_address)
async def process_address(message: Message, state: FSMContext) -> None:
    address = (message.text or "").strip()
    if not address:
        await message.answer("Адрес не должен быть пустым. Введите адрес еще раз.")
        return

    session = await _get_session(state)
    session["address"] = address
    await state.update_data(session=session)

    await _go_to_step(
        message,
        state,
        next_state=Form.ask_num_people,
        text="Сколько человек будет проживать?",
        keyboard=back_kb(),
        log_step="ask_num_people",
    )


@router.message(Form.ask_num_people)
async def process_num_people(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if not value.isdigit() or int(value) <= 0:
        await message.answer("Введите целое число больше 0.")
        return

    num_people = int(value)
    session = await _get_session(state)
    session["num_people_expected"] = num_people
    session["current_passport_index"] = 1
    session["passports"] = []
    await state.update_data(session=session)

    await _go_to_step(
        message,
        state,
        next_state=Form.ask_passport_photo,
        text=f"Пришлите фото паспорта №{session['current_passport_index']} (как фото, не файл).",
        keyboard=bad_photo_kb(),
        log_step=f"ask_passport_photo | passport index={session['current_passport_index']}",
    )


@router.message(Form.ask_passport_photo, ~F.photo)
async def process_passport_not_photo(message: Message) -> None:
    await message.answer("На этом шаге нужно отправить фотографию паспорта.")


@router.message(Form.ask_passport_photo, F.photo)
async def process_passport_photo(message: Message, state: FSMContext) -> None:
    session = await _get_session(state)
    passport_index = session.get("current_passport_index", 1)
    logger.info("FSM step entered: ask_passport_photo | passport index=%s", passport_index)

    photo = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    buf = io.BytesIO()
    await message.bot.download(file, destination=buf)
    img_bytes = buf.getvalue()

    ocr_result = ocr_pipeline_extract(img_bytes)
    text = ocr_result.get("text") or ""
    parsed_fields = ocr_result.get("parsed") or {}
    parsed = dict(parsed_fields)
    mrz_lines = ocr_result.get("mrz_lines")
    source = ocr_result.get("source") or "unknown"
    confidence = ocr_result.get("confidence") or "low"

    logger.info("[OCR] handler stage: source=%s, confidence=%s, text_len=%d", source, confidence, len(text))

    if confidence == "low":
        parsed["needs_better_photo"] = True
        await message.answer(
            "Качество распознавания низкое. Отправьте, пожалуйста, более чёткое фото без бликов и обрезки."
        )

    if not parsed_fields:
        await message.answer(
            "Не удалось распознать паспортные данные. Отправьте более четкое фото этого же паспорта."
        )
        return

    passport_entry = {
        "index": passport_index,
        "photo_file_id": photo.file_id,
        "parsed": parsed,
        "mrz_lines": mrz_lines,
        "ocr_source": source,
        "ocr_confidence": confidence,
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

    await _go_to_step(
        message,
        state,
        next_state=Form.confirm_passport_fields,
        text=f"Паспорт №{passport_index} распознан:\n\n{parsed_text}\n\nВсе верно?",
        keyboard=retry_passport_kb(),
        log_step=f"confirm_passport_fields | passport index={passport_index}",
    )


@router.message(Form.confirm_passport_fields)
async def process_passport_confirmation(message: Message, state: FSMContext) -> None:
    answer = (message.text or "").strip()
    if answer not in {YES_TEXT, NO_TEXT}:
        await message.answer("Пожалуйста, выберите Да или Нет.", reply_markup=retry_passport_kb())
        return

    session = await _get_session(state)
    passport_index = session.get("current_passport_index", 1)
    passports = session.get("passports", [])

    for passport in passports:
        if passport.get("index") == passport_index:
            passport["confirmed"] = answer == YES_TEXT
            break

    logger.info("confirmation result=%s | passport index=%s", answer, passport_index)

    session["passports"] = passports
    await state.update_data(session=session)

    if answer == NO_TEXT:
        await _go_to_step(
            message,
            state,
            next_state=Form.ask_passport_photo,
            text=f"Хорошо, отправьте новое фото для паспорта №{passport_index}.",
            keyboard=bad_photo_kb(),
            log_step=f"ask_passport_photo | passport index={passport_index}",
        )
        return

    await _go_to_step(
        message,
        state,
        next_state=Form.ask_add_another_passport,
        text="Добавить еще один паспорт?",
        keyboard=add_another_keyboard(),
        log_step=f"ask_add_another_passport | passport index={passport_index}",
    )


@router.message(Form.ask_add_another_passport)
async def process_add_another_passport(message: Message, state: FSMContext) -> None:
    answer = (message.text or "").strip()
    if answer not in {ADD_ANOTHER_YES_TEXT, ADD_ANOTHER_NO_TEXT}:
        await message.answer("Выберите вариант на клавиатуре.", reply_markup=add_another_keyboard())
        return

    session = await _get_session(state)

    confirmed_count = sum(1 for p in session.get("passports", []) if p.get("confirmed"))
    expected = session.get("num_people_expected", 0)

    if answer == ADD_ANOTHER_YES_TEXT:
        session["current_passport_index"] = session.get("current_passport_index", 1) + 1
        await state.update_data(session=session)
        await _go_to_step(
            message,
            state,
            next_state=Form.ask_passport_photo,
            text=f"Пришлите фото паспорта №{session['current_passport_index']}.",
            keyboard=bad_photo_kb(),
            log_step=f"ask_passport_photo | passport index={session['current_passport_index']}",
        )
        return

    if confirmed_count < expected:
        await message.answer(
            f"Подтверждено паспортов: {confirmed_count} из {expected}. Добавьте оставшиеся.",
            reply_markup=add_another_keyboard(),
        )
        return

    await _go_to_step(
        message,
        state,
        next_state=Form.ask_contacts,
        text="Введите контактный телефон:",
        keyboard=back_kb(),
        log_step="ask_contacts",
    )


@router.message(Form.ask_contacts)
async def process_contacts(message: Message, state: FSMContext) -> None:
    phone = (message.text or "").strip()
    if not _is_valid_phone(phone):
        await message.answer("Введите корректный телефон, например +79991234567")
        return

    session = await _get_session(state)
    session["phone"] = phone
    await state.update_data(session=session)

    await _go_to_step(
        message,
        state,
        next_state=Form.ask_move_in_date,
        text="Введите дату заезда в формате YYYY-MM-DD",
        keyboard=back_kb(),
        log_step="ask_move_in_date",
    )


@router.message(Form.ask_move_in_date)
async def process_move_in_date(message: Message, state: FSMContext) -> None:
    date_text = (message.text or "").strip()
    try:
        datetime.strptime(date_text, "%Y-%m-%d")
    except ValueError:
        await message.answer("Неверный формат даты. Используйте YYYY-MM-DD")
        return

    session = await _get_session(state)
    session["move_in_date"] = date_text
    await state.update_data(session=session)

    await _go_to_step(
        message,
        state,
        next_state=Form.ask_payment_details,
        text="Введите платежи в формате: аренда, депозит, комиссия",
        keyboard=back_kb(),
        log_step="ask_payment_details",
    )


@router.message(Form.ask_payment_details)
async def process_payment_details(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    chunks = [c.strip().replace(" ", "") for c in raw.split(",")]
    if len(chunks) != 3 or not all(re.fullmatch(r"\d+(\.\d+)?", c) for c in chunks):
        await message.answer("Нужен формат: аренда, депозит, комиссия. Например: 50000, 30000, 25000")
        return

    session = await _get_session(state)
    session["payment"] = {
        "rent": float(chunks[0]),
        "deposit": float(chunks[1]),
        "commission": float(chunks[2]),
    }
    await state.update_data(session=session)

    await _go_to_step(
        message,
        state,
        next_state=Form.final_confirmation,
        text=_session_summary(session),
        keyboard=confirm_keyboard(),
        log_step="final_confirmation",
    )


@router.message(Form.final_confirmation)
async def process_final_confirmation(message: Message, state: FSMContext) -> None:
    answer = (message.text or "").strip()
    if answer not in {CONFIRM_TEXT, CANCEL_TEXT}:
        await message.answer("Выберите Подтвердить или Отменить.", reply_markup=confirm_keyboard())
        return

    session = await _get_session(state)

    if answer == CANCEL_TEXT:
        await state.clear()
        logger.info("REGISTRATION_CANCELLED")
        await message.answer("Регистрация отменена", reply_markup=ReplyKeyboardRemove())
        await start_registration(message, state)
        return

    logger.info("confirmation result=%s | flow=%s", answer, session.get("flow"))
    await _go_to_step(
        message,
        state,
        next_state=Form.done,
        text="Спасибо! Регистрация завершена ✅",
        keyboard=ReplyKeyboardRemove(),
        log_step="done",
    )
    await state.clear()
