import io
import logging
import re

import cv2
import numpy as np
import pytesseract
from PIL import Image

logger = logging.getLogger(__name__)

MRZ_REGEX = re.compile(r"([A-Z0-9<]{20,})\s*[\n\r]+([A-Z0-9<]{20,})", re.MULTILINE)


def image_bytes_to_pil(img_bytes):
    return Image.open(io.BytesIO(img_bytes))


def preprocess_for_mrz_cv(image: Image.Image):
    """OpenCV preprocessing to enhance MRZ readability"""
    img = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # increase contrast / threshold
    gray = cv2.equalizeHist(gray)
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return th


# Note: use pytesseract directly on the whole image, then search for MRZ lines.
def extract_text_from_image_bytes(img_bytes):
    # PIL -> pytesseract
    pil = image_bytes_to_pil(img_bytes)
    text = pytesseract.image_to_string(pil, lang='eng')  # MRZ uses Latin charset
    return text


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


def parse_td3_mrz(line1: str, line2: str):
    """Parse TD3 passport MRZ (2 lines, 44 chars each normally). Returns dict with fields if possible."""
    # pad to expected lengths to avoid IndexError
    l1 = line1 + "<" * (44 - len(line1)) if len(line1) < 44 else line1
    l2 = line2 + "<" * (44 - len(line2)) if len(line2) < 44 else line2
    data = {}
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
        data['passport_number'] = l2[0:9].replace('<', '').strip()
        data['passport_number_check'] = l2[9]
        data['nationality'] = l2[10:13].replace('<', '').strip()
        bdate = l2[13:19]
        data['birth_date'] = f"{bdate[0:2]}{bdate[2:4]}{bdate[4:6]}"  # YYMMDD
        data['sex'] = l2[20]
        expiry = l2[21:27]
        data['expiry_date'] = f"{expiry[0:2]}{expiry[2:4]}{expiry[4:6]}"
    except Exception as e:
        logger.exception("Error parsing MRZ: %s", e)
    return data
