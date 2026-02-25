from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .models import MRZData

try:
    from mrz.checker.td3 import TD3Checker
except Exception:  # pragma: no cover
    TD3Checker = None

try:
    from fastmrz import FastMRZ
except Exception:  # pragma: no cover
    FastMRZ = None

_WEIGHTS = (7, 3, 1)
_MRZ_LINE = re.compile(r"^[A-Z0-9<]{44}$")
_TD1_LINE = re.compile(r"^[A-Z0-9<]{30}$")


@dataclass
class MRZValidation:
    line1: str
    line2: str
    parsed: MRZData
    checks: dict[str, bool]

    @property
    def all_three_ok(self) -> bool:
        return self.checks.get("passport", False) and self.checks.get("birth_date", False) and self.checks.get("expiry", False)


class MRZParser:
    @staticmethod
    def _char_value(char: str) -> int:
        if char.isdigit():
            return int(char)
        if "A" <= char <= "Z":
            return ord(char) - 55
        if char == "<":
            return 0
        return 0

    @classmethod
    def checksum(cls, value: str) -> int:
        total = 0
        for i, ch in enumerate(value):
            total += cls._char_value(ch) * _WEIGHTS[i % 3]
        return total % 10

    @classmethod
    def validate(cls, value: str, check_char: str) -> bool:
        if not check_char or not check_char.isdigit():
            return False
        return cls.checksum(value) == int(check_char)

    @staticmethod
    def passport_hash(passport_number: str) -> str:
        return hashlib.sha256(passport_number.encode("utf-8")).hexdigest() if passport_number else ""

    @staticmethod
    def _normalize_line(line: str) -> str:
        return re.sub(r"[^A-Z0-9<]", "", (line or "").upper())

    def detect_td3_lines(self, text: str, *, image_bytes: bytes | None = None) -> tuple[str, str] | None:
        lines = [self._normalize_line(ln) for ln in (text or "").splitlines() if ln.strip()]
        candidates = [ln for ln in lines if len(ln) >= 30]
        for i in range(len(candidates) - 1):
            l1, l2 = candidates[i], candidates[i + 1]
            l1 = (l1 + "<" * 44)[:44]
            l2 = (l2 + "<" * 44)[:44]
            if _MRZ_LINE.match(l1) and _MRZ_LINE.match(l2):
                return l1, l2

        if image_bytes and FastMRZ is not None:
            try:
                detector = FastMRZ()
                found = detector.get_details(image_bytes)
                if isinstance(found, dict):
                    mrz_raw = str(found.get("mrz") or "")
                else:
                    mrz_raw = str(found or "")
                detected = [self._normalize_line(ln) for ln in mrz_raw.splitlines() if ln.strip()]
                if len(detected) >= 2:
                    l1 = (detected[0] + "<" * 44)[:44]
                    l2 = (detected[1] + "<" * 44)[:44]
                    if _MRZ_LINE.match(l1) and _MRZ_LINE.match(l2):
                        return l1, l2
            except Exception:
                return None

        return None

    def parse_td3(self, line1: str, line2: str) -> MRZValidation:
        l1 = self._normalize_line(line1)
        l2 = self._normalize_line(line2)
        if len(l1) != 44 or len(l2) != 44:
            parsed = MRZData(confidence=0.0, checksum_ok=False, format="TD3")
            return MRZValidation(line1=l1, line2=l2, parsed=parsed, checks={"passport": False, "birth_date": False, "expiry": False})

        passport_number = l2[0:9].replace("<", "")
        names = l1[5:44].split("<<")
        surname = names[0].replace("<", " ").strip()
        given = names[1].replace("<", " ").strip() if len(names) > 1 else ""

        checks = {
            "passport": self.validate(l2[0:9], l2[9]),
            "birth_date": self.validate(l2[13:19], l2[19]),
            "expiry": self.validate(l2[21:27], l2[27]),
            "composite": self.validate(l2[0:10] + l2[13:20] + l2[21:43], l2[43]),
        }

        if TD3Checker is not None:
            try:
                td3_obj = TD3Checker(l1, l2)
                checker_ok = bool(getattr(td3_obj, "valid", False))
                checks["checker"] = checker_ok
            except Exception:
                checks["checker"] = False

        valid_count = sum(1 for key in ("passport", "birth_date", "expiry") if checks.get(key))
        parsed = MRZData(
            format="TD3",
            document_type=l1[0],
            issuing_country=l1[2:5].replace("<", ""),
            surname=surname,
            given_names=given,
            passport_hash=self.passport_hash(passport_number),
            nationality=l2[10:13].replace("<", ""),
            birth_date=l2[13:19],
            sex=l2[20],
            expiry_date=l2[21:27],
            checksum_ok=valid_count == 3,
            confidence=valid_count / 3,
        )
        return MRZValidation(line1=l1, line2=l2, parsed=parsed, checks=checks)


    def _parse_td1(self, l1: str, l2: str, l3: str) -> MRZData:
        passport_number = l1[5:14].replace("<", "")
        checks = [
            self.validate(l1[5:14], l1[14]),
            self.validate(l2[0:6], l2[6]),
            self.validate(l2[8:14], l2[14]),
        ]
        names = l3.split("<<")
        surname = names[0].replace("<", " ").strip()
        given = names[1].replace("<", " ").strip() if len(names) > 1 else ""
        score = sum(1 for x in checks if x) / len(checks)
        return MRZData(
            format="TD1",
            document_type=l1[0],
            issuing_country=l1[2:5],
            surname=surname,
            given_names=given,
            passport_hash=self.passport_hash(passport_number),
            nationality=l2[15:18].replace("<", ""),
            birth_date=l2[0:6],
            sex=l2[7],
            expiry_date=l2[8:14],
            checksum_ok=all(checks),
            confidence=score,
        )

    def parse(self, lines: list[str]) -> MRZData:
        cleaned = [self._normalize_line(ln) for ln in lines if ln]
        if len(cleaned) >= 3 and all(_TD1_LINE.match((ln + "<" * 30)[:30]) for ln in cleaned[:3]):
            l1, l2, l3 = [(ln + "<" * 30)[:30] for ln in cleaned[:3]]
            return self._parse_td1(l1, l2, l3)
        if len(cleaned) >= 2 and all(_MRZ_LINE.match((ln + "<" * 44)[:44]) for ln in cleaned[:2]):
            l1, l2 = [(ln + "<" * 44)[:44] for ln in cleaned[:2]]
            val = self.parse_td3(l1, l2)
            return val.parsed
        return MRZData(confidence=0.0, checksum_ok=False, format="TD3")
