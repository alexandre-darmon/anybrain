"""Protected and append-only sections (PRD 12, acceptance of 12.3)."""
from __future__ import annotations

import json

import pytest

from anybrain import cli, protect


@pytest.fixture
def vault(fixture_vault):
    root = fixture_vault("protected-edit")
    problems, written = protect.check(root)  # first run records, no finding
    assert problems == [] and written
    return root


def edit(root, rel, old, new):
    path = root / rel
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def keys(problems):
    return [(p.kind, p.key) for p in problems]


# ---------------------------------------------------------------- the four cases of 12.3

def test_agent_edit_inside_my_take_is_a_finding(vault):
    edit(vault, "library/book.md", "The owner wrote this.", "The agent rewrote this.")
    problems, _ = protect.check(vault)
    assert keys(problems) == [("protected", "library/book.md#My take")]
    assert problems[0].message() == "library/book.md#My take changed"


def test_owner_accept_clears_the_finding(vault):
    edit(vault, "library/book.md", "The owner wrote this.", "The owner changed their mind.")
    assert protect.check(vault)[0]
    protect.accept(vault, ["library/book.md"])
    assert protect.check(vault)[0] == []


def test_removed_timeline_line_is_a_finding(vault):
    edit(vault, "wiki/concepts/idea.md", "- 2026-01-01 · first event\n", "")
    problems, _ = protect.check(vault)
    assert keys(problems) == [("append-only", "wiki/concepts/idea.md#Timeline")]
    assert problems[0].message() == "wiki/concepts/idea.md#Timeline line removed"


def test_added_timeline_line_is_not_a_finding(vault):
    edit(vault, "wiki/concepts/idea.md", "- 2026-02-01", "- 2026-03-01 · third event\n- 2026-02-01")
    assert protect.check(vault)[0] == []


# ---------------------------------------------------------------- other protected content

@pytest.mark.parametrize("rel, old, new, key", [
    ("projects/proj.md", "Owner reason.", "Agent reason.", "projects/proj.md#Why"),
    ("wiki/concepts/idea.md", "Owner position.", "Agent position.", "wiki/concepts/idea.md#My position"),
    ("me/preferences.md", "- Be short.", "- Be long.", "me/preferences.md#Owner rules"),
    ("me/aspirations.md", "Ship one thing.", "Ship ten things.", "me/aspirations.md"),
    ("wiki/notes/custom.md", "Owner verdict.", "Agent verdict.", "wiki/notes/custom.md#Verdict"),
])
def test_every_kind_of_protected_content(vault, rel, old, new, key):
    edit(vault, rel, old, new)
    assert keys(protect.check(vault)[0]) == [("protected", key)]


@pytest.mark.parametrize("rel, old, key", [
    ("projects/proj.md", "- 2026-01-02 · pause · no time · [hunch]\n", "projects/proj.md#Decisions"),
    ("me/preferences.md", "- Rule one. (topic:one · learned 2026-01-01)\n", "me/preferences.md#Learned rules"),
    ("log.md", "2026-01-01 · init · created vault\n", "log.md"),
])
def test_every_kind_of_append_only_content(vault, rel, old, key):
    edit(vault, rel, old, "")
    assert keys(protect.check(vault)[0]) == [("append-only", key)]


def test_changed_append_only_line_counts_as_removed(vault):
    edit(vault, "log.md", "created vault", "created the vault")
    assert keys(protect.check(vault)[0]) == [("append-only", "log.md")]


def test_agent_note_below_a_protected_section_is_allowed(vault):
    edit(vault, "library/book.md", "The owner wrote this.\n",
         "The owner wrote this.\n\n> Agent note 2026-11-16: a second edition exists.\n")
    assert protect.check(vault)[0] == []


def test_agent_note_inside_the_owner_text_is_not_allowed(vault):
    edit(vault, "library/book.md", "The owner wrote this.\n",
         "> Agent note 2026-11-16: inserted before.\n\nThe owner wrote this.\n")
    assert keys(protect.check(vault)[0]) == [("protected", "library/book.md#My take")]


def test_guidance_comment_removal_is_not_a_removed_line(vault):
    edit(vault, "wiki/concepts/idea.md", "<!-- append-only, newest first -->\n", "")
    assert protect.check(vault)[0] == []


def test_whitespace_only_changes_are_ignored(vault):
    edit(vault, "library/book.md", "The owner wrote this.", "The owner wrote this.   ")
    assert protect.check(vault)[0] == []


def test_removed_section_and_deleted_file_are_findings(vault):
    edit(vault, "wiki/concepts/idea.md", "## My position\nOwner position.\n\n", "")
    (vault / "me" / "aspirations.md").unlink()
    found = {p.key: p.message() for p in protect.check(vault)[0]}
    assert found["wiki/concepts/idea.md#My position"].endswith("changed (section missing)")
    assert found["me/aspirations.md"].endswith("changed (file missing)")


def test_new_page_is_recorded_without_finding_then_protected(vault):
    (vault / "library" / "new.md").write_text((vault / "library" / "book.md").read_text().replace("Book", "New"))
    assert protect.check(vault)[0] == []
    edit(vault, "library/new.md", "The owner wrote this.", "Changed.")
    assert keys(protect.check(vault)[0]) == [("protected", "library/new.md#My take")]


def test_a_page_with_a_finding_keeps_its_old_record(vault):
    edit(vault, "library/book.md", "The owner wrote this.", "First agent edit.")
    protect.check(vault)
    edit(vault, "library/book.md", "First agent edit.", "Second agent edit.")
    assert keys(protect.check(vault)[0]) == [("protected", "library/book.md#My take")]


def test_store_format(vault):
    data = json.loads((vault / ".harness" / "protected.json").read_text())
    assert data["version"] == 1
    assert len(data["protected"]["library/book.md#My take"]) == 64
    assert isinstance(data["append_only"]["log.md"], list)


def test_lint_cli_protected_and_accept(vault, capsys):
    edit(vault, "library/book.md", "The owner wrote this.", "The agent rewrote this.")
    assert cli.main(["lint", str(vault), "--protected"]) == 1
    out = capsys.readouterr().out
    assert "error   protected: library/book.md#My take changed" in out
    assert "--accept library/book.md" in out
    assert cli.main(["lint", str(vault), "--accept", "library/book.md"]) == 0
    assert cli.main(["lint", str(vault), "--protected"]) == 0
