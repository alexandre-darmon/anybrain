#!/usr/bin/env python3
"""Mask PII in a page before it is sent to a frontier model.

Usage: anonymize.py <file.md>  → prints the masked text to stdout; never edits the file.
Masks: emails, phone numbers, IBANs, amounts in € or $, and names listed in <vault>/.harness/names.txt (one per line).
"""
import sys, re
from pathlib import Path

PATTERNS = [
    (r"[\w.+-]+@[\w-]+\.[\w.]+", "<email>"),
    (r"\+\d{1,3}[ .-]?\d(?:[ .-]?\d{2}){4}\b|\b0\d(?:[ .-]?\d{2}){4}\b", "<phone>"),
    (r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7} ?[A-Z0-9]{1,4}\b", "<iban>"),
    (r"\d[\d  .,]*\s?(?:€|\$|EUR|USD)", "<amount>"),
]


def main():
    path = Path(sys.argv[1]).resolve()
    text = path.read_text(encoding="utf-8")
    for pat, rep in PATTERNS:
        text = re.sub(pat, rep, text)
    for parent in path.parents:
        names = parent / ".harness" / "names.txt"
        if names.exists():
            for n in names.read_text().split():
                text = re.sub(re.escape(n), "<name>", text, flags=re.I)
            break
    sys.stdout.write(text)


if __name__ == "__main__":
    main()
