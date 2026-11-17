"""init, update, doctor (PRD 9.3, 9.5, 18.3, 18.4, 20; acceptance of step 6)."""
from __future__ import annotations

import http.server
import json
import os
import stat
import threading
import tomllib

import pytest

from anybrain import cli, install, lint
from anybrain import vault as v

SKILLS = sorted(p.name for p in install.repo_skills())


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fake HOME so skill links never touch the real one."""
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("HOME", str(path))
    return path


@pytest.fixture
def brain(tmp_path, home, capsys):
    root = tmp_path / "brain"
    assert install.init(str(root), owner="Test Owner", role="product manager", lang="fr") == 0
    capsys.readouterr()
    return root


def snapshot(root):
    """Every file and link under root with its content, to prove a command changed nothing."""
    state = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            state[rel] = ("link", os.readlink(path))
        elif path.is_file():
            state[rel] = ("file", path.read_bytes(), path.stat().st_mtime_ns)
        else:
            state[rel] = ("dir",)
    return state


# ---------------------------------------------------------------- init

def test_init_creates_the_p0_layout(brain):
    for rel in install.LAYOUT_DIRS:
        assert (brain / rel).is_dir(), rel
    for rel in ("AGENTS.md", "log.md", ".harness/models.toml", ".harness/render.json", ".harness/graph.json",
                ".harness/clients.txt", ".harness/names.txt", ".claude/settings.json", "opencode.json",
                ".obsidian/app.json", "index.md", "me/preferences.md"):
        assert (brain / rel).is_file(), rel
    for name in ("book", "concept", "note", "project", "profile", "brief"):
        assert (brain / ".harness" / "templates" / f"{name}.md").is_file()


def test_init_renders_agents_md_and_links_claude_md(brain):
    text = (brain / "AGENTS.md").read_text()
    assert text.startswith("# AGENTS.md: Test Owner's brain")
    assert "Test Owner, product manager." in text
    assert "Default language for new pages: `fr`" in text
    assert "{{" not in text
    assert os.readlink(brain / "CLAUDE.md") == "AGENTS.md"


def test_init_links_every_skill_into_every_default_dir(brain, home):
    for directory in (home / ".agents" / "skills", home / ".claude" / "skills"):
        assert sorted(p.name for p in directory.iterdir()) == SKILLS
        for name in SKILLS:
            assert (directory / name).is_symlink()
            assert (directory / name / "SKILL.md").is_file()


def test_init_twice_is_a_no_op(brain, home, capsys):
    before, home_before = snapshot(brain), snapshot(home)
    assert install.init(str(brain)) == 0
    assert snapshot(brain) == before
    assert snapshot(home) == home_before
    assert "init: 0 change(s)" in capsys.readouterr().out


def test_init_never_overwrites_existing_files(tmp_path, home):
    root = tmp_path / "brain"
    (root / ".obsidian").mkdir(parents=True)
    (root / "AGENTS.md").write_text("my own rules\n")
    (root / "log.md").write_text("2025-01-01 · old · kept\n")
    (root / ".obsidian" / "app.json").write_text(json.dumps({"attachmentFolderPath": "Files", "theme": "dark"}))
    assert install.init(str(root)) == 0
    assert (root / "AGENTS.md").read_text() == "my own rules\n"
    assert (root / "log.md").read_text() == "2025-01-01 · old · kept\n"
    app = json.loads((root / ".obsidian" / "app.json").read_text())
    assert app["attachmentFolderPath"] == "Files" and app["theme"] == "dark"
    assert app["newFileLocation"] == "folder" and app["newFileFolderPath"] == "raw/inbox"
    assert "AGENTS.md" not in install.load_render(root)["files"]


def test_env_file_is_private(brain):
    assert stat.S_IMODE((brain / ".harness" / ".env").stat().st_mode) == 0o600


def test_fresh_vault_passes_lint(brain, capsys):
    assert lint.run(brain) == 0
    assert "lint: 0 error(s), 0 warning(s)" in capsys.readouterr().out


def test_skill_dirs_come_from_models_toml(tmp_path, home):
    root = tmp_path / "brain"
    (root / ".harness").mkdir(parents=True)
    custom = tmp_path / "custom-skills"
    (root / ".harness" / "models.toml").write_text(f'[skills]\ndirs = ["{custom}"]\n')
    assert install.init(str(root)) == 0
    assert sorted(p.name for p in custom.iterdir()) == SKILLS
    assert not (home / ".claude").exists()


def test_init_keeps_a_real_folder_where_a_skill_link_would_go(tmp_path, home, capsys):
    (home / ".claude" / "skills" / "ask").mkdir(parents=True)
    assert install.init(str(tmp_path / "brain")) == 0
    assert "kept" in capsys.readouterr().out
    assert not (home / ".claude" / "skills" / "ask").is_symlink()


def test_init_from_the_cli(tmp_path, home, capsys):
    assert cli.main(["init", str(tmp_path / "b"), "--owner", "A", "--lang", "en"]) == 0
    assert (tmp_path / "b" / "AGENTS.md").read_text().startswith("# AGENTS.md: A's brain")


# ---------------------------------------------------------------- framework templates (Q6, docs/decisions.md D020)

def test_rendered_harness_configs_follow_the_default_routing(brain):
    claude = json.loads((brain / ".claude" / "settings.json").read_text())
    assert claude["env"]["ANYBRAIN_PROFILE"] == "frontier"
    assert claude["permissions"]["deny"] == install.claude_read_rules(v.DEFAULT_ROUTING_OBJ)
    opencode = json.loads((brain / "opencode.json").read_text())
    denied = [".harness/baseline/**", ".harness/traces/**", "company/**", "finance/**", "journal/**", "me/**",
              "raw/docs/**", ".harness/.env", ".harness/clients.txt", ".harness/names.txt"]
    assert opencode["permissions"] == (
        [{"action": "read", "resource": r, "effect": "deny"} for r in denied]
        + [{"action": "read", "resource": "journal/reports/**", "effect": "allow"},
           {"action": "read", "resource": "me/preferences.md", "effect": "allow"}])
    local = opencode["agents"]["local"]
    assert local["model"].startswith("ollama/")
    assert [r["resource"] for r in local["permissions"] if r["effect"] == "allow"] == denied


def test_claude_rules_deny_every_local_folder_and_reopen_the_exceptions():
    rules = install.claude_read_rules(v.DEFAULT_ROUTING_OBJ)
    assert rules == ["Read(./.harness/baseline/**)", "Read(./.harness/traces/**)", "Read(./company/**)",
                     "Read(./finance/**)", "Read(journal/*)", "Read(!journal/reports/**)", "Read(me/*)",
                     "Read(!me/preferences.md)", "Read(./raw/docs/**)", "Read(./.harness/.env)",
                     "Read(./.harness/clients.txt)", "Read(./.harness/names.txt)"]


def test_claude_rules_for_a_subfolder_exception():
    routing = v.parse_routing({"default": "frontier", "me/": "local", "me/preferences.md": "frontier",
                               "me/voice/": "frontier"})
    assert install.claude_read_rules(routing)[2:] == ["Read(./company/**)", "Read(./finance/**)", "Read(me/*)",
                                                      "Read(!me/preferences.md)", "Read(!me/voice/**)",
                                                      "Read(./.harness/.env)", "Read(./.harness/clients.txt)",
                                                      "Read(./.harness/names.txt)"]


def test_models_template_is_valid():
    models = tomllib.loads((v.framework_root() / "templates" / "models.example.toml").read_text())
    assert models["harnesses"]["claude"]["headless"] == "claude -p {prompt}"
    assert models["harnesses"]["opencode"]["headless"] == "opencode run {prompt}"
    assert models["skills"]["dirs"] == ["~/.agents/skills", "~/.claude/skills"]
    assert v.parse_routing(models["routing"]) == v.DEFAULT_ROUTING_OBJ


def test_update_follows_a_routing_change(brain, capsys):
    models = brain / ".harness" / "models.toml"
    models.write_text(models.read_text().replace('"journal/"          = "local"\n', ""))
    assert install.doctor(str(brain), timeout=0.2) == 1
    assert "does not match the routing" in capsys.readouterr().out
    assert install.update(str(brain), yes=True) == 0
    out = capsys.readouterr().out
    assert "updated .claude/settings.json" in out and "updated opencode.json" in out
    assert "journal" not in (brain / "opencode.json").read_text()
    assert "company/**" in (brain / "opencode.json").read_text()


def test_update_reports_ignored_routing_entries(brain, capsys):
    models = brain / ".harness" / "models.toml"
    line = '"raw/docs/"         = "local"'
    models.write_text(models.read_text().replace(line, line + '\n"company/plan.md" = "frontier"', 1))
    assert install.update(str(brain), yes=True) == 0
    assert "routing entry 'company/plan.md' ignored" in capsys.readouterr().out
    assert "company/**" in (brain / "opencode.json").read_text()


def test_agents_template_names_no_harness_or_model():
    text = (v.framework_root() / "templates" / "AGENTS.vault.md").read_text().lower()
    for word in ("claude", "opencode", "openai", "anthropic", "gpt", "ollama", "slash command"):
        assert word not in text, word


# ---------------------------------------------------------------- update

def test_update_replaces_an_untouched_file_when_the_repo_changes(brain, monkeypatch, capsys):
    original = install.framework_files

    def newer(params, routing=None):
        return [install.Rendered(f.rel, f.content + "\nNew rule.\n") if f.rel == "AGENTS.md" else f
                for f in original(params, routing)]

    monkeypatch.setattr(install, "framework_files", newer)
    assert install.update(str(brain), yes=True) == 0
    assert (brain / "AGENTS.md").read_text().endswith("New rule.\n")
    assert "updated AGENTS.md" in capsys.readouterr().out


def test_update_keeps_an_owner_modified_file_with_yes(brain, capsys):
    (brain / ".claude" / "settings.json").write_text('{"permissions": {}}\n')
    assert install.update(str(brain), yes=True) == 0
    assert (brain / ".claude" / "settings.json").read_text() == '{"permissions": {}}\n'
    assert "kept .claude/settings.json: you modified it" in capsys.readouterr().out


def test_update_shows_a_diff_and_replaces_on_confirmation(brain, capsys):
    (brain / "opencode.json").write_text("{}\n")
    assert install.update(str(brain), ask=lambda question: True) == 0
    out = capsys.readouterr().out
    assert "--- opencode.json (yours)" in out and "+++ opencode.json (anybrain)" in out
    assert json.loads((brain / "opencode.json").read_text())["agents"]["local"]["mode"] == "primary"
    # the replacement is recorded: a later change in the repo is now applied without a question
    assert install.load_render(brain)["files"]["opencode.json"] == install.sha((brain / "opencode.json").read_text())


def test_update_keeps_the_owner_version_when_the_answer_is_no(brain):
    (brain / "opencode.json").write_text("{}\n")
    assert install.update(str(brain), ask=lambda question: False) == 0
    assert (brain / "opencode.json").read_text() == "{}\n"


def test_update_without_a_terminal_keeps_the_owner_version(brain, monkeypatch):
    (brain / "opencode.json").write_text("{}\n")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert install.update(str(brain)) == 0
    assert (brain / "opencode.json").read_text() == "{}\n"


def test_update_restores_missing_framework_files_and_links(brain, home):
    (brain / ".harness" / "templates" / "book.md").unlink()
    (brain / "CLAUDE.md").unlink()
    (home / ".agents" / "skills" / "ask").unlink()
    assert install.update(str(brain), yes=True) == 0
    assert (brain / ".harness" / "templates" / "book.md").is_file()
    assert os.readlink(brain / "CLAUDE.md") == "AGENTS.md"
    assert (home / ".agents" / "skills" / "ask").is_symlink()


def test_update_never_touches_content_folders(brain):
    (brain / "library" / "a-book.md").write_text("owner content\n")
    content = {rel: data for rel, data in snapshot(brain).items()
               if rel.split("/")[0] in ("me", "raw", "library", "wiki", "projects", "company", "finance", "journal")
               or rel in ("log.md", "index.md")}
    assert install.update(str(brain), yes=True) == 0
    after = snapshot(brain)
    assert {rel: after[rel] for rel in content} == content


def test_update_asks_before_repointing_a_skill_link(brain, home, tmp_path):
    other = tmp_path / "other-ask"
    other.mkdir()
    link = home / ".claude" / "skills" / "ask"
    link.unlink()
    link.symlink_to(other)
    assert install.update(str(brain), yes=True) == 0
    assert link.resolve() == other.resolve()
    assert install.update(str(brain), ask=lambda question: True) == 0
    assert link.resolve() == (v.framework_root() / "skills" / "ask").resolve()


# ---------------------------------------------------------------- doctor

@pytest.fixture
def no_endpoint(brain):
    models = brain / ".harness" / "models.toml"
    models.write_text(models.read_text().replace("http://localhost:11434/v1", "http://127.0.0.1:9/v1"))
    return brain


def test_doctor_on_a_fresh_vault(no_endpoint, capsys):
    assert install.doctor(str(no_endpoint), timeout=0.5) == 0
    out = capsys.readouterr().out
    assert "warn  local model endpoint does not answer" in out
    assert "note  iCloud Advanced Data Protection should be ON" in out
    assert "doctor: 0 problem(s)" in out


def test_doctor_reports_a_planted_icloud_file(no_endpoint, capsys):
    (no_endpoint / "library" / ".a-book.md.icloud").write_text("")
    assert install.doctor(str(no_endpoint), timeout=0.5) == 1
    out = capsys.readouterr().out
    assert "fail  1 iCloud placeholder file(s), e.g. library/.a-book.md.icloud" in out
    assert "Keep Downloaded" in out


def test_doctor_reports_a_readable_env_file(no_endpoint, capsys):
    (no_endpoint / ".harness" / ".env").chmod(0o644)
    assert install.doctor(str(no_endpoint), timeout=0.5) == 1
    assert "chmod 600" in capsys.readouterr().out


def test_doctor_reports_missing_skill_links_and_agents_drift(no_endpoint, home, capsys):
    (home / ".claude" / "skills" / "lint").unlink()
    (no_endpoint / "AGENTS.md").write_text((no_endpoint / "AGENTS.md").read_text() + "\nmine\n")
    assert install.doctor(str(no_endpoint), timeout=0.5) == 1
    out = capsys.readouterr().out
    assert "fail  skills not linked" in out and "lint (missing)" in out
    assert "warn  AGENTS.md differs from the last render" in out


def test_doctor_git_is_a_warning_outside_icloud_and_a_failure_inside(no_endpoint, home, tmp_path, capsys, monkeypatch):
    (no_endpoint / ".git").mkdir()
    assert install.doctor(str(no_endpoint), timeout=0.5) == 0
    assert "warn  vault contains a .git directory" in capsys.readouterr().out
    icloud = tmp_path / "Mobile Documents"
    monkeypatch.setattr(install, "ICLOUD_ROOT", str(icloud))
    inside = icloud / "brain"
    inside.mkdir(parents=True)
    for item in no_endpoint.iterdir():
        os.rename(item, inside / item.name)
    assert install.doctor(str(inside), timeout=0.5) == 1
    assert "fail  vault contains a .git directory" in capsys.readouterr().out


def test_doctor_reaches_a_local_endpoint(brain, capsys):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == "/v1/models" else 404)
            self.end_headers()
            self.wfile.write(b'{"data": []}')

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        models = brain / ".harness" / "models.toml"
        models.write_text(models.read_text().replace("http://localhost:11434/v1",
                                                     f"http://127.0.0.1:{server.server_port}/v1"))
        assert install.doctor(str(brain)) == 0
        assert "ok    local model endpoint answers" in capsys.readouterr().out
    finally:
        server.shutdown()
