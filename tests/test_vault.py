"""anybrain.vault: frontmatter, sections, page discovery, slugs, sensitivity (PRD 10 to 13)."""
from __future__ import annotations

import datetime as dt

import pytest

from anybrain import vault as v
from conftest import page


# ---------------------------------------------------------------- frontmatter

def test_parse_frontmatter_returns_dict_body_and_offset():
    text = "---\ntitle: X\ntype: note\n---\n\n## Summary\nhello\n"
    fm, body, offset, error = v.parse_frontmatter(text)
    assert error is None
    assert fm == {"title": "X", "type": "note"}
    assert body.startswith("\n## Summary")
    assert offset == 4  # body starts on file line 5


def test_parse_frontmatter_reads_dates_as_dates():
    fm, *_ = v.parse_frontmatter("---\ncreated: 2026-11-01\ndate_read: 2024-03\n---\n")
    assert fm["created"] == dt.date(2026, 11, 1)
    assert fm["date_read"] == "2024-03"


def test_missing_frontmatter_is_reported():
    fm, body, offset, error = v.parse_frontmatter("# Just a heading\n")
    assert fm is None and error == "no frontmatter" and offset == 0
    assert body == "# Just a heading\n"


def test_invalid_yaml_is_reported_with_a_line():
    fm, _, _, error = v.parse_frontmatter("---\ntitle: [unclosed\n---\nbody\n")
    assert fm is None
    assert error.startswith("invalid YAML frontmatter")


def test_frontmatter_that_is_not_a_mapping_is_reported():
    fm, _, _, error = v.parse_frontmatter("---\n- a\n- b\n---\n")
    assert fm is None and error == "frontmatter is not a mapping"


def test_empty_frontmatter_is_an_empty_dict():
    fm, _, _, error = v.parse_frontmatter("---\n\n---\nbody\n")
    assert fm == {} and error is None


def test_crlf_files_are_read_as_lf(tmp_path):
    path = tmp_path / "a.md"
    path.write_bytes(b"---\r\ntitle: X\r\n---\r\nbody\r\n")
    assert v.read_text(path) == "---\ntitle: X\n---\nbody\n"


# ---------------------------------------------------------------- sections

BODY = """
## My take
Owner text.

### Detail
Still inside My take.

## Key ideas
- one
```
## not a heading inside a fence
```
# Top
"""


def test_sections_run_until_same_or_higher_level():
    found = {s.title: s for s in v.sections(BODY)}
    assert "Still inside My take." in found["My take"].content
    assert "one" not in found["My take"].content
    assert found["Detail"].level == 3
    assert "not a heading" in found["Key ideas"].content
    assert "not a heading inside a fence" not in found


def test_find_section_ignores_hashes_and_case():
    assert v.find_section(BODY, "## my take").title == "My take"
    assert v.find_section(BODY, "Key ideas").heading == "## Key ideas"
    assert v.find_section(BODY, "Quotes") is None


# ---------------------------------------------------------------- pages

def test_load_pages_skips_raw_hidden_generated_and_root_files(make_vault):
    root = make_vault({
        "library/a-book.md": page("A book", "book"),
        "library/index.md": "generated",
        "raw/highlights/a-book.md": "raw text",
        ".harness/templates/book.md": "template",
        ".obsidian/notes.md": "config",
        "log.md": "2026-01-01 · init · created vault",
        "AGENTS.md": "rules",
        "index.md": "root index",
        "wiki/concepts/x.md": page("X", "concept"),
        "content/ideas.md": "append-only backlog",
    })
    rels = [p.rel for p in v.load_pages(root)]
    assert rels == ["library/a-book.md", "wiki/concepts/x.md"]


def test_page_properties(make_vault):
    root = make_vault({"wiki/concepts/category-design.md": page("Category design", "concept", "Win by creating the category.")})
    (p,) = v.load_pages(root)
    assert p.stem == "category-design"
    assert p.folder == "wiki"
    assert p.type == "concept" and p.title == "Category design"
    assert p.description == "Win by creating the category."
    assert p.sensitivity == "public"
    assert p.is_compiled
    assert p.error is None


def test_page_with_bad_frontmatter_is_loaded_with_an_error(make_vault):
    root = make_vault({"library/broken.md": "no frontmatter here\n"})
    (p,) = v.load_pages(root)
    assert p.error == "no frontmatter" and p.meta == {}
    assert p.title == "broken"


# ---------------------------------------------------------------- slugs

@pytest.mark.parametrize("title, slug", [
    ("The Snow Leopard", "snow-leopard"),
    ("création de niche", "creation-de-niche"),
    ("$100M Offers", "100m-offers"),
    ("The Art & Business of Ghostwriting", "art-and-business-of-ghostwriting"),
    ("This Book Will Make You Dangerous", "this-book-will-make-you-dangerous"),
    ("L’Art de la guerre", "art-de-la-guerre"),
    ("Les Misérables", "miserables"),
    ("A/B testing", "a-b-testing"),
    ("MOC - Création de niche", "moc-creation-de-niche"),
    ("  Pièces jointes  ", "pieces-jointes"),
])
def test_slugify(title, slug):
    assert v.slugify(title) == slug


def test_slugify_rejects_text_without_letters():
    with pytest.raises(ValueError):
        v.slugify("!!!")


@pytest.mark.parametrize("stem, ok", [
    ("snow-leopard", True), ("2026-11-03", True), ("lint-2026-11-16", True),
    ("Snow-Leopard", False), ("snow_leopard", False), ("snow--leopard", False), ("création", False),
])
def test_is_kebab(stem, ok):
    assert v.is_kebab(stem) is ok


# ---------------------------------------------------------------- sensitivity (PRD 10.3)

@pytest.mark.parametrize("rel, fm, expected", [
    ("me/profile.md", {}, "personal"),
    ("raw/highlights/x.md", {}, "personal"),
    ("library/x.md", {}, "public"),
    ("wiki/concepts/x.md", {}, "public"),
    ("projects/x.md", {"kind": "portfolio"}, "personal"),
    ("projects/x.md", {"kind": "mission"}, "confidential"),
    ("company/x.md", {}, "confidential"),
    ("finance/x.md", {}, "confidential"),
    ("journal/2026-11-03.md", {}, "personal"),
    ("content/drafts/x.md", {"channel": "linkedin"}, "public"),
    ("content/drafts/x.md", {"channel": "application"}, "personal"),
    ("content/published/x.md", {}, "public"),
])
def test_default_sensitivity(rel, fm, expected):
    assert v.default_sensitivity(rel, fm) == expected


def test_effective_sensitivity_is_the_stricter_of_declared_and_default():
    assert v.effective_sensitivity("company/x.md", {"sensitivity": "public"}) == "confidential"
    assert v.effective_sensitivity("library/x.md", {"sensitivity": "personal"}) == "personal"
    assert v.effective_sensitivity("library/x.md", {"sensitivity": "nonsense"}) == "public"
    assert v.stricter("public", "confidential") == "confidential"


# ---------------------------------------------------------------- paths and dates

def test_resolve_vault_uses_argument_then_environment(tmp_path, monkeypatch):
    assert v.resolve_vault(str(tmp_path)) == tmp_path.resolve()
    monkeypatch.setenv("ANYBRAIN_VAULT", str(tmp_path))
    assert v.resolve_vault(None) == tmp_path.resolve()
    monkeypatch.delenv("ANYBRAIN_VAULT")
    with pytest.raises(v.VaultError, match="ANYBRAIN_VAULT"):
        v.resolve_vault(None)
    with pytest.raises(v.VaultError, match="not found"):
        v.resolve_vault(str(tmp_path / "missing"))


def test_today_can_be_pinned(monkeypatch):
    monkeypatch.setenv("ANYBRAIN_TODAY", "2026-01-10")
    assert v.today() == dt.date(2026, 1, 10)


def test_as_date():
    assert v.as_date("2026-11-30") == dt.date(2026, 11, 30)
    assert v.as_date(dt.date(2026, 1, 1)) == dt.date(2026, 1, 1)
    assert v.as_date("2026-13-01") is None
    assert v.as_date(None) is None


def test_framework_root_finds_templates_and_skills():
    root = v.framework_root()
    assert (root / "templates" / "AGENTS.vault.md").is_file()
    assert (root / "skills").is_dir()


def test_impossible_date_stays_a_string_instead_of_crashing():
    fm, _, _, error = v.parse_frontmatter("---\ncreated: 2026-02-30\nupdated: 2026-03-01\n---\n")
    assert error is None
    assert fm["created"] == "2026-02-30"
    assert fm["updated"] == dt.date(2026, 3, 1)
    assert v.as_date(fm["created"]) is None
