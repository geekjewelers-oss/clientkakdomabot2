from __future__ import annotations

import hashlib
import re

from .models import MRZData

_WEIGHTS = (7, 3, 1)


class MRZParser:
    td3_pattern = re.compile(r"^[A-Z0-9<]{44}$")
    td1_pattern = re.compile(r"^[A-Z0-9<]{30}$")

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
        return hashlib.sha256(passport_number.encode("utf-8")).hexdigest()

    def parse(self, lines: list[str]) -> MRZData:
        cleaned = [re.sub(r"[^A-Z0-9<]", "", ln.upper()) for ln in lines]
        if len(cleaned) == 2 and all(self.td3_pattern.match(ln or "") for ln in cleaned):
            return self._parse_td3(cleaned[0], cleaned[1])
        if len(cleaned) == 3 and all(self.td1_pattern.match(ln or "") for ln in cleaned):
            return self._parse_td1(cleaned[0], cleaned[1], cleaned[2])
        return MRZData(confidence=0.0, checksum_ok=False)

    def _parse_td3(self, l1: str, l2: str) -> MRZData:
        passport_number = l2[0:9].replace("<", "")
        checks = [
            self.validate(l2[0:9], l2[9]),
            self.validate(l2[13:19], l2[19]),
            self.validate(l2[21:27], l2[27]),
            self.validate(l2[0:10] + l2[13:20] + l2[21:43], l2[43]),
        ]
        names = l1[5:44].split("<<")
        surname = names[0].replace("<", " ").strip()
        given = names[1].replace("<", " ").strip() if len(names) > 1 else ""
        score = sum(1 for x in checks if x) / len(checks)
        return MRZData(
            format="TD3",
            document_type=l1[0],
            issuing_country=l1[2:5],
            surname=surname,
            given_names=given,
            passport_hash=self.passport_hash(passport_number),
            nationality=l2[10:13].replace("<", ""),
            birth_date=l2[13:19],
            sex=l2[20],
            expiry_date=l2[21:27],
            checksum_ok=all(checks),
            confidence=score,
        )

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
