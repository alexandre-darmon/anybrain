"""lint --schema: one passing and one failing case per rule (PRD 15.4, 11)."""
from __future__ import annotations

import pytest

from anybrain import lint
from conftest import EXAMPLE, page


def schema(root):
    return [f.message for f in lint.check_schema(lint.Context(root))]


def test_example_vault_is_clean():
    assert schema(EXAMPLE) == []


BOOK = dict(title="B", type="book", author="A. Author", status="read", rating=4, date_read="2024-03")


@pytest.mark.parametrize("override, expected", [
    ({"author": None}, "missing `author`"),
    ({"status": "finished"}, "`status` is 'finished'"),
    ({"rating": 6}, "`rating` is 6"),
    ({"status": "to-read", "rating": 3}, "`rating` is set but `status` is not read or abandoned"),
    ({"date_read": "2024-03-01"}, "`date_read` is"),
    ({"description": "x" * 151}, "`description` is 151 characters"),
    ({"language": "de"}, "`language` is 'de'"),
    ({"sensitivity": "secret"}, "`sensitivity` is 'secret'"),
    ({"created": "2026-02-30"}, "`created` is not a valid date"),
    ({"tags": ["Category Design"]}, "tag 'Category Design' is not kebab-case"),
    ({"type": "article"}, "`type` is 'article'"),
    ({"title": None}, "missing `title`"),
])
def test_book_rules(make_vault, override, expected):
    fields = dict(BOOK)
    fields.update(override)
    if fields.get("tags") == ["Category Design"]:
        fields["tags"] = '["Category Design"]'
    root = make_vault({"library/a-book.md": page(**fields)})
    found = schema(root)
    assert any(expected in message for message in found), found


def test_valid_book_passes(make_vault):
    root = make_vault({"library/a-book.md": page(**BOOK)})
    assert schema(root) == []


def test_missing_list_fields_are_reported(make_vault):
    text = page(**BOOK).replace("tags: []\n", "").replace("sources: []\n", "")
    root = make_vault({"library/a-book.md": text})
    found = schema(root)
    assert any("missing `tags`" in m for m in found)
    assert any("missing `sources`" in m for m in found)


def test_concept_needs_valid_confidence(make_vault):
    root = make_vault({
        "wiki/concepts/ok.md": page("Ok", "concept", confidence="low"),
        "wiki/concepts/bad.md": page("Bad", "concept", confidence="certain"),
        "wiki/concepts/none.md": page("None", "concept"),
    })
    found = schema(root)
    assert found == [
        "wiki/concepts/bad.md: `confidence` is 'certain', expected one of low, medium, high",
        "wiki/concepts/none.md: missing `confidence`",
    ]


def test_project_needs_status_kind_and_next_review(make_vault):
    root = make_vault({
        "projects/ok.md": page("Ok", "project", status="active", kind="personal", next_review="2026-12-01"),
        "projects/bad.md": page("Bad", "project", status="busy", kind="client", next_review="soon"),
    })
    found = schema(root)
    assert found == [
        "projects/bad.md: `status` is 'busy', expected one of idea, active, paused, shipped, dropped",
        "projects/bad.md: `kind` is 'client', expected one of portfolio, mission, venture, personal",
        "projects/bad.md: `next_review` is not a valid date (YYYY-MM-DD)",
    ]


def test_filename_must_be_kebab_case(make_vault):
    root = make_vault({"wiki/notes/My Note.md": page("My note")})
    assert schema(root) == ["wiki/notes/My Note.md: filename is not ASCII kebab-case: My Note.md"]


def test_page_without_frontmatter(make_vault):
    root = make_vault({"wiki/notes/bare.md": "# Bare\n"})
    assert schema(root) == ["wiki/notes/bare.md: no frontmatter"]


def test_cli_exit_codes(make_vault, capsys):
    from anybrain import cli

    good = make_vault({"library/a-book.md": page(**BOOK)}, name="good")
    bad = make_vault({"library/a-book.md": page(**dict(BOOK, status="nope"))}, name="bad")
    assert cli.main(["lint", str(good), "--schema"]) == 0
    assert cli.main(["lint", str(bad), "--schema"]) == 1
    assert cli.main(["lint", str(bad), "--schema", "--report-only"]) == 0
    assert "error   schema: library/a-book.md" in capsys.readouterr().out
