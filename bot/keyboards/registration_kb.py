from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

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

ADD_ANOTHER_YES_TEXT = "Добавить ещё"
ADD_ANOTHER_NO_TEXT = "Продолжить"


def manager_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=manager)] for manager in MANAGERS],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def district_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=d)] for d in DISTRICTS],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def yes_no_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=YES_TEXT), KeyboardButton(text=NO_TEXT)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def confirm_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=CONFIRM_TEXT), KeyboardButton(text=CANCEL_TEXT)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def add_another_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=ADD_ANOTHER_YES_TEXT)],
            [KeyboardButton(text=ADD_ANOTHER_NO_TEXT)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
