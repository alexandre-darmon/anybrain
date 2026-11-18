"""Routing (PRD 18.2): what only the local profile may read, and how a file is opened to frontier models (D027)."""
from __future__ import annotations

import json

import pytest

from anybrain import graph as g
from anybrain import index, install
from anybrain import vault as v
from conftest import EXAMPLE, page

DEFAULT = v.DEFAULT_ROUTING_OBJ


@pytest.mark.parametrize("rel, profile", [
    ("me/preferences.md", "frontier"),
    ("me/profile.md", "local"),
    ("me/sub/notes.md", "local"),
    ("journal/2026-11-03.md", "local"),
    ("journal/reports/lint-2026-11-17.md", "frontier"),
    ("company/admin.md", "local"),
    ("finance/holdings.csv", "local"),
    ("raw/docs/report.pdf", "local"),
    ("raw/inbox/clip.md", "frontier"),
    ("library/snow-leopard.md", "frontier"),
    ("./me/preferences.md", "frontier"),
])
def test_default_routing(rel, profile):
    assert DEFAULT.route(rel) == profile


def test_file_entry_wins_over_folder_and_longest_folder_wins():
    routing = v.parse_routing({"default": "frontier", "me/": "local", "me/voice/": "frontier",
                               "me/voice/draft.md": "local", "library/": "frontier"})
    assert routing.route("me/voice/samples.md") == "frontier"
    assert routing.route("me/voice/draft.md") == "local"
    assert routing.route("me/profile.md") == "local"
    assert routing.exceptions() == ["me/voice/"]


def test_confidential_folders_cannot_be_opened():
    routing = v.parse_routing({"default": "frontier", "company/plan.md": "frontier", "finance/": "frontier"})
    assert routing.route("company/plan.md") == "local"
    assert routing.route("finance/x.csv") == "local"
    assert [key for key, _ in routing.ignored] == ["company/plan.md", "finance/"]
    assert routing.local_folders() == [".harness/baseline/", ".harness/traces/", "company/", "finance/"]


def test_bad_entries_are_ignored_with_a_reason():
    routing = v.parse_routing({"default": "cloud", "x/": "remote", "/etc/": "local", "../up/": "local"})
    reasons = dict(routing.ignored)
    assert routing.default == "frontier"
    assert set(reasons) == {"default", "x/", "/etc/", "../up/"}


def test_local_folders_and_exceptions_of_the_default():
    assert DEFAULT.local_folders() == [".harness/baseline/", ".harness/traces/", "company/", "finance/", "journal/",
                                       "me/", "raw/docs/"]
    assert DEFAULT.local_files() == [".harness/.env", ".harness/clients.txt", ".harness/names.txt"]
    assert DEFAULT.exceptions() == ["journal/reports/", "me/preferences.md", "me/voice.md"]  # Q35


def test_routing_is_read_from_models_toml(make_vault):
    root = make_vault({".harness/models.toml": '[routing]\ndefault = "frontier"\n"me/" = "local"\n"me/voice.md" = "frontier"\n'})
    routing = v.load_routing(root)
    assert routing.route("me/voice.md") == "frontier"
    assert routing.route("me/preferences.md") == "local"  # the owner's table replaces the default
    assert routing.route("journal/x.md") == "frontier"


def test_invalid_models_toml_is_a_clear_error(make_vault):
    root = make_vault({".harness/models.toml": "[routing\n"})
    with pytest.raises(v.VaultError, match="not valid TOML"):
        v.load_routing(root)


# ---------------------------------------------------------------- what the scripts show on the frontier profile

@pytest.fixture
def vault_with_me(make_vault):
    return make_vault({
        "me/preferences.md": page("Preferences", "profile", "Rules for the agent.", body="Follow [[tool]] rules."),
        "me/profile.md": page("Profile", "profile", "Private facts about the owner.", body="Works on [[tool]]."),
        "projects/tool.md": page("Tool", "project", "A tool."),
    })


def test_preferences_are_visible_and_the_rest_of_me_is_not(vault_with_me, capsys):
    data = g.Graph(vault_with_me).to_json_dict("t")["pages"]
    assert data["me/preferences.md"]["description"] == "Rules for the agent."
    assert data["me/profile.md"]["description"] == ""
    assert g.run_neighbors(vault_with_me, "projects/tool.md") == 0
    out = capsys.readouterr().out
    assert "me/preferences.md" in out and "Follow [[tool]] rules." in out
    assert "Profile (confidential: local profile only)" in out and "Works on" not in out


def test_one_line_in_models_toml_opens_a_file(vault_with_me, capsys):
    (vault_with_me / ".harness").mkdir()
    (vault_with_me / ".harness" / "models.toml").write_text(
        '[routing]\ndefault = "frontier"\n"me/" = "local"\n"me/preferences.md" = "frontier"\n"me/profile.md" = "frontier"\n')
    assert g.Graph(vault_with_me).to_json_dict("t")["pages"]["me/profile.md"]["description"] == "Private facts about the owner."
    rules = json.loads(install.opencode_config(v.load_routing(vault_with_me)))["permissions"]
    assert {"action": "read", "resource": "me/profile.md", "effect": "allow"} in rules


def test_a_page_routed_local_in_a_frontier_domain_shows_its_title_only(make_vault):
    root = make_vault({
        ".harness/models.toml": '[routing]\ndefault = "frontier"\n"library/private.md" = "local"\n',
        "library/private.md": page("Private book", "book", "A take I keep for myself.", author="A", status="read"),
        "library/public.md": page("Public book", "book", "Anyone can read this.", author="A", status="read"),
    })
    text = index.build(root)[0]["library/index.md"]
    assert "- [Private book](private.md): (confidential: local profile only)" in text
    assert "A take I keep for myself." not in text and "Anyone can read this." in text


def test_example_vault_shows_its_preferences():
    data = json.loads((EXAMPLE / ".harness" / "graph.json").read_text())["pages"]
    assert data["me/preferences.md"]["description"]
    assert data["me/profile.md"]["description"] == ""


def test_private_harness_files_are_always_local_and_the_rest_of_harness_is_readable():
    routing = v.parse_routing({"default": "frontier", ".harness/.env": "frontier", ".harness/traces/": "frontier"})
    for rel in (".harness/.env", ".harness/clients.txt", ".harness/names.txt", ".harness/traces/2026-11.jsonl",
                ".harness/baseline/run-claude-frontier-2026-11-17.json"):
        assert routing.route(rel) == "local", rel
    assert {key for key, _ in routing.ignored} == {".harness/.env", ".harness/traces/"}
    for rel in (".harness/templates/book.md", ".harness/graph.json", ".harness/models.toml", ".harness/feedback.log"):
        assert DEFAULT.route(rel) == "frontier", rel
