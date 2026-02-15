import hashlib
import io
import logging
import re

import cv2
import numpy as np
import pytesseract
from PIL import Image

logger = logging.getLogger(__name__)

MRZ_REGEX = re.compile(r"([A-Z0-9<]{20,})\s*[\n\r]+([A-Z0-9<]{20,})", re.MULTILINE)
_CHECKSUM_WEIGHTS = (7, 3, 1)
NUM_MAP = {"O": "0", "Q": "0", "I": "1", "L": "1", "B": "8", "S": "5", "G": "6"}


def compute_mrz_hash(line1: str | None, line2: str | None) -> str | None:
    l1 = (line1 or "").strip()
    l2 = (line2 or "").strip()
    if not l1 and not l2:
        return None
    value = f"{l1}|{l2}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest().lower()


def image_bytes_to_pil(img_bytes):
    return Image.open(io.BytesIO(img_bytes))


def preprocess_for_mrz_cv(image: Image.Image):
    """OpenCV preprocessing to enhance MRZ readability"""
    return preprocess_for_mrz_cv_mode(image, mode="current")


def preprocess_for_mrz_cv_mode(image: Image.Image, mode: str = "current"):
    """Preprocess image for MRZ OCR using one of supported modes."""
    img = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)

    if mode == "adaptive":
        return cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31,
            2,
        )

    if mode == "morphology":
        _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        return cv2.morphologyEx(th, cv2.MORPH_CLOSE, kernel)

    # current threshold mode
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return th


# Note: use pytesseract directly on the whole image, then search for MRZ lines.
def extract_text_from_image_bytes(img_bytes):
    # PIL -> pytesseract
    pil = image_bytes_to_pil(img_bytes)
    text = pytesseract.image_to_string(pil, lang='eng')  # MRZ uses Latin charset
    return text


def extract_mrz_from_image_bytes(img_bytes):
    """Run MRZ extraction on multiple preprocess variants until MRZ lines are found."""
    pil = image_bytes_to_pil(img_bytes)
    preprocess_modes = ("current", "adaptive", "morphology")

    for mode in preprocess_modes:
        try:
            processed = preprocess_for_mrz_cv_mode(pil, mode=mode)
            text = pytesseract.image_to_string(processed, lang='eng')
        except Exception as exc:
            logger.warning("[OCR] MRZ preprocess failed: mode=%s, error=%s", mode, exc)
            continue

        line1, line2 = find_mrz_from_text(text)
        if line1 and line2:
            logger.info("[OCR] MRZ found with preprocess=%s", mode)
            return line1, line2, text, mode

    return None, None, "", None


def find_mrz_from_text(text):
    # Normalize: remove spaces on MRZ lines
    # We look for two consecutive lines with many '<'
    candidates = MRZ_REGEX.findall(text.replace(" ", "").replace("\r", "\n"))
    if candidates:
        # MRZ_REGEX returns tuples (line1, line2)
        for l1, l2 in candidates:
            # choose first plausible (length check)
            if len(l1) >= 30 and len(l2) >= 30:
                return l1.strip(), l2.strip()
    # fallback: search for sequences with many '<'
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for i in range(len(lines)-1):
        a, b = lines[i], lines[i+1]
        if a.count('<') >= 3 and b.count('<') >= 3 and len(a) >= 25 and len(b) >= 25:
            return a.replace(" ", ""), b.replace(" ", "")
    return None, None


def _mrz_char_value(ch: str) -> int:
    if ch.isdigit():
        return int(ch)
    if 'A' <= ch <= 'Z':
        return ord(ch) - ord('A') + 10
    if ch == '<':
        return 0
    return 0


def compute_mrz_checksum(value: str) -> int:
    total = 0
    for idx, ch in enumerate(value):
        total += _mrz_char_value(ch) * _CHECKSUM_WEIGHTS[idx % 3]
    return total % 10


def normalize_for_numeric(s: str) -> str:
    s = s.upper()
    return "".join(NUM_MAP.get(ch, ch) for ch in s)


def validate_mrz_checksum(value: str, check_char: str) -> bool:
    if not check_char or not check_char.isdigit():
        return False
    return compute_mrz_checksum(value) == int(check_char)


def validate_td3_composite(l2: str) -> bool:
    """Validate TD3 composite checksum from line 2 (position 43)."""
    if len(l2) < 44:
        l2 = l2 + "<" * (44 - len(l2))

    composite_check = l2[43]

    part_doc = normalize_for_numeric(l2[0:10])     # passport + check
    part_birth = normalize_for_numeric(l2[13:20])  # birth + check
    part_exp = normalize_for_numeric(l2[21:28])    # expiry + check
    optional = l2[28:43]                           # may contain letters → no normalize

    composite_value = part_doc + part_birth + part_exp + optional
    return validate_mrz_checksum(composite_value, composite_check)


def parse_td3_mrz(line1: str, line2: str):
    """Parse TD3 passport MRZ (2 lines, 44 chars each normally). Returns dict with fields if possible."""
    # pad to expected lengths to avoid IndexError
    l1 = line1 + "<" * (44 - len(line1)) if len(line1) < 44 else line1
    l2 = line2 + "<" * (44 - len(line2)) if len(line2) < 44 else line2
    data = {}
    checks = {}
    try:
        # line1
        data['document_type'] = l1[0]
        data['issuing_country'] = l1[2:5]
        names = l1[5:44].split('<<')
        surname = names[0].replace('<', ' ').strip()
        given = names[1].replace('<', ' ').strip() if len(names) > 1 else ""
        data['surname'] = surname
        data['given_names'] = given

        # line2
        passport_number_raw = l2[0:9]
        passport_check = l2[9]
        birth_date_raw = l2[13:19]
        birth_check = l2[19]
        expiry_raw = l2[21:27]
        expiry_check = l2[27]

        passport_number_norm = normalize_for_numeric(passport_number_raw)
        birth_date_norm = normalize_for_numeric(birth_date_raw)
        expiry_norm = normalize_for_numeric(expiry_raw)

        data['passport_number'] = passport_number_raw.replace('<', '').strip()
        data['passport_number_check'] = passport_check
        data['nationality'] = l2[10:13].replace('<', '').strip()
        data['birth_date'] = f"{birth_date_raw[0:2]}{birth_date_raw[2:4]}{birth_date_raw[4:6]}"  # YYMMDD
        data['sex'] = l2[20]
        data['expiry_date'] = f"{expiry_raw[0:2]}{expiry_raw[2:4]}{expiry_raw[4:6]}"

        checks["passport_number"] = validate_mrz_checksum(passport_number_norm, passport_check)
        checks["birth_date"] = validate_mrz_checksum(birth_date_norm, birth_check)
        checks["expiry_date"] = validate_mrz_checksum(expiry_norm, expiry_check)
        checks["composite"] = validate_td3_composite(l2)

        if not checks["passport_number"]:
            logger.warning(
                "[OCR] MRZ checksum failed: field=passport_number hash=%s len=%s normalized_len=%s check_char=%s computed=%s",
                compute_mrz_hash(passport_number_raw, None),
                len(passport_number_raw),
                len(passport_number_norm),
                passport_check,
                compute_mrz_checksum(passport_number_norm),
            )
        if not checks["birth_date"]:
            logger.warning(
                "[OCR] MRZ checksum failed: field=birth_date hash=%s len=%s normalized_len=%s check_char=%s computed=%s",
                compute_mrz_hash(birth_date_raw, None),
                len(birth_date_raw),
                len(birth_date_norm),
                birth_check,
                compute_mrz_checksum(birth_date_norm),
            )
        if not checks["expiry_date"]:
            logger.warning(
                "[OCR] MRZ checksum failed: field=expiry_date hash=%s len=%s normalized_len=%s check_char=%s computed=%s",
                compute_mrz_hash(expiry_raw, None),
                len(expiry_raw),
                len(expiry_norm),
                expiry_check,
                compute_mrz_checksum(expiry_norm),
            )
        if not checks["composite"]:
            part_doc = normalize_for_numeric(l2[0:10])
            part_birth = normalize_for_numeric(l2[13:20])
            part_exp = normalize_for_numeric(l2[21:28])
            optional = l2[28:43]
            composite_value = part_doc + part_birth + part_exp + optional
            logger.warning(
                "[OCR] MRZ checksum failed: field=composite hash=%s len=%s check_char=%s computed=%s",
                compute_mrz_hash(composite_value, None),
                len(composite_value),
                l2[43],
                compute_mrz_checksum(composite_value),
            )
    except Exception as e:
        logger.exception("[OCR] Error parsing MRZ: %s", e)
        checks = {"passport_number": False, "birth_date": False, "expiry_date": False, "composite": False}

    check_weights = {
        "passport_number": 0.2,
        "birth_date": 0.2,
        "expiry_date": 0.2,
        "composite": 0.4,
    }
    mrz_confidence_score = sum(weight for key, weight in check_weights.items() if checks.get(key))
    checksum_ok = all(checks.get(key, False) for key in check_weights)

    data["_mrz_checksum_ok"] = checksum_ok
    data["mrz_confidence_score"] = float(mrz_confidence_score)
    return data
