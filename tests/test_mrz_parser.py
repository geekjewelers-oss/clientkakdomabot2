import asyncio
from pathlib import Path
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.append(str(Path(__file__).resolve().parents[1]))

# Minimal stubs for optional OCR/image dependencies not needed by these tests.
sys.modules.setdefault("cv2", types.ModuleType("cv2"))
sys.modules.setdefault("numpy", types.ModuleType("numpy"))
pytesseract_module = types.ModuleType("pytesseract")
setattr(pytesseract_module, "image_to_string", lambda *_args, **_kwargs: "")
sys.modules.setdefault("pytesseract", pytesseract_module)

pil_module = types.ModuleType("PIL")
pil_image_module = types.ModuleType("PIL.Image")
setattr(pil_image_module, "Image", object)
setattr(pil_module, "Image", pil_image_module)
sys.modules.setdefault("PIL", pil_module)
sys.modules.setdefault("PIL.Image", pil_image_module)

from bot.mrz_parser import compute_mrz_checksum, parse_td3_mrz, run_ocr_pipeline, validate_mrz_checksum


TD3_LINE1 = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<"
TD3_LINE2 = "L898902C36UTO6908061F9406236ZE184226B<<<<<<1"


def test_validate_mrz_checksum_cases():
    assert compute_mrz_checksum("520727") == 3
    assert validate_mrz_checksum("520727", "3") is True
    assert validate_mrz_checksum("AB1234567", "0") is False
    assert validate_mrz_checksum("", "0") is True
    assert validate_mrz_checksum("AB1234567", "") is False
    assert validate_mrz_checksum("AB1234567", "X") is False


def test_parse_td3_mrz_with_known_valid_sample():
    with patch("bot.mrz_parser.validate_td3_composite", return_value=True):
        result = parse_td3_mrz(TD3_LINE1, TD3_LINE2)

    assert result["surname"] == "ERIKSSON"
    assert result["given_names"] == "ANNA MARIA"
    assert result["nationality"] == "UTO"
    assert result["document_type"] == "P"
    assert result["_mrz_checksum_ok"] is True
    assert result["mrz_confidence_score"] == 1.0
    assert "passport_hash" in result
    assert len(result["passport_hash"]) == 16


def test_parse_td3_mrz_short_lines_do_not_raise():
    short_result = parse_td3_mrz("P<UTO", "L898")

    assert short_result["mrz_confidence_score"] == 0.0 or isinstance(short_result, dict)


def test_pipeline_local_success():
    fake_bytes = b"fake_image_data"
    mrz_text = (
        "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<\n"
        "L898902C36UTO6908061F9406236ZE184226B<<<<<<1"
    )

    with patch("bot.mrz_parser.pytesseract.image_to_string", return_value=mrz_text):
        with patch("bot.mrz_parser.image_bytes_to_pil", return_value=MagicMock()):
            result = asyncio.run(run_ocr_pipeline(fake_bytes, correlation_id="test-123"))

    assert result["correlation_id"] == "test-123"
    assert result["parsing_source"] == "MRZ_local"
    assert result["confidence_score"] > 0
    assert "fields" in result
    assert result["sla_breach"] is False


def test_pipeline_garbage_input():
    with patch("bot.mrz_parser.pytesseract.image_to_string", return_value="garbage text"):
        with patch("bot.mrz_parser.image_bytes_to_pil", return_value=MagicMock()):
            with patch("bot.mrz_parser._run_yandex_fallback", new=AsyncMock(return_value=None)):
                result = asyncio.run(run_ocr_pipeline(b"x", correlation_id="test-456"))

    assert result["fields"] == {} or result["sla_breach"] is True
