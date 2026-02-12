import re
from datetime import datetime
from io import BytesIO
from typing import Dict, List, Optional

import cv2
import numpy as np
import pytesseract
from PIL import Image


def extract_text_from_image_bytes(image_bytes: bytes) -> str:
    """Extract raw OCR text from image bytes using pytesseract."""
    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    np_image = np.array(image)

    gray = cv2.cvtColor(np_image, cv2.COLOR_RGB2GRAY)
    denoised = cv2.bilateralFilter(gray, 9, 75, 75)
    _, thresh = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    text = pytesseract.image_to_string(
        thresh,
        config="--oem 3 --psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<",
    )
    return text


def find_mrz_from_text(text: str) -> Optional[List[str]]:
    """Find TD3 MRZ lines in OCR text and return two normalized lines."""
    normalized_lines = []
    for line in text.upper().splitlines():
        clean = re.sub(r"[^A-Z0-9<]", "", line)
        if len(clean) >= 40:
            normalized_lines.append(clean[:44].ljust(44, "<"))

    td3_line_pattern = re.compile(r"^[A-Z0-9<]{44}$")
    candidates = [line for line in normalized_lines if td3_line_pattern.match(line)]

    for i in range(len(candidates) - 1):
        line1, line2 = candidates[i], candidates[i + 1]
        if line1.startswith("P<") and re.match(r"^[A-Z0-9<]{44}$", line2):
            return [line1, line2]

    return None


def _format_mrz_date(date_str: str) -> str:
    """Convert YYMMDD from MRZ to YYYY-MM-DD."""
    if not re.match(r"^\d{6}$", date_str):
        return ""

    yy = int(date_str[:2])
    current_yy = int(datetime.utcnow().strftime("%y"))
    century = 1900 if yy > current_yy else 2000

    try:
        parsed = datetime.strptime(f"{century + yy}{date_str[2:]}", "%Y%m%d")
        return parsed.strftime("%Y-%m-%d")
    except ValueError:
        return ""


def parse_td3_mrz(mrz_lines: List[str]) -> Dict[str, str]:
    """Parse TD3 MRZ (passport) into structured fields."""
    if len(mrz_lines) != 2:
        return {
            "surname": "",
            "name": "",
            "passport_number": "",
            "birth_date": "",
            "expiry_date": "",
            "nationality": "",
        }

    line1 = mrz_lines[0].ljust(44, "<")[:44]
    line2 = mrz_lines[1].ljust(44, "<")[:44]

    name_part = line1[5:44]
    surname, *given_names = name_part.split("<<")
    surname = surname.replace("<", " ").strip()
    name = " ".join(part.replace("<", " ").strip() for part in given_names if part.strip("<"))

    passport_number = line2[0:9].replace("<", "").strip()
    nationality = line2[10:13].replace("<", "").strip()
    birth_date = _format_mrz_date(line2[13:19])
    expiry_date = _format_mrz_date(line2[21:27])

    return {
        "surname": surname,
        "name": name,
        "passport_number": passport_number,
        "birth_date": birth_date,
        "expiry_date": expiry_date,
        "nationality": nationality,
    }
