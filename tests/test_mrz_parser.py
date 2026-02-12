from pathlib import Path
import sys
import types

sys.path.append(str(Path(__file__).resolve().parents[1]))

# Minimal stubs for optional OCR/image dependencies not needed by these tests.
sys.modules.setdefault("cv2", types.ModuleType("cv2"))
sys.modules.setdefault("numpy", types.ModuleType("numpy"))
sys.modules.setdefault("pytesseract", types.ModuleType("pytesseract"))

pil_module = types.ModuleType("PIL")
pil_image_module = types.ModuleType("PIL.Image")
setattr(pil_image_module, "Image", object)
setattr(pil_module, "Image", pil_image_module)
sys.modules.setdefault("PIL", pil_module)
sys.modules.setdefault("PIL.Image", pil_image_module)

from bot.mrz_parser import find_mrz_from_text, parse_td3_mrz


LINE1 = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<"
LINE2 = "L898902C36UTO7408122F1204159ZE184226B<<<<<10"


def test_find_mrz_from_text_extracts_td3_lines():
    text = f"Random OCR noise\n{LINE1}\n{LINE2}\nFooter"

    line1, line2 = find_mrz_from_text(text)

    assert line1 == LINE1
    assert line2 == LINE2


def test_parse_td3_mrz_parses_expected_fields():
    parsed = parse_td3_mrz(LINE1, LINE2)

    assert parsed["surname"] == "ERIKSSON"
    assert "ANNA" in parsed["given_names"]
    assert parsed["passport_number"] == "L898902C3"
    assert parsed["nationality"] == "UTO"
