"""lint: one passing and one failing fixture per check (PRD 13.6, 15.4, acceptance of step 5)."""
from __future__ import annotations

import pytest

from anybrain import cli, lint
from conftest import EXAMPLE, page


def findings(root, check):
    return [str(f) for f in lint.CHECKS[check](lint.Context(root))]


def has(found, *parts):
    return any(all(p in f for p in parts) for f in found)


# ---------------------------------------------------------------- whole-vault results

def test_example_vault_is_clean_on_every_check(example_vault, capsys):
    assert cli.main(["lint", str(example_vault)]) == 0
    assert "lint: 0 error(s), 0 warning(s)" in capsys.readouterr().out


def test_clean_fixture_is_clean_on_every_check(fixture_vault, capsys):
    assert cli.main(["lint", str(fixture_vault("clean"))]) == 0
    assert "lint: 0 error(s), 0 warning(s)" in capsys.readouterr().out


@pytest.mark.parametrize("check", sorted(lint.CHECKS))
def test_each_check_passes_on_the_clean_fixture(fixture_vault, check):
    assert findings(fixture_vault("clean"), check) == []


# ---------------------------------------------------------------- --links

def test_links_fail(fixture_vault):
    found = findings(fixture_vault("broken-links"), "links")
    assert has(found, "error", "library/a-book.md:20", "broken link [[missing-page]]")
    assert has(found, "broken link [a page](../wiki/nowhere.md)")


# ---------------------------------------------------------------- --graph (each row of 13.6)

@pytest.fixture
def broken(fixture_vault):
    return findings(fixture_vault("broken-links"), "graph")


def test_graph_broken_link(broken):
    assert has(broken, "error", "links:", "[[missing-page]]")


def test_graph_confidential_leak(broken):
    assert has(broken, "error", "library/a-book.md:22: public page links to confidential page company/secret-plan.md")


def test_graph_orphan_is_a_warning_for_seven_days_then_an_error(broken):
    assert has(broken, "error", "library/orphan-book.md: orphan, no page in library/, wiki/ or projects/ links to it")
    assert has(broken, "warning", "library/new-book.md: orphan")
    assert has(broken, "warning", "(an error from ")


def test_orphan_candidates_come_from_facts(make_vault, monkeypatch):
    monkeypatch.setenv("ANYBRAIN_TODAY", "2026-11-16")
    old = {"created": "2026-01-01"}
    root = make_vault({
        "library/deep-work.md": page("Deep Work", "book", author="Cal Newport", tags=["livre", "focus"], **old),
        "library/slow-productivity.md": page("Slow Productivity", "book", author="Cal Newport", tags=["livre"], **old),
        "library/mystery.md": page("Mystery", "book", author="Unknown", tags=["livre"], **old),
        "library/other.md": page("Other", "book", author="unknown", tags=["livre"], **old),
        "wiki/notes/focus-blocks.md": page("Focus blocks", "note", tags=["focus"], **old,
                                           body="Same habit as in [[slow-productivity]]."),
        "wiki/notes/plugins.md": page("Plugins", "note", tags=["tools"], **old,
                                      body="Highlights of [[other]] come through Readwise."),
        "finance/plan.md": page("Plan", "note", tags=["focus"], **old),
    })
    found = [str(f) for f in lint.check_graph(lint.Context(root))]

    def line(rel):
        return next(f for f in found if f"{rel}: orphan" in f)

    deep = line("library/deep-work.md")
    assert deep.startswith("error") and "same author: library/slow-productivity.md" in deep
    assert "shares the tag focus with wiki/notes/focus-blocks.md" in deep and "finance/plan.md" not in deep
    assert "livre" not in deep, "a tag on most books is generic"
    assert "it links to library/other.md (state the relation back there if it is real)" in line("wiki/notes/plugins.md")
    assert "same author" not in line("library/mystery.md"), "a placeholder author is no relation"
    assert "no candidate found: ask the owner" in line("library/mystery.md")


def test_graph_naked_link(broken):
    assert has(broken, "warning", "library/a-book.md:21: naked link")
    assert not has(broken, "library/a-book.md:20: naked link")


def test_graph_concept_with_one_source_after_60_days(broken, make_vault):
    assert has(broken, "warning", "wiki/concepts/lonely.md: concept with 1 source")
    young = make_vault({"wiki/concepts/young.md": page("Young", "concept", confidence="low", created="2026-10-01",
                                                       sources=["library/a.md"])})
    assert not has(findings(young, "graph"), "concept with 1 source")


def test_graph_duplicate_filename_and_near_duplicate_concepts(fixture_vault):
    found = findings(fixture_vault("duplicates"), "graph")
    assert has(found, "error", "duplicate filename same.md: library/same.md, wiki/notes/same.md")
    assert has(found, "warning", "near-duplicate concepts: wiki/concepts/customer-interview.md and "
                                 "wiki/concepts/customer-interviews.md")


def test_graph_over_linked(make_vault):
    files = {f"wiki/notes/n{i:02d}.md": page(f"N{i}", body="Back to [[hub]].") for i in range(21)}
    files["wiki/notes/hub.md"] = page("Hub", body="\n".join(f"- related because of x: [[n{i:02d}]]" for i in range(21)))
    found = findings(make_vault(files), "graph")
    assert has(found, "warning", "wiki/notes/hub.md: 21 outgoing links (max 20)")
    files.pop("wiki/notes/n20.md")
    files["wiki/notes/hub.md"] = page("Hub", body="\n".join(f"- related because of x: [[n{i:02d}]]" for i in range(20)))
    assert not has(findings(make_vault(files, name="ok"), "graph"), "outgoing links")


def test_graph_passes_when_links_are_sound(fixture_vault):
    assert findings(fixture_vault("clean"), "graph") == []


# ---------------------------------------------------------------- --stale

def test_stale(make_vault):
    root = make_vault({
        "projects/late.md": page("Late", "project", status="active", kind="personal", next_review="2026-11-01"),
        "projects/paused.md": page("Paused", "project", status="paused", kind="personal", next_review="2026-01-01"),
        "projects/lag.md": page("Lag", "project", status="active", kind="personal", next_review="2026-12-31",
                                updated="2026-10-01", body="## Now\nOld state.\n\n## Timeline\n- 2026-10-20 · shipped · [doc]\n"),
        "projects/fresh.md": page("Fresh", "project", status="active", kind="personal", next_review="2026-12-31",
                                  updated="2026-10-01", body="## Now\nAs of 2026-10-19, shipping.\n\n## Timeline\n- 2026-10-20 · shipped\n"),
        "library/slow.md": page("Slow", "book", author="A", status="reading", updated="2026-08-01"),
        "library/recent.md": page("Recent", "book", author="A", status="reading", updated="2026-10-01"),
    })
    found = findings(root, "stale")
    assert has(found, "warning", "projects/late.md: active project past next_review 2026-11-01")
    assert has(found, "projects/lag.md: Now is older than the newest Timeline entry (2026-10-20) by 19 days")
    assert has(found, "library/slow.md: book `reading` for 107 days")
    assert len(found) == 3


# ---------------------------------------------------------------- --quotes

def test_quotes(make_vault):
    root = make_vault({
        "raw/highlights/b.md": "Highlight (loc. 1): It’s  a “named” idea\nthat travels.\n",
        "library/good.md": page("Good", "book", author="A", status="read", sources=["raw/highlights/b.md"],
                                body='## Quotes\n- "It\'s a "named" idea that travels." (loc. 1)\n'),
        "library/bad.md": page("Bad", "book", author="A", status="read", sources=["raw/highlights/b.md"],
                               body='## Quotes\n- “An invented quote.” (loc. 9)\n- "It\'s a" (loc. 1)\n'),
        "library/nosource.md": page("None", "book", author="A", status="read",
                                    body='## Quotes\n- "Anything at all." (loc. 2)\n'),
    })
    found = findings(root, "quotes")
    assert has(found, "error", "library/bad.md:16: quote not found in its sources", "An invented quote.")
    assert has(found, "library/nosource.md:16: quote no readable file in `sources`")
    assert not has(found, "library/good.md")
    assert len(found) == 2


# ---------------------------------------------------------------- --secrets

def test_secrets_fail_without_printing_values(fixture_vault):
    found = findings(fixture_vault("secrets"), "secrets")
    for label in ("email address", "phone number", "IBAN", "card number"):
        assert has(found, "wiki/notes/leaky.md", label)
    assert has(found, "wiki/notes/leaky.md:20: client name from .harness/clients.txt")
    assert has(found, "raw/inbox/acme-bank-brief.md: client name from .harness/clients.txt in a raw/ filename")
    assert not has(found, "jane.doe") and not has(found, "4111")
    assert not has(found, "company/bank.md")
    assert not has(found, "projects/mission-one.md")
    assert not has(found, "wiki/notes/fine.md")


@pytest.mark.parametrize("key, label", [
    ("sk-" + "a1B2" * 6, "API key (sk-)"),
    ("AKIA" + "ABCDEFGH12345678", "API key (AKIA)"),
    ("ghp_" + "x" * 36, "API key (ghp_)"),
    ("xox" + "b-" + "1234567890-abcdef", "API key (xox)"),
])
def test_api_keys_are_found_everywhere_even_in_raw_and_finance(make_vault, key, label):
    root = make_vault({
        "wiki/notes/a.md": page("A", body=f"token {key}"),
        "finance/b.md": page("B", body=f"token {key}"),
        "raw/inbox/c.md": f"token {key}\n",
    })
    found = findings(root, "secrets")
    for rel in ("wiki/notes/a.md", "finance/b.md", "raw/inbox/c.md"):
        assert has(found, rel, label)


def test_personal_data_is_allowed_in_company_finance_and_raw(make_vault):
    email = "someone" + "@" + "example.org"
    root = make_vault({"company/a.md": page("A", body=email), "raw/inbox/b.md": email, "finance/c.csv": email})
    assert findings(root, "secrets") == []


# ---------------------------------------------------------------- --feedback

def test_feedback(fixture_vault):
    found = findings(fixture_vault("feedback"), "feedback")
    assert has(found, "warning", "topic:short-summaries has 3 corrections in 90 days and no rule")
    assert has(found, "rule topic:no-em-dash not followed", "2026-11-15", "consider rewording it")
    assert not has(found, "old-topic")
    assert len(found) == 2


def test_feedback_malformed_line(make_vault):
    root = make_vault({".harness/feedback.log": "not a correction\n"})
    assert findings(root, "feedback") == ["warning feedback: .harness/feedback.log:1: line does not follow the log format"]


# ---------------------------------------------------------------- --icloud

def test_icloud(make_vault):
    root = make_vault({"library/.book.md.icloud": "", "library/book.md": page("Book", "book", author="A", status="read")})
    found = findings(root, "icloud")
    assert found == ["error   icloud: library/.book.md.icloud: iCloud placeholder, the file is not downloaded. "
                     "Right-click the vault folder in Finder and choose Keep Downloaded."]


# ---------------------------------------------------------------- runner: flags, report, masking

def test_flags_select_checks(fixture_vault, capsys):
    root = fixture_vault("broken-links")
    assert cli.main(["lint", str(root), "--schema"]) == 0
    assert cli.main(["lint", str(root), "--links"]) == 1
    assert cli.main(["lint", str(root), "--links", "--report-only"]) == 0


def test_findings_are_not_repeated_when_links_and_graph_both_run(fixture_vault, capsys):
    cli.main(["lint", str(fixture_vault("broken-links")), "--links", "--graph"])
    out = capsys.readouterr().out
    assert out.count("broken link [[missing-page]]") == 1


def test_report_file_is_a_valid_report_page(fixture_vault, capsys):
    root = fixture_vault("broken-links")
    assert cli.main(["lint", str(root), "--graph", "--report-only", "--report", "journal/lint-2026-11-16.md"]) == 0
    assert "wrote journal/lint-2026-11-16.md" in capsys.readouterr().out
    text = (root / "journal" / "lint-2026-11-16.md").read_text()
    assert "type: report" in text and "## Errors" in text and "## Warnings" in text
    assert findings(root, "schema") == []


def test_confidential_findings_are_masked_unless_local(fixture_vault, capsys, monkeypatch):
    root = fixture_vault("broken-links")
    (root / "company" / "secret-plan.md").write_text(
        (root / "company" / "secret-plan.md").read_text() + "\nSee [[project-falcon-acquisition]].\n")
    cli.main(["lint", str(root), "--links"])
    out = capsys.readouterr().out
    assert "project-falcon-acquisition" not in out
    assert "company/secret-plan.md: details hidden (confidential: local profile only)" in out
    monkeypatch.setenv("ANYBRAIN_PROFILE", "local")
    cli.main(["lint", str(root), "--links"])
    assert "project-falcon-acquisition" in capsys.readouterr().out


def test_lint_reads_vault_from_environment(monkeypatch, capsys):
    monkeypatch.setenv("ANYBRAIN_VAULT", str(EXAMPLE))
    assert cli.main(["lint", "--schema"]) == 0


# ---------------------------------------------------------------- Q22: the destination of a report decides its masking

@pytest.fixture
def leaky_company(make_vault):
    return make_vault({"company/plan.md": page("Plan", body="See [[project-falcon-acquisition]].")})


def test_local_report_in_a_frontier_folder_is_masked(leaky_company, monkeypatch, capsys):
    monkeypatch.setenv("ANYBRAIN_PROFILE", "local")
    cli.main(["lint", str(leaky_company), "--links", "--report-only", "--report", "journal/reports/lint-2026-11-16.md"])
    assert "project-falcon-acquisition" in capsys.readouterr().out  # the local session itself sees everything
    report = (leaky_company / "journal" / "reports" / "lint-2026-11-16.md").read_text()
    assert "project-falcon-acquisition" not in report
    assert "company/plan.md: details hidden (confidential: local profile only)" in report


def test_local_report_in_a_local_folder_is_complete(leaky_company, monkeypatch):
    monkeypatch.setenv("ANYBRAIN_PROFILE", "local")
    cli.main(["lint", str(leaky_company), "--links", "--report-only", "--report", "journal/lint-2026-11-16.md"])
    assert "project-falcon-acquisition" in (leaky_company / "journal" / "lint-2026-11-16.md").read_text()


@pytest.mark.parametrize("link, target", [("journal/reports", "journal/out"), ("wiki", "journal/out")])
def test_a_symlinked_destination_is_judged_where_it_lands(leaky_company, monkeypatch, link, target):
    monkeypatch.setenv("ANYBRAIN_PROFILE", "local")
    (leaky_company / link).mkdir(parents=True, exist_ok=True)
    (leaky_company / "journal").mkdir(exist_ok=True)
    (leaky_company / target).symlink_to(leaky_company / link)
    cli.main(["lint", str(leaky_company), "--links", "--report-only", "--report", f"{target}/lint-x.md"])
    report = (leaky_company / link / "lint-x.md").read_text()
    assert "project-falcon-acquisition" not in report and "details hidden" in report


def test_a_symlinked_report_file_is_masked(leaky_company, monkeypatch):
    monkeypatch.setenv("ANYBRAIN_PROFILE", "local")
    (leaky_company / "journal").mkdir(exist_ok=True)
    (leaky_company / "wiki").mkdir(exist_ok=True)
    (leaky_company / "wiki" / "lint-x.md").write_text("---\ntitle: x\ntype: report\n---\n")
    (leaky_company / "journal" / "lint-x.md").symlink_to(leaky_company / "wiki" / "lint-x.md")
    cli.main(["lint", str(leaky_company), "--links", "--report-only", "--report", "journal/lint-x.md"])
    assert "project-falcon-acquisition" not in (leaky_company / "wiki" / "lint-x.md").read_text()


def test_frontier_report_is_masked_wherever_it_goes(leaky_company):
    cli.main(["lint", str(leaky_company), "--links", "--report-only", "--report", "journal/reports/lint-2026-11-16.md"])
    assert "project-falcon-acquisition" not in (leaky_company / "journal" / "reports" / "lint-2026-11-16.md").read_text()
