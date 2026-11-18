"""Page templates match the contracts of PRD section 11 (step 3)."""
from __future__ import annotations

import re

import pytest

from anybrain import lint, vault as v

TEMPLATES = v.framework_root() / "templates" / "pages"

# Sections each contract requires, in order (PRD 11.2 to 11.5, 15.7).
CONTRACT_SECTIONS = {
    "book": ["My take", "Key ideas", "Quotes", "Related"],
    "concept": ["Summary", "What the sources say", "My position", "Draft position", "Timeline", "Open questions"],
    "note": ["Summary", "Key points", "Related"],
    "project": ["Why", "Now", "Timeline", "Decisions", "Next steps"],
    "brief": ["Today", "Needs a reply", "Projects"],
    "profile": [],
    "draft": ["Notes for the owner"],
    "published": [],
    "channel": ["Audience", "Goal", "Format", "Rules", "Examples"],
}
FOLDER = {
    "book": "library", "concept": "wiki/concepts", "note": "wiki/notes",
    "project": "projects", "brief": "journal", "profile": "me",
    "draft": "content/drafts", "published": "content/published", "channel": "content/channels",
}
VALUES = {
    "title": "Example title", "description": "Says one thing.", "author": "A. Author",
    "status": "read", "kind": "portfolio", "language": "en", "created": "2026-11-16",
    "updated": "2026-11-16", "date": "2026-11-16", "next_review": "2026-12-01",
    "channel": "linkedin", "audience": "Product managers who run interviews.", "angle": "Interviews fail on hypotheticals.",
    "url": "https://example.com/post", "published": "2026-11-16", "draft": "content/drafts/2026-11-16-example-title.md",
}


def render(name: str) -> str:
    text = (TEMPLATES / f"{name}.md").read_text(encoding="utf-8")
    if name == "project":
        text = text.replace('"{{status}}"', '"active"')
    if name == "draft":
        text = text.replace('"{{status}}"', '"drafting"').replace("sources: []", "sources: [library/example.md]")
    if name == "published":
        text = text.replace("edit_ratio:  #", "edit_ratio: 0.4  #")
    return re.sub(r"\{\{(\w+)\}\}", lambda m: VALUES[m.group(1)], text)


@pytest.mark.parametrize("name", sorted(CONTRACT_SECTIONS))
def test_template_has_contract_sections_in_order(name):
    fm, body, _, error = v.parse_frontmatter(render(name))
    assert error is None
    assert fm["type"] == name
    titles = [s.title for s in v.sections(body) if s.level == 2]
    assert titles == CONTRACT_SECTIONS[name]


@pytest.mark.parametrize("name", sorted(CONTRACT_SECTIONS))
def test_rendered_template_passes_schema(name, make_vault):
    slug = {"brief": "2026-11-16", "draft": "2026-11-16-example-title", "published": "2026-11-16-example-title",
            "channel": "linkedin"}.get(name, "example-title")
    root = make_vault({f"{FOLDER[name]}/{slug}.md": render(name)})
    ctx = lint.Context(root)
    assert lint.check_schema(ctx) == []


@pytest.mark.parametrize("name", sorted(CONTRACT_SECTIONS))
def test_template_has_common_fields(name):
    fm, *_ = v.parse_frontmatter(render(name))
    for field in ("title", "type", "description", "tags", "language", "created", "updated", "sources", "sensitivity"):
        assert field in fm, field


def test_templates_contain_no_live_links():
    """Guidance links sit inside HTML comments so a fresh page has no broken link."""
    for path in TEMPLATES.glob("*.md"):
        text = re.sub(r"<!--.*?-->", "", path.read_text(encoding="utf-8"), flags=re.S)
        assert "[[" not in text, path.name


def test_placeholders_are_quoted_so_templates_stay_valid_yaml():
    for path in TEMPLATES.glob("*.md"):
        fm, _, _, error = v.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert error is None, path.name


def test_voice_template_is_a_profile_with_a_banned_list(make_vault):
    text = (TEMPLATES / "voice.md").read_text(encoding="utf-8").replace("{{language}}", "en")
    text = text.replace("{{created}}", "2026-11-16").replace("{{updated}}", "2026-11-16")
    fm, body, _, error = v.parse_frontmatter(text)
    assert error is None and fm["type"] == "profile"
    assert [s.title for s in v.sections(body) if s.level == 2] == ["How I sound", "Signature moves", "Banned", "Examples"]
    root = make_vault({"me/voice.md": text})
    patterns, problems = lint.banned_patterns(root)
    assert problems == [] and len(patterns) == 9
    assert any(p.search("a " + chr(0x2014) + " b") for _, p in patterns), "the em dash is banned by default"
    assert lint.check_schema(lint.Context(root)) == []
