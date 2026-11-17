"""Regressions for the findings confirmed by the adversarial review of steps 5 to 7 (docs/decisions.md D026).

PII-looking test data is built at run time so the repo hygiene test stays meaningful (D006).
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from anybrain import anonymize as a
from anybrain import cli, feedback, install, lint, protect
from conftest import page

EMAIL_ACCENT = "andré.dupont" + "@" + "example.fr"
PHONE = "06 12 " + "34 56 78"


# ---------------------------------------------------------------- lint --graph: links into company/ and finance/

def test_links_to_confidential_files_and_unreadable_confidential_pages_are_leaks(make_vault):
    root = make_vault({
        "finance/holdings.csv": "date,ticker\n",
        "company/acme-deal.md": "no frontmatter\n",
        "company/strategy.md": page("Strategy", "note"),
        "wiki/notes/thoughts.md": page("Thoughts", body=(
            "Mine: [holdings](../../finance/holdings.csv)\n"
            "Again [[holdings.csv]] and [[acme-deal]] and [[strategy]].\n")),
    })
    found = [str(f) for f in lint.check_graph(lint.Context(root)) if "confidential" in str(f)]
    assert any("links to confidential file finance/holdings.csv" in f for f in found)
    assert any("links to confidential page company/acme-deal.md" in f for f in found)
    assert any("links to confidential page company/strategy.md" in f for f in found)
    assert len(found) == 3  # one finding per target, not per link


# ---------------------------------------------------------------- anonymize: dates, amounts, phones, IBANs...

@pytest.mark.parametrize("text, expected", [
    ("Signed on 2026-01-10, 40 k€ budget.", "Signed on 2026-01-10, <amount> budget."),
    ("Paid 2026-01-10 12 000 € to the vendor.", "Paid 2026-01-10 <amount> to the vendor."),
    ("Invoice €1,200 2026-01-10", "Invoice <amount> 2026-01-10"),
    ("On 10/01/2026, 1 500 € was paid.", "On 10/01/2026, <amount> was paid."),
    ("Due 2026-01-10 $500", "Due 2026-01-10 <amount>"),
    ("CA de 2,5 M€ et 3 Md€", "CA de <amount> et <amount>"),
    ("2 millions d'euros, 800 euros, 55 000 euros bruts", "<amount>, <amount>, <amount> bruts"),
    ("EUR 1 500, USD 3,000, CHF 1200, 15 kEUR, 1.2 MEUR, ¥5000", "<amount>, <amount>, <amount>, <amount>, <amount>, <amount>"),
    (f"Appelez-moi au {PHONE}.", "Appelez-moi au <phone>."),
    ("Le standard est le 01 23 " + "45 67 89. Merci.", "Le standard est le <phone>. Merci."),
    ("Call 0033 6 12 " + "34 56 78 or 0044 20 7946 " + "0958", "Call <phone> or <phone>"),
    ("Call 06 12 34 " + "56 78", "Call <phone>"),
    ("fr76 3000 6000 0112 3456 7890 189 then", "<iban> then"),
    ("FR76-3000-6000-0112-3456-7890-189", "<iban>"),
    ("FR76 30006 00001 12345678901 89 RIB", "<iban> RIB"),
    ("card 4111.1111.1111.1111 ok", "card <card> ok"),
    (f"mail {EMAIL_ACCENT}", "mail <email>"),
    ("key sk-or-v1-" + "aaaaBBBBccccDDDD1234 and AKIA" + "ABCDEFGHIJKLMNOP", "key <secret> and <secret>"),
])
def test_anonymize_formats(text, expected):
    assert a.mask(text) == expected


@pytest.mark.parametrize("text", [
    "2026-01-10", "10/01/2026", "Le 1.2.2026 et la version 1.2.3", "In 2026, 444 bestsellers.",
])
def test_anonymize_keeps_dates_and_plain_numbers(text):
    assert a.mask(text) == text


def test_anonymize_is_fast_on_long_lines_of_numbers():
    line = ", ".join(f"{i} {i * 7}" for i in range(4000))  # about 55 KB, no currency
    start = time.perf_counter()
    a.mask(line)
    assert time.perf_counter() - start < 2


def test_names_are_masked_in_slugs_links_tags_and_across_lines():
    text = "tags: [acme-bank]\nsources: [raw/inbox/acme_bank_q1.pdf]\nCall Jane\nDoe; see [[jane-doe-call-notes]] and AcmeBank."
    out = a.mask(text, names=["Jane Doe"], clients=["Acme Bank"])
    assert "acme" not in out.lower() and "jane" not in out.lower()
    assert a.mask("Janet and Jane Does", names=["Jane Doe"]) == "Janet and Jane Does"


@pytest.mark.parametrize("rel, content", [
    ("company/deal.md", page("Deal", "note", body="Cut 400 jobs.")),
    ("finance/holdings.csv", "AAPL,120\n"),
    ("me/profile.md", page("Profile", "profile", body="Private.")),
    ("journal/2026-11-10.md", page("Retro", "brief", body="Private.")),
    (".harness/.env", "FRONTIER_API_KEY=x\n"),
    ("raw/docs/report.md", "Private report.\n"),
    ("projects/audit.md", page("Audit", "project", kind="mission", body="Sell the branch.")),
])
def test_anonymize_refuses_local_only_files_outside_the_local_profile(make_vault, monkeypatch, capsys, rel, content):
    root = make_vault({rel: content})
    assert cli.main(["anonymize", str(root / rel)]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "local profile only" in captured.err
    monkeypatch.setenv("ANYBRAIN_PROFILE", "local")
    assert cli.main(["anonymize", str(root / rel)]) == 0


# ---------------------------------------------------------------- lint --report

def test_report_twice_gives_the_same_findings(make_vault, capsys):
    root = make_vault({"library/b.md": page("B", "book", author="A", status="read",
                                            body="See [[missing-page]].\n- [[other]]\n")})
    args = ["lint", str(root), "--report-only", "--report", "journal/lint-2026-11-16.md"]
    cli.main(args)
    first = capsys.readouterr().out.splitlines()[-1]
    cli.main(args)
    assert capsys.readouterr().out.splitlines()[-1] == first
    report = (root / "journal" / "lint-2026-11-16.md").read_text()
    assert "```text" in report and "[[missing-page]]" in report


def test_report_never_overwrites_a_page_that_is_not_a_report(make_vault, capsys):
    root = make_vault({"me/profile.md": page("Profile", "profile", body="Mine.")})
    before = (root / "me" / "profile.md").read_text()
    assert cli.main(["lint", str(root), "--report", "me/profile.md"]) == 2
    assert "refusing to overwrite me/profile.md" in capsys.readouterr().err
    assert (root / "me" / "profile.md").read_text() == before


# ---------------------------------------------------------------- lint --schema and --quotes

@pytest.mark.parametrize("value", ["2026", "true", "2026-11-16", "Product, Strategy"])
def test_scalar_tags_are_a_finding_not_a_crash(make_vault, value):
    root = make_vault({"wiki/notes/a.md": page("A").replace("tags: []", f"tags: {value}")})
    found = [str(f) for f in lint.check_schema(lint.Context(root))]
    assert found == ["error   schema: wiki/notes/a.md: `tags` must be a list"]


def test_quotes_are_not_compared_with_local_only_sources_on_frontier(make_vault, monkeypatch):
    root = make_vault({
        "company/deal.md": page("Deal", body="Acme will cut 400 jobs."),
        "library/probe.md": page("Probe", "book", author="A", status="read", sources=["company/deal.md"],
                                 body='## Quotes\n- "Acme will cut 300 jobs" (loc. 1)\n'),
    })
    found = [str(f) for f in lint.check_quotes(lint.Context(root))]
    assert found == ["warning quotes: library/probe.md: quotes not checked, their sources are local-only; "
                     "run lint with ANYBRAIN_PROFILE=local"]
    monkeypatch.setenv("ANYBRAIN_PROFILE", "local")
    assert "quote not found" in str(lint.check_quotes(lint.Context(root))[0])


def test_findings_about_a_moved_local_only_file_stay_masked(make_vault, capsys):
    root = make_vault({"company/board.md": page("Board", protected='["Sale of the Lyon branch"]',
                                                body="## Sale of the Lyon branch\nSecret.\n")})
    protect.check(root)
    (root / "company" / "board.md").rename(root / "company" / "board-2026.md")
    cli.main(["lint", str(root), "--protected"])
    out = capsys.readouterr().out
    assert "Lyon" not in out and "company/board.md: details hidden" in out


# ---------------------------------------------------------------- --accept

def test_accept_canonicalises_the_path(fixture_vault):
    root = fixture_vault("protected-edit")
    protect.check(root)
    path = root / "library" / "book.md"
    path.write_text(path.read_text().replace("The owner wrote this.", "Changed by the owner."))
    assert protect.check(root)[0]
    assert protect.accept(root, ["library/../library//book.md"]) == ["library/book.md"]
    assert protect.check(root)[0] == []
    assert protect.accept(root, [str(path)]) == ["library/book.md"]


def test_accept_rejects_paths_outside_the_vault_and_unknown_files(fixture_vault, capsys):
    root = fixture_vault("protected-edit")
    assert cli.main(["lint", str(root), "--accept", "/etc/hostname"]) == 2
    assert "not inside the vault" in capsys.readouterr().err
    assert cli.main(["lint", str(root), "--accept", "library/nope.md"]) == 2
    assert "nothing to accept" in capsys.readouterr().err


def test_a_hash_in_a_filename_does_not_break_protection(make_vault):
    root = make_vault({
        "projects/c#-rewrite.md": page("C sharp", "project", body="## Why\nOwner reason.\n"),
        "projects/alpha.md": page("Alpha", "project", body="## Why\nOwner reason.\n"),
    })
    protect.check(root)
    for rel in ("projects/c#-rewrite.md", "projects/alpha.md"):
        path = root / rel
        path.write_text(path.read_text().replace("Owner reason.", "Agent reason."))
    for _ in range(3):  # reported every time, never silently re-recorded
        assert sorted(p.key for p in protect.check(root)[0]) == ["projects/alpha.md#Why", "projects/c#-rewrite.md#Why"]


# ---------------------------------------------------------------- init, update and index never overwrite owner files

def test_init_keeps_a_handwritten_index(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "brain"
    (root / "journal").mkdir(parents=True)
    (root / "index.md").write_text("# My home\nOwner map, written by hand.\n")
    (root / "journal" / "index.md").write_text("# My journal hub\n")
    assert install.init(str(root)) == 1
    assert (root / "index.md").read_text() == "# My home\nOwner map, written by hand.\n"
    assert (root / "journal" / "index.md").read_text() == "# My journal hub\n"
    assert "kept index.md: it was not generated by anybrain" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["init", "update"])
def test_init_and_update_refuse_while_files_are_evicted(tmp_path, monkeypatch, capsys, command):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "brain"
    assert install.init(str(root)) == 0
    (root / "log.md").rename(tmp_path / "log-away.md")
    (root / ".log.md.icloud").write_text("")
    assert cli.main([command, str(root)]) == 2
    assert "evicted by iCloud" in capsys.readouterr().err
    assert not (root / "log.md").exists()


# ---------------------------------------------------------------- feedback add appends

def test_feedback_add_appends_without_rewriting(make_vault):
    root = make_vault({".harness/feedback.log": "2026-11-01 · topic:keep-me · skill:ask · owner line"})
    errors = []

    def add(i):
        try:
            feedback.add(root, "topic-k", "ask", f"note {i}")
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=add, args=(i,)) for i in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    lines = (root / ".harness" / "feedback.log").read_text().splitlines()
    assert errors == []
    assert lines[0].endswith("owner line")
    assert len(lines) == 21
