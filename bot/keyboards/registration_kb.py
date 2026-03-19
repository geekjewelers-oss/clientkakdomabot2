from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

MANAGERS = [
    "Менеджер Анна",
    "Менеджер Борис",
    "Менеджер Светлана",
]

DISTRICTS = [
    "Центральный",
    "Северный",
    "Южный",
    "Западный",
    "Восточный",
    "Другой район",
]

YES_TEXT = "Да"
NO_TEXT = "Нет"

CONFIRM_TEXT = "Подтвердить"
CANCEL_TEXT = "Отменить"
BACK_TEXT = "⬅ Назад"
RETRY_PASSPORT_TEXT = "🔁 Пересканировать паспорт"
BAD_PHOTO_TEXT = "📷 Плохое фото"
EDIT_ADDRESS_TEXT = "✏ Исправить адрес"
GLOBAL_CANCEL_TEXT = "❌ Отменить регистрацию"

ADD_ANOTHER_YES_TEXT = "Добавить ещё"
ADD_ANOTHER_NO_TEXT = "Продолжить"


def manager_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=manager, callback_data=f"manager:{manager}")]
            for manager in MANAGERS
        ]
    )


def district_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=d, callback_data=f"district:{d}")]
            for d in DISTRICTS
        ]
        + [
            [InlineKeyboardButton(text=BACK_TEXT, callback_data="action:back")],
            [InlineKeyboardButton(text=GLOBAL_CANCEL_TEXT, callback_data="action:cancel_registration")],
        ]
    )


def yes_no_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=YES_TEXT, callback_data="confirm:yes"),
                InlineKeyboardButton(text=NO_TEXT, callback_data="confirm:no"),
            ],
            [InlineKeyboardButton(text=GLOBAL_CANCEL_TEXT, callback_data="action:cancel_registration")],
        ]
    )


def confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=CONFIRM_TEXT, callback_data="action:confirm"),
                InlineKeyboardButton(text=CANCEL_TEXT, callback_data="action:cancel"),
            ],
            [InlineKeyboardButton(text=EDIT_ADDRESS_TEXT, callback_data="action:edit_address")],
            [InlineKeyboardButton(text=GLOBAL_CANCEL_TEXT, callback_data="action:cancel_registration")],
        ]
    )


def add_another_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=ADD_ANOTHER_YES_TEXT, callback_data="residents:add_more")],
            [InlineKeyboardButton(text=ADD_ANOTHER_NO_TEXT, callback_data="residents:continue")],
            [InlineKeyboardButton(text=GLOBAL_CANCEL_TEXT, callback_data="action:cancel_registration")],
        ]
    )


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BACK_TEXT, callback_data="action:back")],
            [InlineKeyboardButton(text=GLOBAL_CANCEL_TEXT, callback_data="action:cancel_registration")],
        ]
    )


def retry_passport_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=YES_TEXT, callback_data="passport:ok"),
                InlineKeyboardButton(text=NO_TEXT, callback_data="confirm:no"),
            ],
            [InlineKeyboardButton(text=RETRY_PASSPORT_TEXT, callback_data="passport:rescan")],
            [InlineKeyboardButton(text=GLOBAL_CANCEL_TEXT, callback_data="action:cancel_registration")],
        ]
    )


def bad_photo_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BAD_PHOTO_TEXT, callback_data="passport:bad_photo")],
            [InlineKeyboardButton(text=GLOBAL_CANCEL_TEXT, callback_data="action:cancel_registration")],
        ]
    )


def edit_address_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=EDIT_ADDRESS_TEXT, callback_data="action:edit_address")],
            [InlineKeyboardButton(text=GLOBAL_CANCEL_TEXT, callback_data="action:cancel_registration")],
        ]
    )


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=GLOBAL_CANCEL_TEXT, callback_data="action:cancel_registration")]
        ]
    )
