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

from bot.mrz_parser import validate_td3_composite


LINE2_VALID = "L898902C36UTO7408122F1204159ZE184226B<<<<<10"
LINE2_INVALID_COMPOSITE = "L898902C36UTO7408122F1204159ZE184226B<<<<<11"


def test_validate_td3_composite_valid():
    assert validate_td3_composite(LINE2_VALID) is True


def test_validate_td3_composite_invalid():
    assert validate_td3_composite(LINE2_INVALID_COMPOSITE) is False
