"""PII patterns and masking (PRD 19.3). Patterns are shared with `lint --secrets` (15.4).

`anybrain anonymize <file>` prints a masked copy to stdout and never edits the file.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------- patterns

EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
# French national numbers (ten digits starting with 0) and international numbers (starting with +).
PHONE_FR_RE = re.compile(r"(?<![\d.+-])0[1-9](?:[ .-]?\d{2}){4}(?![\d.-])")
PHONE_INTL_RE = re.compile(r"(?<![\w+])\+\d[\d .()-]{6,18}\d(?!\d)")
IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")
# Card numbers: grouped 4-4-4-4 (or 4-4-4-1..7), Amex 4-6-5, or 13 to 19 digits in one run.
CARD_RE = re.compile(r"(?<![\d-])(?:\d{4}(?:[ -]\d{4}){2}[ -]\d{1,7}|\d{4}[ -]\d{6}[ -]\d{5}|\d{13,19})(?![\d-])")
AMOUNT_RE = re.compile(
    r"(?:[$€£]\s?\d[\d   ,.]*\d|[$€£]\s?\d)"
    r"|(?:\d[\d   ,.]*\s?(?:€|\$|£|EUR|USD|GBP|k€|K€)(?![A-Za-z]))"
)
API_KEY_PATTERNS = [
    ("API key (sk-)", re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}")),
    ("API key (AKIA)", re.compile(r"(?<![A-Z0-9])AKIA[0-9A-Z]{16}(?![A-Z0-9])")),
    ("API key (ghp_)", re.compile(r"(?<![A-Za-z0-9])ghp_[A-Za-z0-9]{36}")),
    ("API key (xox)", re.compile(r"(?<![A-Za-z0-9])xox[abposr]-[A-Za-z0-9-]{10,}")),
]


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def iban_ok(value: str) -> bool:
    """ISO 13616 mod-97 check."""
    compact = value.replace(" ", "").upper()
    if not 15 <= len(compact) <= 34:
        return False
    moved = compact[4:] + compact[:4]
    try:
        number = int("".join(str(int(ch, 36)) for ch in moved))
    except ValueError:
        return False
    return number % 97 == 1


def find_cards(text: str) -> list[re.Match]:
    """Card-like numbers that pass Luhn and start like a payment card (2 to 6), not an ISBN (978, 979)."""
    found = []
    for match in CARD_RE.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19 and digits[0] in "23456" and luhn_ok(digits):
            found.append(match)
    return found


def find_phones(text: str) -> list[re.Match]:
    found = list(PHONE_FR_RE.finditer(text))
    for match in PHONE_INTL_RE.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if 8 <= len(digits) <= 15:
            found.append(match)
    return found


def find_ibans(text: str, validate: bool = True) -> list[re.Match]:
    return [m for m in IBAN_RE.finditer(text) if not validate or iban_ok(m.group(0))]


# ---------------------------------------------------------------------------- masking (step 7)

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    path = Path(argv[0]).resolve()
    text = path.read_text(encoding="utf-8")
    text = EMAIL_RE.sub("<email>", text)
    for match in reversed(find_phones(text)):
        text = text[: match.start()] + "<phone>" + text[match.end():]
    sys.stdout.write(text)
    return 0
