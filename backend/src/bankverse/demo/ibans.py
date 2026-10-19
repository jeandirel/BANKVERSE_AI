"""IBAN utilities (ISO 13616 mod-97) — used to generate valid *fictitious* IBANs for the DEMO bank
(bank code 99999, which no French bank uses) and to validate IBANs extracted from documents."""

from __future__ import annotations

import random
import re

_LETTERS = {chr(i + 65): str(i + 10) for i in range(26)}
_IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{10,30}$")
LENGTHS = {"FR": 27, "DE": 22, "ES": 24, "IT": 27, "BE": 16, "NL": 18, "LU": 20, "GB": 22, "CH": 21, "PT": 25,
           "AE": 23, "MT": 31, "CY": 28}


def _to_digits(s: str) -> str:
    return "".join(_LETTERS.get(c, c) for c in s)


def iban_check_digits(country: str, bban: str) -> str:
    rem = int(_to_digits(bban + country + "00")) % 97
    return f"{98 - rem:02d}"


def is_valid_iban(iban: str) -> bool:
    s = iban.replace(" ", "").upper()
    if not _IBAN_RE.match(s):
        return False
    if s[:2] in LENGTHS and len(s) != LENGTHS[s[:2]]:
        return False
    return int(_to_digits(s[4:] + s[:4])) % 97 == 1


def french_rib_key(bank: str, branch: str, account: str) -> str:
    acc = "".join(_conv_rib(c) for c in account)
    key = 97 - ((89 * int(bank) + 15 * int(branch) + 3 * int(acc)) % 97)
    return f"{key:02d}"


def _conv_rib(c: str) -> str:
    if c.isdigit():
        return c
    table = {**dict.fromkeys("AJ", "1"), **dict.fromkeys("BKS", "2"), **dict.fromkeys("CLT", "3"),
             **dict.fromkeys("DMU", "4"), **dict.fromkeys("ENV", "5"), **dict.fromkeys("FOW", "6"),
             **dict.fromkeys("GPX", "7"), **dict.fromkeys("HQY", "8"), **dict.fromkeys("IRZ", "9")}
    return table[c.upper()]


def make_fr_iban(rng: random.Random, bank: str = "99999", branch: str | None = None) -> str:
    branch = branch or f"{rng.randint(1, 99):05d}"
    account = f"{rng.randint(0, 10**11 - 1):011d}"
    bban = bank + branch + account + french_rib_key(bank, branch, account)
    return "FR" + iban_check_digits("FR", bban) + bban


def make_foreign_iban(rng: random.Random, country: str) -> str:
    length = LENGTHS.get(country, 22)
    bban = "".join(str(rng.randint(0, 9)) for _ in range(length - 4))
    return country + iban_check_digits(country, bban) + bban


def format_iban(iban: str) -> str:
    s = iban.replace(" ", "")
    return " ".join(s[i:i + 4] for i in range(0, len(s), 4))
