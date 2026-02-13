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

from bot.mrz_parser import parse_td3_mrz


LINE1 = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<"
LINE2_VALID = "L898902C36UTO7408122F1204159ZE184226B<<<<<10"
LINE2_INVALID = "L898902C35UTO7408122F1204159ZE184226B<<<<<10"


def test_valid_mrz_checksum():
    parsed = parse_td3_mrz(LINE1, LINE2_VALID)

    assert parsed["_mrz_checksum_ok"] is True


def test_invalid_mrz_checksum():
    parsed = parse_td3_mrz(LINE1, LINE2_INVALID)

    assert parsed["_mrz_checksum_ok"] is False
