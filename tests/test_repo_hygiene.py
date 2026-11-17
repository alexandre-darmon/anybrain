"""Repo-wide checks from PRD sections 0, 23 and 24 step 12.

- No em dash (U+2014) in any tracked text file.
- No email address or phone number outside vault.example/ and tests/fixtures/ (the repo is public).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EM_DASH = chr(0x2014)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
PHONE_FR = re.compile(r"(?<![\d.-])0[1-9](?:[ .-]?\d{2}){4}(?![\d.-])")
PHONE_INTL = re.compile(r"\+\d[\d .()-]{6,18}\d")
PII_ALLOWED = ("vault.example/", "tests/fixtures/")


def tracked_files() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout
        names = [n for n in out.splitlines() if n]
    except (OSError, subprocess.CalledProcessError):
        names = [p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts]
    files = []
    for name in names:
        path = ROOT / name
        if path.is_file() and not path.is_symlink():
            files.append(path)
    return files


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def test_no_em_dash_anywhere():
    offenders = []
    for path in tracked_files():
        text = read_text(path)
        if text and EM_DASH in text:
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == [], f"em dash found in: {offenders}"


def _phones(text: str) -> list[str]:
    found = PHONE_FR.findall(text)
    for match in PHONE_INTL.findall(text):
        digits = re.sub(r"\D", "", match)
        if 8 <= len(digits) <= 15:
            found.append(match)
    return found


def test_no_email_or_phone_outside_fake_data():
    offenders = []
    for path in tracked_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(PII_ALLOWED):
            continue
        text = read_text(path)
        if not text:
            continue
        if EMAIL.search(text) or _phones(text):
            offenders.append(rel)
    assert offenders == [], f"email or phone pattern found in: {offenders}"
