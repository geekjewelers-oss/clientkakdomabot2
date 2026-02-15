import cv2
import numpy as np


def blur_score(gray: np.ndarray) -> float:
    return cv2.Laplacian(gray, cv2.CV_64F).var()


def exposure_score(gray: np.ndarray) -> float:
    mean = float(np.mean(gray))
    if mean < 60:
        return 0.2
    if mean > 200:
        return 0.3
    return 1.0


def is_blur_bad(score: float) -> bool:
    return score < 80


def is_image_low_quality(mrz_data: dict, blur: float, exposure: float) -> bool:
    conf = float(mrz_data.get("mrz_confidence_score", 0.0))
    return (
        conf < 0.55
        or not mrz_data.get("_mrz_checksum_ok", False)
        or is_blur_bad(blur)
        or exposure < 0.5
    )


def build_ocr_quality_report(mrz_data: dict, blur: float, exposure: float) -> dict:
    conf = float(mrz_data.get("mrz_confidence_score", 0.0))

    return {
        "confidence": conf,
        "checksum_ok": mrz_data.get("_mrz_checksum_ok", False),
        "blur_score": blur,
        "blur_bad": is_blur_bad(blur),
        "exposure_score": exposure,
        "needs_retry": is_image_low_quality(mrz_data, blur, exposure),
    }
