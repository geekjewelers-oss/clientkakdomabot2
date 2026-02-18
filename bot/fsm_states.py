from aiogram.fsm.state import State, StatesGroup


class RegistrationFSM(StatesGroup):
    START = State()
    COLLECT_CONTACT = State()
    UPLOAD_DOC = State()
    QUALITY_PRECHECK = State()
    OCR_SUBMIT = State()
    WAIT_RESULT = State()
    PREVIEW_CONFIRM = State()
    MANUAL_EDIT = State()
    CONFIRMED = State()
    MANAGER_VERIFICATION = State()
    DONE = State()
