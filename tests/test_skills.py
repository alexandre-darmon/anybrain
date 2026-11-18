"""Skills follow the Agent Skills standard and PRD 15.1 and 15.2 (step 8)."""
from __future__ import annotations

import re

import pytest
import yaml

from anybrain import vault as v

SKILLS = sorted((v.framework_root() / "skills").glob("*/SKILL.md"))
FORBIDDEN = ("claude", "opencode", "codex", "cursor", "gemini", "openai", "anthropic", "gpt", "ollama", "llama",
             "mistral", "slash command", "subagent tool", "read tool", "write tool", "bash tool")


def frontmatter(path):
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), path
    return yaml.safe_load(text.split("---\n", 2)[1]), text


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_frontmatter_follows_the_standard(path):
    fm, _ = frontmatter(path)
    assert set(fm) == {"name", "description"}
    assert fm["name"] == path.parent.name
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", fm["name"]) and len(fm["name"]) <= 64
    assert 0 < len(fm["description"]) <= 200, len(fm["description"])
    assert "use " in fm["description"].lower(), "the description says when to use the skill"


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_no_model_provider_harness_or_tool_name(path):
    _, text = frontmatter(path)
    lower = text.lower()
    for word in FORBIDDEN:
        assert word not in lower, word


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_shared_rules_are_repeated_at_the_top(path):
    _, text = frontmatter(path)
    shared = text.index("## Shared rules")
    first_method = min(i for i in (text.find("## Method"), text.find("## Input"), text.find("## Record"),
                                   text.find("## Preconditions")) if i >= 0)
    assert shared < first_method
    rules = text[shared:first_method]
    for needle in ("AGENTS.md", "me/preferences.md", "anybrain index .", "anybrain feedback . add", "files opened"
                   if path.parent.name == "ask" else "three lines"):
        assert needle in text, needle
    assert len(re.findall(r"^\d+\. ", rules, re.M)) == 8


def test_p1_and_p1b_skills_exist():
    names = {p.parent.name for p in SKILLS}
    assert {"ingest", "ask", "lint", "recommend", "feedback", "daily-brief", "migrate"} <= names
    assert {"draft", "publish"} <= names  # P1b, built before step 14 at the owner's request (D044)


def test_commands_named_in_skills_exist():
    from anybrain import cli

    commands = set(cli.build_parser()._subparsers._group_actions[0].choices)
    for path in SKILLS:
        for command in re.findall(r"`anybrain ([a-z]+)", path.read_text()):
            assert command in commands, (path.parent.name, command)
