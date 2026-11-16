"""PII patterns and masking (PRD 19.3). The patterns are shared with `lint --secrets` (15.4).

`anybrain anonymize <file>` prints a masked copy to stdout and never edits the file. It masks API keys,
emails, IBANs, card numbers, amounts with a currency, phone numbers (French and international), and the
names listed in `.harness/names.txt` and `.harness/clients.txt` of the file's vault. Dates are set aside
before masking and put back after, so they are never masked. Outside the local profile it refuses to print
a local-only file (company/, finance/, me/, journal/, raw/docs/, .harness/, confidential pages), because a
script would otherwise get around the harness deny rules (docs/decisions.md D026).
"""
from __future__ import annotations

import os
import re
import sys
import unicodedata
from pathlib import Path

from . import vault as v

# ---------------------------------------------------------------------------- patterns

NBSP_FOLD = str.maketrans({" ": " ", " ": " ", " ": " "})

EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.%+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}")
# French national numbers (ten digits starting with 0), possibly ending a sentence, and international
# numbers starting with + or 00 and a country code.
PHONE_FR_RE = re.compile(r"(?<![\d.+-])0[1-9](?:[ .-]?\d{2}){4}(?![\d-]|\.\d)")
PHONE_INTL_RE = re.compile(r"(?<![\w+])(?:\+|00(?=[1-9]))\d[\d .()-]{6,18}\d(?!\d)")
# IBAN candidates in any letter case, grouped by spaces or hyphens; checked with mod-97.
IBAN_LOOSE_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{2}\d{2}(?:[ -]?[A-Za-z0-9]){11,30}")
# The upper-case shape alone, masked by anonymize even when the checksum fails (over-masking is safe).
IBAN_SHAPE_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:[ -]?[A-Z0-9]{4}){2,7}(?:[ -]?[A-Z0-9]{1,4})?\b")
# Card numbers: groups of 4 separated by space, dot or hyphen, Amex 4-6-5, or 13 to 19 digits in one run.
CARD_RE = re.compile(
    r"(?<![\d.-])(?:\d{4}(?:[ .-]\d{4}){2}[ .-]\d{1,7}|\d{4}[ -]\d{6}[ -]\d{5}|\d{13,19})(?![\d-]|\.\d)"
)
_NUM = r"(?:\d{1,3}(?:[ .,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
_CODES = r"(?:EUR|USD|GBP|CHF|JPY|CAD)"
_SIGNS = "[$€£¥]"
_SUFFIX = (
    rf"(?:k|K|M|Md|Mds|Mrd)?\s?{_SIGNS}"
    rf"|(?:k|K|M)?{_CODES}\b"
    r"|(?:k|K|M)\s?euros?\b|euros?\b|dollars?\b"
    r"|(?:millions?|milliards?|mille)\s+d['’]\s?euros\b"
    r"|(?:millions?|milliards?)\s+(?:de\s+)?dollars?\b"
)
AMOUNT_RE = re.compile(
    rf"(?<![\w.,])(?:(?:{_SIGNS}|{_CODES}\s?)\s?{_NUM}(?:\s?(?:k|K|M|Md|bn)\b)?|{_NUM}\s?(?:{_SUFFIX}))"
)
DATE_RE = re.compile(
    r"(?<![\d/.-])(?:\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"|(?:0?[1-9]|[12]\d|3[01])[/.](?:0?[1-9]|1[0-2])[/.](?:\d{4}|\d{2}))(?![\d]|[/.-]\d)"
)
API_KEY_PATTERNS = [
    ("API key (sk-)", re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}")),
    ("API key (AKIA)", re.compile(r"(?<![A-Z0-9])AKIA[0-9A-Z]{16}(?![A-Z0-9])")),
    ("API key (ghp_)", re.compile(r"(?<![A-Za-z0-9])ghp_[A-Za-z0-9]{36}")),
    ("API key (xox)", re.compile(r"(?<![A-Za-z0-9])xox[abposr]-[A-Za-z0-9-]{10,}")),
]

Span = tuple[int, int]


def fold_spaces(text: str) -> str:
    """Non-breaking spaces become plain spaces; the length of the text does not change."""
    return text.translate(NBSP_FOLD)


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
    compact = re.sub(r"[ -]", "", value).upper()
    if not 15 <= len(compact) <= 34 or not compact[:2].isalpha() or not compact[2:4].isdigit():
        return False
    moved = compact[4:] + compact[:4]
    try:
        number = int("".join(str(int(ch, 36)) for ch in moved))
    except ValueError:
        return False
    return number % 97 == 1


def find_ibans(text: str, validate: bool = True) -> list[Span]:
    """IBANs that pass mod-97, trimming trailing words (`FR76 ... 189 now`). Without `validate`, the
    upper-case IBAN shape is also returned when the checksum fails."""
    found: list[Span] = []
    for match in IBAN_LOOSE_RE.finditer(text):
        s, start = match.group(0), match.start()
        alnum = [i for i, ch in enumerate(s) if ch.isalnum()]
        for k in range(min(len(alnum), 34), 14, -1):
            end = alnum[k - 1] + 1
            if end < len(s) and s[end] not in " -":
                continue
            if iban_ok(s[:end]):
                found.append((start, start + end))
                break
    if not validate:
        found += [m.span() for m in IBAN_SHAPE_RE.finditer(text)]
    return found


def find_cards(text: str) -> list[Span]:
    """Card-like numbers that pass Luhn and start like a payment card (2 to 6), not an ISBN (978, 979)."""
    found = []
    for match in CARD_RE.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19 and digits[0] in "23456" and luhn_ok(digits):
            found.append(match.span())
    return found


def find_phones(text: str) -> list[Span]:
    found = [m.span() for m in PHONE_FR_RE.finditer(text)]
    for match in PHONE_INTL_RE.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if 8 <= len(digits) <= 15:
            found.append(match.span())
    return sorted(found)


def find_emails(text: str) -> list[Span]:
    return [m.span() for m in EMAIL_RE.finditer(text)]


def find_keys(text: str) -> list[Span]:
    return [m.span() for _, pattern in API_KEY_PATTERNS for m in pattern.finditer(text)]


# ---------------------------------------------------------------------------- masking

def _replace(text: str, spans: list[Span], placeholder: str) -> str:
    """Replace non-overlapping spans, right to left so earlier offsets stay valid; longest span wins."""
    chosen: list[Span] = []
    for start, end in sorted(set(spans), key=lambda s: (s[0], -(s[1] - s[0]))):
        if chosen and start < chosen[-1][1]:
            continue
        chosen.append((start, end))
    for start, end in reversed(chosen):
        text = text[:start] + placeholder + text[end:]
    return text


def _token(i: int) -> str:
    """A placeholder made of private-use characters: no letter, digit or space for a pattern to match."""
    return "" + "".join(chr(0xE010 + int(d)) for d in str(i)) + ""


def name_pattern(name: str) -> re.Pattern:
    """`Acme Bank` also matches `acme-bank`, `Acme_Bank`, `AcmeBank` and `Acme\\nBank`."""
    variants = {name, unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")}
    parts = []
    for variant in variants:
        words = [w for w in re.split(r"[\s_-]+", variant.strip()) if w]
        if words:
            parts.append(r"[\s_-]*".join(re.escape(w) for w in words))
    return re.compile(r"(?<![^\W_])(?:" + "|".join(sorted(parts, key=len, reverse=True)) + r")(?![^\W_])", re.I)


def read_names(path: Path) -> list[str]:
    if not path.is_file():
        return []
    names = [line.strip() for line in v.read_text(path).split("\n")]
    return sorted({n for n in names if n and not n.startswith("#")}, key=len, reverse=True)


def mask(text: str, names: list[str] = (), clients: list[str] = ()) -> str:
    """The masked text, with dates untouched."""
    text = fold_spaces(text)
    dates: list[str] = []

    def keep(match: re.Match) -> str:
        dates.append(match.group(0))
        return _token(len(dates) - 1)

    text = DATE_RE.sub(keep, text)
    text = _replace(text, find_keys(text), "<secret>")
    text = _replace(text, find_emails(text), "<email>")
    text = _replace(text, find_ibans(text, validate=False), "<iban>")
    text = _replace(text, find_cards(text), "<card>")
    text = _replace(text, [m.span() for m in AMOUNT_RE.finditer(text)], "<amount>")
    text = _replace(text, find_phones(text), "<phone>")
    for placeholder, group in (("<client>", clients), ("<name>", names)):
        for name in sorted(group, key=len, reverse=True):
            text = name_pattern(name).sub(placeholder, text)
    for i, date in enumerate(dates):
        text = text.replace(_token(i), date, 1)
    return text


def find_vault(file: Path) -> Path | None:
    """ANYBRAIN_VAULT when set, else the nearest parent folder that has a `.harness/` folder."""
    env = os.environ.get("ANYBRAIN_VAULT")
    if env:
        return Path(env).expanduser().resolve()
    for parent in file.resolve().parents:
        if (parent / ".harness").is_dir():
            return parent
    return None


def local_only_file(vault: Path, path: Path) -> str | None:
    """The vault-relative path when the file must stay on the local profile, else None."""
    try:
        rel = v.rel_path(vault, path.resolve())
    except ValueError:
        return None
    if rel.split("/", 1)[0] in v.LOCAL_ONLY_FOLDERS or rel.startswith(("raw/docs/", ".harness/", ".")):
        return rel
    if v.is_page_path(rel) and v.load_page(vault, path.resolve()).local_only:
        return rel
    return None


def local_only_outside_vault(path: Path) -> str | None:
    """No vault found: judge by the folder names on the path and by the file's own frontmatter."""
    parts = path.resolve().parts[:-1]
    if any(part in v.LOCAL_ONLY_FOLDERS or part == ".harness" for part in parts) or "/raw/docs/" in path.resolve().as_posix():
        return str(path)
    if path.suffix == ".md":
        try:
            fm, *_ = v.parse_frontmatter(v.read_text(path))
        except UnicodeDecodeError:
            return None
        if fm and (fm.get("kind") == "mission" or fm.get("sensitivity") == "confidential"):
            return str(path)
    return None


def run(file: str) -> int:
    path = Path(file).expanduser()
    if not path.is_file():
        raise v.VaultError(f"file not found: {file}")
    vault = find_vault(path)
    profile = os.environ.get("ANYBRAIN_PROFILE", "frontier").strip().lower() or "frontier"
    if profile != "local":
        rel = local_only_file(vault, path) if vault is not None else local_only_outside_vault(path)
        if rel:
            print(f"{rel} (confidential: local profile only). It is not sent to a frontier model, even masked; "
                  "open a session with ANYBRAIN_PROFILE=local to read it.", file=sys.stderr)
            return 1
    names = read_names(vault / ".harness" / "names.txt") if vault else []
    clients = read_names(vault / ".harness" / "clients.txt") if vault else []
    sys.stdout.write(mask(v.read_text(path), names, clients))
    return 0
