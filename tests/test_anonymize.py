"""anonymize: PII masking (PRD 19.3). PII-looking test data is built at run time (docs/decisions.md D006)."""
from __future__ import annotations

import pytest

from anybrain import anonymize as a
from anybrain import cli

EMAIL = "jane.doe" + "@" + "example.com"
PHONE_FR = "06 12 " + "34 56 78"
PHONE_FR_DOTS = "01." + "23.45.67.89"
PHONE_INTL = "+33 6 12 " + "34 56 78"
PHONE_US = "+1 415 " + "555 0100"


@pytest.mark.parametrize("text, expected", [
    (f"write to {EMAIL} today", "write to <email> today"),
    (f"call {PHONE_FR}", "call <phone>"),
    (f"call {PHONE_FR_DOTS}", "call <phone>"),
    (f"call {PHONE_INTL}", "call <phone>"),
    (f"call {PHONE_US}", "call <phone>"),
    ("pay FR76 3000 6000 0112 3456 7890 189 now", "pay <iban> now"),
    ("card 4111 1111 1111 1111 expires", "card <card> expires"),
    ("card 4111111111111111 expires", "card <card> expires"),
    ("amex 3782 822463 10005", "amex <card>"),
    ("budget 12 500 € this year", "budget <amount> this year"),
    ("budget € 1.200,50 this year", "budget <amount> this year"),
    ("raised $3,000 and 40 k€ and 15 EUR and 20 USD", "raised <amount> and <amount> and <amount> and <amount>"),
])
def test_masks_each_kind(text, expected):
    assert a.mask(text) == expected


@pytest.mark.parametrize("text", [
    "2026-01-10",  # the regression test required by 19.3
    "On 2026-01-10 2026-02-10 2026-03-10 three purchases",
    "ISBN 9780306406157 is a book",
    "version 1.2.3, 444 bestsellers, loc. 412",
    "card-like but invalid 4111 1111 1111 1112",
])
def test_does_not_mask_dates_and_ordinary_numbers(text):
    assert a.mask(text) == text


def test_a_ten_digit_number_starting_with_0_is_masked_as_a_phone():
    """An ISBN-10 such as this one is also a valid French landline: masking it is the safe side."""
    isbn10 = "03064" + "06152"
    assert a.mask(f"isbn {isbn10}") == "isbn <phone>"


def test_masks_names_and_clients_whole_words_case_insensitive():
    text = "Jane Doe met ACME BANK; acme bankers and Janet are fine."
    assert a.mask(text, names=["Jane Doe"], clients=["Acme Bank"]) == \
        "<name> met <client>; acme bankers and Janet are fine."


def test_longer_names_win_over_shorter_ones():
    assert a.mask("Jane Doe and Jane", names=["Jane", "Jane Doe"]) == "<name> and <name>"


def test_email_is_masked_before_names():
    assert a.mask(f"mail {EMAIL}", names=["Jane"]) == "mail <email>"


def test_cli_reads_names_from_the_file_vault_and_never_edits_the_file(make_vault, capsys):
    root = make_vault({
        ".harness/names.txt": "# comment\nJane Doe\n",
        ".harness/clients.txt": "Acme Bank\n",
        "projects/p.md": f"Jane Doe of Acme Bank, {EMAIL}, on 2026-01-10.\n",
    })
    before = (root / "projects" / "p.md").read_bytes()
    assert cli.main(["anonymize", str(root / "projects" / "p.md")]) == 0
    assert capsys.readouterr().out == "<name> of <client>, <email>, on 2026-01-10.\n"
    assert (root / "projects" / "p.md").read_bytes() == before


def test_cli_uses_anybrain_vault_when_set(make_vault, tmp_path, monkeypatch, capsys):
    root = make_vault({".harness/names.txt": "Jane Doe\n"})
    outside = tmp_path / "elsewhere.md"
    outside.write_text("Jane Doe\n")
    monkeypatch.setenv("ANYBRAIN_VAULT", str(root))
    assert cli.main(["anonymize", str(outside)]) == 0
    assert capsys.readouterr().out == "<name>\n"


def test_cli_missing_file(capsys, tmp_path):
    assert cli.main(["anonymize", str(tmp_path / "missing.md")]) == 2
    assert "file not found" in capsys.readouterr().err


def test_validators():
    assert a.luhn_ok("4111111111111111") and not a.luhn_ok("4111111111111112")
    assert a.iban_ok("FR76 3000 6000 0112 3456 7890 189") and not a.iban_ok("FR76 3000 6000 0112 3456 7890 188")
