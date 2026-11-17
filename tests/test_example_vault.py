"""vault.example/ follows the page contracts (PRD 24 step 3) and holds only fake data."""
from __future__ import annotations

from anybrain import vault as v
from conftest import EXAMPLE


def pages_by_type():
    found: dict[str, list[v.Page]] = {}
    for page in v.load_pages(EXAMPLE):
        found.setdefault(page.type, []).append(page)
    return found


def titles(page: v.Page) -> list[str]:
    return [s.title for s in page.sections() if s.level == 2]


def test_every_p0_page_type_is_represented():
    assert {"book", "concept", "note", "project", "profile", "brief"} <= set(pages_by_type())


def test_concepts_have_draft_position_and_timeline():
    for page in pages_by_type()["concept"]:
        assert "Draft position" in titles(page), page.rel
        assert "Timeline" in titles(page), page.rel
        assert len(page.meta["sources"]) >= 2, page.rel


def test_projects_have_now_and_timeline():
    for page in pages_by_type()["project"]:
        assert titles(page) == ["Why", "Now", "Timeline", "Decisions", "Next steps"], page.rel


def test_books_follow_the_contract():
    for page in pages_by_type()["book"]:
        assert titles(page) == ["My take", "Key ideas", "Quotes", "Related"], page.rel


def test_preferences_have_owner_and_learned_rules():
    page = v.load_page(EXAMPLE, EXAMPLE / "me" / "preferences.md")
    assert titles(page) == ["Owner rules", "Learned rules"]
    assert "topic:no-em-dash" in page.section("Learned rules").content


def test_book_statuses_cover_grouping_cases():
    statuses = {p.meta["status"] for p in pages_by_type()["book"]}
    assert {"read", "reading", "to-read"} <= statuses
