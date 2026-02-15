from pathlib import Path
import sys
import types

sys.path.append(str(Path(__file__).resolve().parents[1]))

# Optional heavy deps stubs
sys.modules.setdefault("cv2", types.ModuleType("cv2"))
numpy_module = types.ModuleType("numpy")
setattr(numpy_module, "ndarray", object)
sys.modules.setdefault("numpy", numpy_module)
sys.modules.setdefault("pytesseract", types.ModuleType("pytesseract"))
sys.modules.setdefault("easyocr", types.ModuleType("easyocr"))

pil_module = types.ModuleType("PIL")
pil_image_module = types.ModuleType("PIL.Image")
setattr(pil_image_module, "Image", object)
setattr(pil_module, "Image", pil_image_module)
sys.modules.setdefault("PIL", pil_module)
sys.modules.setdefault("PIL.Image", pil_image_module)

from bot import ocr_orchestrator
from bot.mrz_parser import compute_mrz_hash


def test_compute_mrz_hash_present_for_mrz():
    line1 = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<"
    line2 = "L898902C36UTO7408122F1204159ZE184226B<<<<<10"
    mrz_hash = compute_mrz_hash(line1, line2)

    assert mrz_hash is not None
    assert len(mrz_hash) == 64


def test_ocr_sla_breach_and_hash_and_correlation(monkeypatch):
    calls = []

    def fake_inc(name: str, value: int = 1):
        calls.append((name, value))

    monkeypatch.setattr(ocr_orchestrator.metrics, "inc", fake_inc)
    monkeypatch.setattr(ocr_orchestrator.config, "OCR_SLA_TOTAL_TIMEOUT_SECONDS", 8)
    monkeypatch.setattr(ocr_orchestrator.config, "OCR_SLA_BREACH_THRESHOLD_RATIO", 0.9)
    monkeypatch.setattr(ocr_orchestrator.config, "OCR_SLA_MAX_LOCAL_ATTEMPTS", 1)
    monkeypatch.setattr(ocr_orchestrator.config, "OCR_SLA_FALLBACK_AFTER_FAILURES", 1)
    monkeypatch.setattr(ocr_orchestrator.config, "OCR_SLA_FALLBACK_ATTEMPTS", 0)
    monkeypatch.setattr(ocr_orchestrator.config, "OCR_SLA_AUTO_ACCEPT_CONFIDENCE", 0.8)
    monkeypatch.setattr(ocr_orchestrator.config, "OCR_SLA_FALLBACK_THRESHOLD_CONFIDENCE", 0.55)

    monkeypatch.setattr(ocr_orchestrator, "_decode_gray_image", lambda *_: None)

    line1 = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<"
    line2 = "L898902C36UTO7408122F1204159ZE184226B<<<<<10"

    def fake_local(*_args, **_kwargs):
        return {
            "text": "ok",
            "source": "mrz",
            "confidence": "high",
            "parsed": {"surname": "ERIKSSON", "_mrz_checksum_ok": True},
            "mrz_lines": (line1, line2),
            "quality": {
                "confidence": 0.95,
                "needs_retry": False,
                "checksum_ok": True,
                "blur_bad": False,
                "exposure_score": 1.0,
            },
        }

    monkeypatch.setattr(ocr_orchestrator, "_local_ocr_attempt", fake_local)

    times = iter([0.0, 7.3, 7.3])
    monkeypatch.setattr(ocr_orchestrator.time, "monotonic", lambda: next(times))

    correlation_id = "123e4567-e89b-12d3-a456-426614174000"
    result = ocr_orchestrator.ocr_pipeline_extract(b"img", correlation_id=correlation_id)

    assert result["sla_breach"] is True
    assert result["passport_hash"] == compute_mrz_hash(line1, line2)
    assert result["correlation_id"] == correlation_id
    assert "ocr.sla.breach" in result["metrics_inc"]
    assert any(name == "ocr.sla.breach" for name, _ in calls)
