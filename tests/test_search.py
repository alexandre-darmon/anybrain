"""Search with QMD behind the routing (PRD 17.2, 17.4, 13.7; docs/decisions.md D055).

Most tests run a stand-in for QMD (tests/fake_qmd.py); the last one runs the real QMD when it is installed.
"""
from __future__ import annotations

import json
import os
import shutil
import sys

import pytest

from anybrain import cli, install, search
from anybrain import trace as t
from anybrain import vault as v
from conftest import EXAMPLE, ROOT, page

FAKE = ROOT / "tests" / "fake_qmd.py"


def enable(vault, extra=""):
    (vault / ".harness").mkdir(exist_ok=True)
    (vault / ".harness" / "models.toml").write_text(
        (ROOT / "templates" / "models.example.toml").read_text().replace('engine = "none"', 'engine = "qmd"') + extra)


@pytest.fixture
def qmd_env(tmp_path, monkeypatch):
    """QMD's config and cache in the test folder, and the stand-in on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "qmd"
    script.write_text(f"#!/bin/sh\nexec {sys.executable} {FAKE} \"$@\"\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("QMD_CONFIG_DIR", str(tmp_path / "qmd-config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("FAKE_QMD_LOG", str(tmp_path / "qmd.log"))
    monkeypatch.delenv("ANYBRAIN_PROFILE", raising=False)
    monkeypatch.setattr(search, "model_host_reachable", lambda timeout=10: False)
    return tmp_path


@pytest.fixture
def brain(tmp_path, qmd_env):
    root = tmp_path / "brain"
    shutil.copytree(EXAMPLE, root)
    enable(root)
    return root


def calls(env):
    log = env / "qmd.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.is_file() else []


def models(env):
    folder = env / "cache" / "qmd" / "models"
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("embeddinggemma.gguf", "qwen3-reranker.gguf", "qmd-query-expansion.gguf"):
        (folder / name).write_text("x")


# ---------------------------------------------------------------- the two indexes

def test_the_frontier_index_leaves_out_what_the_routing_keeps_local(brain):
    (brain / "projects" / "acme-mission.md").write_text(page("Acme", "project", kind="mission", status="active",
                                                            sensitivity="confidential", next_review="2026-12-01"))
    ignore = search.frontier_ignores(brain)
    for pattern in ("company/**", "finance/**", "me/**", "journal/**", "raw/docs/**", ".harness/**",
                    "projects/acme-mission.md"):
        assert pattern in ignore, pattern
    assert "wiki/**" not in ignore
    local = search.render_config(brain, "local")
    assert "company/**" not in local and ".harness/**" in local


def test_a_vault_local_by_default_opens_only_its_frontier_folders(brain):
    models_toml = brain / ".harness" / "models.toml"
    models_toml.write_text(models_toml.read_text().replace('default             = "frontier"', 'default = "local"\n"wiki/" = "frontier"'))
    pattern = search.frontier_pattern(brain).split(",")
    assert "wiki/**/*.md" in pattern and "me/voice.md" in pattern
    assert not any(p.startswith(("library/", "projects/", "raw/")) for p in pattern)


def test_configs_are_written_once_outside_the_vault(brain, qmd_env):
    assert sorted(search.write_configs(brain, out=lambda _: None)) == ["frontier", "local"]
    assert search.write_configs(brain, out=lambda _: None) == []
    for path in search.config_paths(brain).values():
        assert path.parent == qmd_env / "qmd-config" and not str(path).startswith(str(brain))


# ---------------------------------------------------------------- anybrain search

def test_a_frontier_session_never_sees_a_local_only_result(brain, qmd_env, capsys, monkeypatch):
    assert cli.main(["search", str(brain), "admin"]) == 0
    assert "company/" not in capsys.readouterr().out
    monkeypatch.setenv("FAKE_QMD_IGNORE_EXCLUDES", "1")  # an index built before the routing changed
    assert cli.main(["search", str(brain), "admin"]) == 0
    out = capsys.readouterr().out
    assert "company/admin.md" not in out and "result(s) hidden: local-only" in out


def test_the_local_profile_searches_everything(brain, qmd_env, capsys, monkeypatch):
    monkeypatch.setenv("ANYBRAIN_PROFILE", "local")
    assert cli.main(["search", str(brain), "admin"]) == 0
    assert "company/admin.md" in capsys.readouterr().out
    assert calls(qmd_env)[-1]["args"][:2] == ["--index", search.index_names(brain)["local"]]


def test_meaning_search_waits_for_the_models_and_says_so(brain, qmd_env, capsys):
    assert cli.main(["search", str(brain), "pricing"]) == 0
    assert "keyword results" in capsys.readouterr().out
    assert calls(qmd_env)[-1]["args"][2] == "search"
    models(qmd_env)
    assert cli.main(["search", str(brain), "pricing"]) == 0
    assert calls(qmd_env)[-1]["args"][2] == "query"
    assert cli.main(["search", str(brain), "--exact", "pricing"]) == 0
    assert calls(qmd_env)[-1]["args"][2] == "search"


def test_raw_keeps_only_sources_and_qmd_never_runs_in_the_vault(brain, qmd_env, capsys):
    assert cli.main(["search", str(brain), "--exact", "--raw", "value equation"]) == 0
    out = capsys.readouterr().out
    assert "raw/highlights/100m-offers.md" in out and "library/" not in out
    assert all(not c["cwd"].startswith(str(brain)) for c in calls(qmd_env))


def test_search_off_or_qmd_missing_says_how_to_turn_it_on(brain, qmd_env, monkeypatch, capsys):
    monkeypatch.setenv("PATH", "/nonexistent")
    assert cli.main(["search", str(brain), "pricing"]) == 2
    assert "npm install -g @tobilu/qmd" in capsys.readouterr().err
    enable(brain)
    (brain / ".harness" / "models.toml").write_text((ROOT / "templates" / "models.example.toml").read_text())
    assert cli.main(["search", str(brain), "pricing"]) == 2
    assert 'engine = "qmd"' in capsys.readouterr().err


# ---------------------------------------------------------------- refresh and link suggestions

def test_refresh_offline_keeps_keyword_search_and_never_hangs(brain, qmd_env, capsys):
    assert cli.main(["search", str(brain), "--refresh"]) == 0
    out = capsys.readouterr().out
    assert "keyword search: up to date" in out and "does not answer from here" in out
    assert [c["args"][2] for c in calls(qmd_env)] == ["update", "update"]


def test_refresh_downloads_the_models_once_then_embeds_both_indexes(brain, qmd_env, monkeypatch, capsys):
    monkeypatch.setattr(search, "model_host_reachable", lambda timeout=10: True)
    assert cli.main(["search", str(brain), "--refresh"]) == 0
    assert [c["args"][2] for c in calls(qmd_env)] == ["update", "update", "pull", "embed", "embed"]
    assert "meaning search: up to date" in capsys.readouterr().out
    monkeypatch.setenv("FAKE_QMD_PULL_FAIL", "1")
    assert cli.main(["search", str(brain), "--refresh"]) == 0  # models present now: no new download
    assert [c["args"][2] for c in calls(qmd_env)][-2:] == ["embed", "embed"]


def test_refresh_never_runs_inside_an_eval_case(brain, qmd_env, monkeypatch, capsys):
    monkeypatch.setenv(v.EVAL_COPY_ENV, str(brain))
    assert cli.main(["search", str(brain), "--refresh"]) == 2
    assert "eval case" in capsys.readouterr().err


def test_an_eval_case_searches_the_indexes_of_the_vault_it_was_copied_from(brain, qmd_env, tmp_path, monkeypatch):
    copy = tmp_path / "case" / "vault"
    shutil.copytree(brain, copy)
    monkeypatch.setenv(v.EVAL_COPY_ENV, str(copy))
    monkeypatch.setenv(search.EVAL_ORIGIN_ENV, str(brain))
    assert search.index_names(copy) == {p: n for p, n in search.index_names(brain).items()}


def test_link_suggestions_list_similar_pages_that_are_not_linked(brain, qmd_env, capsys):
    (brain / "wiki" / "concepts" / "positioning-twin.md").write_text(page(
        "Positioning twin", "concept", description="Category design and positioning, the same idea again.",
        confidence="low", body="## Draft position\n\nCategory design matters.\n"))
    models(qmd_env)
    assert cli.main(["search", str(brain), "--similar"]) == 0
    out = capsys.readouterr().out
    assert "wiki/concepts/positioning-twin.md" in out and "<->" in out
    assert "index.md" not in out and "company/" not in out


# ---------------------------------------------------------------- AGENTS.md, doctor and traces

def test_agents_md_gets_the_search_step_only_when_search_is_on(tmp_path, qmd_env, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "new"
    assert cli.main(["init", str(root)]) == 0
    assert "anybrain search" not in (root / "AGENTS.md").read_text() and "Search (PRD 17.2) is off" in (root / "AGENTS.md").read_text()
    enable(root)
    assert cli.main(["update", str(root), "--yes"]) == 0
    assert 'anybrain search . "<question>"' in (root / "AGENTS.md").read_text()
    assert all(path.is_file() for path in search.config_paths(root).values())


def test_doctor_reports_search_and_refuses_a_qmd_folder_in_the_vault(tmp_path, qmd_env, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "new"
    assert cli.main(["init", str(root)]) == 0
    enable(root)
    assert cli.main(["update", str(root), "--yes"]) == 0
    capsys.readouterr()
    cli.main(["doctor", str(root)])
    out = capsys.readouterr().out
    assert "QMD index configs follow the routing" in out and "keyword search only" in out
    (root / ".qmd").mkdir()
    assert cli.main(["doctor", str(root)]) == 1
    assert "holds a .qmd/ folder" in capsys.readouterr().out


@pytest.mark.parametrize("command, flagged", [("qmd query pricing", True), ("qmd --index anybrain-x-local get company/admin.md", True),
                                              ("qmd --index anybrain-x-frontier search pricing", False),
                                              ("anybrain search . pricing", False), ("qmd status", False)])
def test_qmd_run_directly_counts_as_a_search_of_the_whole_vault(brain, command, flagged):
    read, _, searches = t.files_from_shell(command, brain)
    assert bool(t.local_only_reads(brain, read, None, searches)) is flagged, command


# ---------------------------------------------------------------- the real QMD

@pytest.mark.skipif(shutil.which("qmd") is None or os.environ.get("ANYBRAIN_NO_REAL_QMD"), reason="QMD is not installed")
def test_with_the_real_qmd_keyword_search_follows_the_routing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("QMD_CONFIG_DIR", str(tmp_path / "qmd-config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("ANYBRAIN_PROFILE", raising=False)
    monkeypatch.setattr(search, "model_host_reachable", lambda timeout=10: False)
    root = tmp_path / "brain"
    shutil.copytree(EXAMPLE, root)
    enable(root)
    assert cli.main(["search", str(root), "--refresh"]) == 0
    capsys.readouterr()
    assert cli.main(["search", str(root), "--exact", "admin"]) == 0
    assert "company/" not in capsys.readouterr().out
    monkeypatch.setenv("ANYBRAIN_PROFILE", "local")
    assert cli.main(["search", str(root), "--exact", "admin"]) == 0
    assert "company/admin.md" in capsys.readouterr().out
