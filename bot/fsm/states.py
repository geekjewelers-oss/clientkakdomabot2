from aiogram.fsm.state import State, StatesGroup


class Form(StatesGroup):
    choosing_manager = State()
    ask_district = State()
    ask_address = State()
    ask_num_people = State()
    ask_passport_photo = State()
    rescan_passport = State()
    manual_input_mode = State()
    confirm_passport_fields = State()
    ask_add_another_passport = State()
    ask_contacts = State()
    ask_move_in_date = State()
    ask_payment_details = State()
    final_confirmation = State()
    done = State()
