"""PII patterns and masking (PRD 19.3). The patterns are shared with `lint --secrets` (15.4).

`anybrain anonymize <file>` prints a masked copy to stdout and never edits the file. It masks emails, phone
numbers (French and international), IBANs, card numbers, amounts with a currency, and the names listed in
`.harness/names.txt` and `.harness/clients.txt` of the file's vault. Dates are never masked.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from . import vault as v

# ---------------------------------------------------------------------------- patterns

EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
# French national numbers (ten digits starting with 0) and international numbers (starting with +).
PHONE_FR_RE = re.compile(r"(?<![\d.+-])0[1-9](?:[ .-]?\d{2}){4}(?![\d.-])")
PHONE_INTL_RE = re.compile(r"(?<![\w+])\+\d[\d .()-]{6,18}\d(?!\d)")
IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")
# Card numbers: grouped 4-4-4-4 (or 4-4-4-1..7), Amex 4-6-5, or 13 to 19 digits in one run.
CARD_RE = re.compile(r"(?<![\d-])(?:\d{4}(?:[ -]\d{4}){2}[ -]\d{1,7}|\d{4}[ -]\d{6}[ -]\d{5}|\d{13,19})(?![\d-])")
_SPACES = "   "
_CURRENCY = "$€£"
AMOUNT_RE = re.compile(
    rf"[{_CURRENCY}][{_SPACES}]?\d(?:[\d{_SPACES},.]*\d)?"
    rf"|\d(?:[\d{_SPACES},.]*\d)?[{_SPACES}]?(?:k?[{_CURRENCY}]|K€|EUR|USD|GBP)(?![A-Za-z])"
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
    return sorted(found, key=lambda m: m.start())


def find_ibans(text: str, validate: bool = True) -> list[re.Match]:
    return [m for m in IBAN_RE.finditer(text) if not validate or iban_ok(m.group(0))]


# ---------------------------------------------------------------------------- masking

def _replace(text: str, matches: list[re.Match], placeholder: str) -> str:
    """Replace non-overlapping matches, right to left so earlier offsets stay valid."""
    last_start = len(text) + 1
    for match in sorted(matches, key=lambda m: m.start(), reverse=True):
        if match.end() > last_start:
            continue
        text = text[: match.start()] + placeholder + text[match.end():]
        last_start = match.start()
    return text


def read_names(path: Path) -> list[str]:
    if not path.is_file():
        return []
    names = [line.strip() for line in v.read_text(path).split("\n")]
    return sorted({n for n in names if n and not n.startswith("#")}, key=len, reverse=True)


def find_vault(file: Path) -> Path | None:
    """ANYBRAIN_VAULT when set, else the nearest parent folder that has a `.harness/` folder."""
    env = os.environ.get("ANYBRAIN_VAULT")
    if env:
        return Path(env).expanduser().resolve()
    for parent in file.resolve().parents:
        if (parent / ".harness").is_dir():
            return parent
    return None


def mask(text: str, names: list[str] = (), clients: list[str] = ()) -> str:
    """The masked text. IBANs are masked without the checksum test: over-masking is the safe side."""
    text = _replace(text, list(EMAIL_RE.finditer(text)), "<email>")
    text = _replace(text, find_ibans(text, validate=False), "<iban>")
    text = _replace(text, find_cards(text), "<card>")
    text = _replace(text, find_phones(text), "<phone>")
    text = _replace(text, list(AMOUNT_RE.finditer(text)), "<amount>")
    for placeholder, group in (("<client>", clients), ("<name>", names)):
        for name in sorted(group, key=len, reverse=True):
            pattern = re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)", re.I)
            text = pattern.sub(placeholder, text)
    return text


def run(file: str) -> int:
    path = Path(file).expanduser()
    if not path.is_file():
        raise v.VaultError(f"file not found: {file}")
    vault = find_vault(path)
    names = read_names(vault / ".harness" / "names.txt") if vault else []
    clients = read_names(vault / ".harness" / "clients.txt") if vault else []
    sys.stdout.write(mask(v.read_text(path), names, clients))
    return 0
