from __future__ import annotations

import cv2
import numpy as np


def _remove_specular_highlights(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, bright = cv2.threshold(gray, 240, 255, cv2.THRESH_BINARY)
    inpainted = cv2.inpaint(image, bright, 5, cv2.INPAINT_TELEA)
    return inpainted


def _deskew(image: np.ndarray, *, max_rotation: float | None = None) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(thresh > 0))
    if coords.size == 0:
        return image

    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = 90 + angle

    if max_rotation is not None:
        angle = max(-max_rotation, min(max_rotation, angle))

    h, w = image.shape[:2]
    center = (w // 2, h // 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(image, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def _decode(image_bytes: bytes) -> np.ndarray:
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Failed to decode image bytes")
    return image


def light_preprocess(image_bytes: bytes) -> np.ndarray:
    """Light preprocessor for full-page OCR preserving color/name details."""
    image = _decode(image_bytes)
    image = _remove_specular_highlights(image)

    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=1.6, tileGridSize=(8, 8))
    l = clahe.apply(l)
    image = cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)

    # light deskew only; large rotations can hurt full-page line structure
    image = _deskew(image, max_rotation=3.5)
    return image


def aggressive_preprocess(image_bytes: bytes) -> np.ndarray:
    """Aggressive preprocessor for MRZ extraction."""
    image = light_preprocess(image_bytes)

    blur = cv2.GaussianBlur(image, (0, 0), sigmaX=2.0)
    image = cv2.addWeighted(image, 1.5, blur, -0.5, 0)

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    denoised = cv2.bilateralFilter(gray, 7, 50, 50)
    thresholded = cv2.adaptiveThreshold(
        denoised,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        15,
    )
    return thresholded


def preprocess_image(image_bytes: bytes) -> np.ndarray:
    """Backward-compatible alias used by legacy code paths."""
    return aggressive_preprocess(image_bytes)
