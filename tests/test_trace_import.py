"""`anybrain trace import`: finished Claude Code sessions become run traces (PRD 22.5, 22.6, D049).

The transcripts here are synthetic, in the shape Claude Code 2.1.327 writes to
`~/.claude/projects/<folder>/<session>.jsonl`: one event per line, `promptId` on the owner's messages, `usage` on
each model message (repeated for each content block of the same message), and `cost-state` checkpoints holding
the session's running cost.
"""
from __future__ import annotations

import datetime as dt
import json
import re

import pytest

from anybrain import cli
from anybrain import trace as t
from conftest import page

PROMPT = "What do I think about pricing? SECRET-PROMPT-WORDS"
ANSWER = "You hold that pricing is positioning. SECRET-ANSWER-WORDS\n\nfiles opened: 2"


class Transcript:
    """Builds one session transcript, event by event."""

    def __init__(self, cwd, session="11111111-2222-3333-4444-555555555555", version="2.1.327", model="test-model",
                 start="2026-11-17T09:00:00"):
        self.cwd, self.session, self.version, self.model = str(cwd), session, version, model
        self.events: list[dict] = []
        self.clock = dt.datetime.fromisoformat(start)
        self.n = 0
        self.prompt_id = None

    def _base(self, kind: str) -> dict:
        self.clock += dt.timedelta(seconds=1)
        self.n += 1
        return {"type": kind, "sessionId": self.session, "cwd": self.cwd, "version": self.version,
                "timestamp": self.clock.isoformat(timespec="milliseconds") + "Z", "uuid": f"u{self.n}",
                "isSidechain": False}

    def prompt(self, text=PROMPT, prompt_id=None):
        self.prompt_id = prompt_id or f"p{self.n}"
        event = self._base("user")
        event.update({"promptId": self.prompt_id, "message": {"role": "user", "content": text}})
        self.events.append(event)
        return self

    def tool(self, name, data, result="ok", error=False, usage=None, skill=None, denied=False):
        tool_id = f"toolu_{self.n}"
        usage = usage or {"input_tokens": 2, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 100,
                          "cache_creation": {"ephemeral_1h_input_tokens": 100, "ephemeral_5m_input_tokens": 0},
                          "output_tokens": 50}
        message_id = f"msg_{self.n}"
        for block in ({"type": "thinking", "thinking": ""}, {"type": "tool_use", "id": tool_id, "name": name, "input": data}):
            event = self._base("assistant")  # Claude Code writes one event per content block, usage repeated
            event["message"] = {"id": message_id, "model": self.model, "role": "assistant", "content": [block],
                                "usage": usage}
            if skill:
                event["attributionSkill"] = skill
            self.events.append(event)
        event = self._base("user")
        content = {"type": "tool_result", "tool_use_id": tool_id, "content": result}
        if error or denied:
            content["is_error"] = True
        event.update({"promptId": self.prompt_id, "message": {"role": "user", "content": [content]}})
        if denied:
            event["toolDenialKind"] = "user-rejected"
        self.events.append(event)
        return tool_id

    def answer(self, text=ANSWER, skill=None):
        event = self._base("assistant")
        event["message"] = {"id": f"msg_{self.n}", "model": self.model, "role": "assistant",
                            "content": [{"type": "text", "text": text}],
                            "usage": {"input_tokens": 1, "cache_read_input_tokens": 500, "output_tokens": 100}}
        if skill:
            event["attributionSkill"] = skill
        self.events.append(event)
        return self

    def chdir(self, folder):
        """Claude Code records the shell's folder on every event: a later event carries the new one."""
        self.cwd = str(folder)
        return self

    def cost(self, total):
        self.events.append({"type": "cost-state", "sessionId": self.session, "totalCostUSD": total,
                            "modelUsage": {self.model: {"inputTokens": 10, "outputTokens": 400,
                                                        "cacheReadInputTokens": 5000,
                                                        "cacheCreationInputTokens": 500, "costUSD": total}}})
        return self

    def write(self, home, folder=None):
        folder = home / "projects" / (folder or t.project_dir_name(self.cwd))
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{self.session}.jsonl"
        path.write_text("".join(json.dumps(e) + "\n" for e in self.events), encoding="utf-8")
        return path


@pytest.fixture
def vault(make_vault):
    return make_vault({
        "wiki/concepts/pricing.md": page("Pricing", "concept", confidence="low"),
        "wiki/index.md": "# Wiki\n",
        "company/q3.md": page("Q3", "note"),
        "me/profile.md": page("Profile", "profile"),
        "me/preferences.md": page("Preferences", "profile"),
        "log.md": "2026-11-01 · init · created\n",
    })


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "claude-home"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    monkeypatch.setenv("ANYBRAIN_TODAY", "2026-11-19")
    return home


def records(vault):
    return [json.loads(line) for path in sorted((vault / t.TRACES_DIR).glob("*.jsonl"))
            for line in path.read_text(encoding="utf-8").splitlines()]


def ask_session(vault, **kwargs):
    s = Transcript(vault, **kwargs).prompt()
    s.tool("Skill", {"skill": "ask"})
    s.tool("Read", {"file_path": str(vault / "wiki/index.md")}, skill="ask")
    s.tool("Bash", {"command": "cat wiki/concepts/pricing.md"}, skill="ask")
    s.answer(skill="ask")
    return s


def test_one_finished_question_becomes_one_record(vault, home, capsys):
    ask_session(vault).cost(0.25).write(home)
    assert t.import_sessions(vault) == 0
    [record] = records(vault)
    assert record["trigger"] == "session" and record["skill"] == "ask" and record["harness"] == "claude"
    assert record["harness_version"] == "2.1.327" and record["model"] == "test-model"
    assert record["profile"] == "frontier"
    assert record["files_read"] == ["wiki/index.md", "wiki/concepts/pricing.md"]
    assert record["cost_usd"] == 0.25 and record["cost_shared"] is False  # the harness's own figure
    # usage repeated on each content block of a message counts once
    assert record["tokens"] == {"input": 3 * (2 + 1000 + 100) + 1 + 500, "output": 3 * 50 + 100, "cached": 3 * 1000 + 500}
    assert record["self_reported_files_opened"] == 2
    assert record["started"] == "2026-11-17T09:00:01Z"
    assert record["run_id"].startswith("claude:11111111-2222-3333-4444-555555555555:")
    assert "imported 1 question(s) from 1 Claude Code session(s)" in capsys.readouterr().out


def test_a_record_never_holds_the_prompt_or_the_answer(vault, home):
    ask_session(vault).cost(0.25).write(home)
    t.import_sessions(vault)
    text = "".join(p.read_text(encoding="utf-8") for p in (vault / t.TRACES_DIR).glob("*"))
    assert "SECRET-PROMPT-WORDS" not in text and "SECRET-ANSWER-WORDS" not in text


def test_import_twice_changes_nothing(vault, home, capsys):
    ask_session(vault).cost(0.25).write(home)
    t.import_sessions(vault)
    before = (vault / t.TRACES_DIR / "2026-11.jsonl").read_text(encoding="utf-8")
    capsys.readouterr()
    t.import_sessions(vault)
    assert (vault / t.TRACES_DIR / "2026-11.jsonl").read_text(encoding="utf-8") == before
    assert "imported 0 question(s)" in capsys.readouterr().out


def test_questions_sharing_a_checkpoint_share_its_cost_by_tokens(vault, home):
    s = ask_session(vault)
    s.prompt("second question", prompt_id="second")
    s.tool("Read", {"file_path": str(vault / "wiki/concepts/pricing.md")},
           usage={"input_tokens": 1, "output_tokens": 5000})  # far more output: a larger share
    s.answer()
    s.cost(1.0).write(home)
    t.import_sessions(vault)
    first, second = records(vault)
    assert first["cost_shared"] and second["cost_shared"]
    assert first["cost_usd"] + second["cost_usd"] == pytest.approx(1.0, abs=1e-3)
    assert second["cost_usd"] > first["cost_usd"]
    assert second["skill"] is None  # the harness attributes no skill to it: none is guessed


def test_each_checkpoint_interval_keeps_its_exact_cost(vault, home):
    s = ask_session(vault).cost(0.30)
    s.prompt("again", prompt_id="again")
    s.tool("Read", {"file_path": str(vault / "wiki/index.md")})
    s.answer()
    s.cost(0.50).write(home)  # running total: the second question cost 0.20
    t.import_sessions(vault)
    assert [r["cost_usd"] for r in records(vault)] == [0.3, 0.2]
    assert not any(r["cost_shared"] for r in records(vault))


def test_a_question_after_the_last_checkpoint_waits_for_the_session_to_end(vault, home, capsys):
    s = ask_session(vault).cost(0.25)
    s.prompt("still talking", prompt_id="open")
    s.tool("Read", {"file_path": str(vault / "wiki/index.md")})
    s.write(home)
    t.import_sessions(vault)
    assert len(records(vault)) == 1
    assert "1 question(s) still running in an open session" in capsys.readouterr().out
    s.answer().cost(0.40).write(home)
    t.import_sessions(vault)
    assert [r["cost_usd"] for r in records(vault)] == [0.25, 0.15]


def test_a_local_only_read_in_an_open_session_is_stored_at_once_and_its_cost_added_later(vault, home, capsys):
    s = ask_session(vault).cost(0.25)
    s.prompt("still talking", prompt_id="open")
    s.tool("Bash", {"command": "cat company/q3.md"})
    s.write(home)
    t.import_sessions(vault)
    out = capsys.readouterr().out
    first, second = records(vault)
    assert second["local_only_read_on_frontier"] == ["company/q3.md"]
    assert second["cost_usd"] is None and second["cost_pending"] is True
    assert "1 question(s) stored without their cost" in out
    s.answer().cost(0.40).write(home)
    t.import_sessions(vault)
    assert "1 updated" in capsys.readouterr().out
    assert [(r["cost_usd"], r["cost_pending"]) for r in records(vault)] == [(0.25, False), (0.15, False)]


def test_a_question_followed_by_another_or_left_for_a_day_is_stored_without_cost(vault, home):
    import os
    s = ask_session(vault).cost(0.25)
    s.prompt("second", prompt_id="second")
    s.tool("Read", {"file_path": str(vault / "wiki/index.md")})
    s.answer()
    s.prompt("third", prompt_id="third")
    s.tool("Read", {"file_path": str(vault / "wiki/concepts/pricing.md")})
    path = s.write(home)  # killed here: no checkpoint will ever come
    t.import_sessions(vault)
    assert [r["cost_pending"] for r in records(vault)] == [False, True]  # the second: the session moved on
    day_ago = path.stat().st_mtime - 25 * 3600
    os.utime(path, (day_ago, day_ago))
    t.import_sessions(vault)
    assert [r["cost_pending"] for r in records(vault)] == [False, True, True]


def test_local_only_reads_on_frontier_are_recorded_and_printed_first(vault, home, capsys):
    s = Transcript(vault).prompt()
    s.tool("Read", {"file_path": str(vault / "me/profile.md")})
    s.tool("Read", {"file_path": str(vault / "me/preferences.md")})
    s.answer().cost(0.1).write(home)
    t.import_sessions(vault)
    [record] = records(vault)
    assert record["local_only_read_on_frontier"] == ["me/profile.md"]
    out = capsys.readouterr().out
    assert out.index("read a local-only file on the frontier profile") < out.index("imported 1 question")


def test_the_local_model_of_models_toml_marks_a_local_run(vault, home):
    (vault / ".harness").mkdir()
    (vault / ".harness/models.toml").write_text('[profiles.local]\nmodel = "qwen-local"\n[harnesses.claude]\n',
                                               encoding="utf-8")
    s = Transcript(vault, model="qwen-local").prompt()
    s.tool("Read", {"file_path": str(vault / "me/profile.md")})
    s.answer().cost(0.0).write(home)
    t.import_sessions(vault)
    [record] = records(vault)
    assert record["profile"] == "local" and record["local_only_read_on_frontier"] == []


def test_a_subagent_counts_for_the_question_that_started_it(vault, home):
    s = Transcript(vault).prompt()
    agent_id = s.tool("Agent", {"description": "Vault judgment pass", "prompt": "SECRET-PROMPT-WORDS"}, skill="lint")
    s.answer(skill="lint").cost(0.5)
    path = s.write(home)
    sub = Transcript(vault, session=s.session)
    sub.prompt_id = s.prompt_id
    sub.tool("Bash", {"command": "cat company/q3.md"}, usage={"input_tokens": 7, "output_tokens": 70})
    folder = path.with_suffix("") / "subagents"
    folder.mkdir(parents=True)
    events = [dict(e, isSidechain=True) for e in sub.events]
    (folder / "agent-a1.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    (folder / "agent-a1.meta.json").write_text(json.dumps({"toolUseId": agent_id}), encoding="utf-8")
    t.import_sessions(vault)
    [record] = records(vault)
    assert record["skill"] == "lint"
    assert "company/q3.md" in record["files_read"]
    assert record["local_only_read_on_frontier"] == ["company/q3.md"]
    assert record["tokens"]["output"] == 50 + 100 + 70


def test_sessions_are_matched_to_the_vault_by_their_folder(vault, home, tmp_path):
    sibling = tmp_path / "vault-legacy"  # same folder-name prefix, another folder
    sibling.mkdir()
    ask_session(sibling, session="aaaaaaaa-0000-0000-0000-000000000000").cost(0.1).write(home)
    inside = Transcript(vault / "wiki", session="bbbbbbbb-0000-0000-0000-000000000000").prompt()
    inside.tool("Bash", {"command": "cat concepts/pricing.md"})  # relative to the session's folder
    inside.answer().cost(0.1).write(home)
    t.import_sessions(vault)
    [record] = records(vault)
    assert record["run_id"].startswith("claude:bbbbbbbb")
    assert record["files_read"] == ["wiki/concepts/pricing.md"]


def test_the_shell_folder_is_the_one_claude_code_recorded(vault, home):
    s = Transcript(vault).prompt()
    s.tool("Bash", {"command": "cd company"})
    s.chdir(vault / "company")
    s.tool("Bash", {"command": "cat q3.md"})
    s.tool("Bash", {"command": "cd /tmp"}, result="Shell cwd was reset to " + str(vault))
    s.chdir(vault)
    s.tool("Bash", {"command": "cat wiki/index.md"})
    s.answer().cost(0.1).write(home)
    t.import_sessions(vault)
    [record] = records(vault)
    assert record["files_read"] == ["company/q3.md", "wiki/index.md"]


def test_a_failed_cd_does_not_hide_the_reads_after_it(vault, home):
    s = Transcript(vault).prompt()
    s.tool("Bash", {"command": "cd companies"}, result="Exit code 1\nno such directory", error=True)
    s.tool("Bash", {"command": "cat company/q3.md"})  # the shell stayed at the vault root
    s.tool("Bash", {"command": "cd companies; cat company/q3.md wiki/index.md"})
    s.answer().cost(0.1).write(home)
    t.import_sessions(vault)
    [record] = records(vault)
    assert record["files_read"] == ["company/q3.md", "wiki/index.md"]
    assert record["local_only_read_on_frontier"] == ["company/q3.md"]


def test_the_shell_folder_carries_in_an_eval_stream_only_after_a_command_that_worked(vault):
    lines = [{"type": "system", "subtype": "init", "model": "m", "claude_code_version": "9"}]
    for i, (command, error) in enumerate([("cd companies", True), ("cat company/q3.md", False),
                                          ("cd wiki", False), ("cat index.md", False)]):
        lines.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": command}}]}})
        lines.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": "Exit code 1" if error else "ok", "is_error": error}]}})
    run = t.ClaudeAdapter().parse("\n".join(json.dumps(x) for x in lines), vault)
    assert run.files_read == ["company/q3.md", "wiki/index.md"]


def test_a_question_asked_from_an_added_folder_still_counts(vault, home, tmp_path):
    other = tmp_path / "downloads"
    other.mkdir()
    s = Transcript(vault).prompt()
    s.tool("Bash", {"command": f"cd {other} && ls"})
    s.answer()
    s.chdir(other).prompt("second", prompt_id="second")
    s.tool("Read", {"file_path": str(vault / "company/q3.md")})
    s.answer().cost(0.6).write(home)
    t.import_sessions(vault)
    first, second = records(vault)
    assert second["local_only_read_on_frontier"] == ["company/q3.md"]
    assert first["cost_usd"] + second["cost_usd"] == pytest.approx(0.6, abs=1e-3)


def test_a_vault_whose_name_holds_an_emoji_is_found(home, make_vault):
    vault = make_vault({"company/q3.md": page("Q3", "note")}, name="\U0001F9E0 brain")
    assert t.project_dir_name(str(vault)).endswith("----brain")  # two UTF-16 units, as Claude Code counts
    s = Transcript(vault).prompt()
    s.tool("Read", {"file_path": str(vault / "company/q3.md")})
    s.answer().cost(0.1).write(home)
    t.import_sessions(vault)
    assert records(vault)[0]["local_only_read_on_frontier"] == ["company/q3.md"]


def test_a_session_in_a_folder_with_another_name_is_found_by_its_first_folder(vault, home):
    s = ask_session(vault).cost(0.1)
    s.write(home, folder="-shortened-name-1a2b3c")  # a long path Claude Code cut and hashed
    t.import_sessions(vault)
    assert len(records(vault)) == 1


def test_questions_copied_into_a_branched_session_are_not_imported_twice(vault, home):
    s = ask_session(vault).cost(0.3)
    s.write(home)
    branch = Transcript(vault, session="bbbbbbbb-0000-0000-0000-000000000000")
    branch.events = [dict(e, sessionId=branch.session, forkedFrom={"sessionId": s.session, "messageUuid": e.get("uuid")})
                     for e in s.events if e["type"] != "cost-state"]
    branch.n = s.n
    branch.prompt("on the branch", prompt_id="branch-q")
    branch.tool("Read", {"file_path": str(vault / "wiki/index.md")})
    branch.answer().cost(0.2).write(home)
    t.import_sessions(vault)
    ids = [r["run_id"] for r in records(vault)]
    assert len(ids) == 2 and ids[1].endswith(":branch-q")
    assert records(vault)[1]["cost_usd"] == 0.2


def test_free_text_tool_fields_never_reach_a_record(vault, home):
    s = Transcript(vault).prompt()
    s.tool("TaskCreate", {"subject": "SECRET-PROMPT-WORDS", "description": "SECRET-PROMPT-WORDS about Acme"})
    s.tool("Agent", {"description": "SECRET-PROMPT-WORDS", "prompt": "SECRET-PROMPT-WORDS"})
    s.tool("WebSearch", {"query": "SECRET-PROMPT-WORDS"})
    s.answer().cost(0.1).write(home)
    t.import_sessions(vault)
    text = (vault / t.TRACES_DIR / "2026-11.jsonl").read_text(encoding="utf-8")
    assert "SECRET-PROMPT-WORDS" not in text
    assert [c["tool"] for c in records(vault)[0]["tool_calls"]] == ["TaskCreate", "Agent", "WebSearch"]


def test_since_and_retention_skip_older_questions(vault, home, capsys):
    ask_session(vault, session="old00000-0000-0000-0000-000000000000", start="2026-10-01T09:00:00").cost(0.1).write(home)
    ask_session(vault, session="mid00000-0000-0000-0000-000000000000", start="2026-11-05T09:00:00").cost(0.1).write(home)
    ask_session(vault, session="new00000-0000-0000-0000-000000000000", start="2026-11-17T09:00:00").cost(0.1).write(home)
    t.import_sessions(vault, since=dt.date(2026, 11, 10))
    assert [r["run_id"][7:10] for r in records(vault)] == ["new"]
    t.import_sessions(vault)
    assert [r["run_id"][7:10] for r in records(vault)] == ["mid", "new"]
    assert "1 question(s) older than 30 days skipped" in capsys.readouterr().out


def test_expired_records_leave_a_weekly_aggregate_without_paths(vault, home, monkeypatch, capsys):
    ask_session(vault, session="mid00000-0000-0000-0000-000000000000", start="2026-11-05T09:00:00").cost(0.1).write(home)
    ask_session(vault, session="new00000-0000-0000-0000-000000000000", start="2026-11-17T09:00:00").cost(0.3).write(home)
    t.import_sessions(vault)
    monkeypatch.setenv("ANYBRAIN_TODAY", "2026-12-10")  # 35 days after the first question
    t.import_sessions(vault)
    assert [r["run_id"][7:10] for r in records(vault)] == ["new"]
    weekly = json.loads((vault / t.WEEKLY_FILE).read_text(encoding="utf-8"))
    [row] = weekly["weeks"]["2026-W45"]
    assert row["skill"] == "ask" and row["runs"] == 1 and row["cost_usd"] == 0.1 and row["files_read_median"] == 2
    assert "wiki/" not in (vault / t.WEEKLY_FILE).read_text(encoding="utf-8")
    assert "removed 1 question(s) older than 30 days" in capsys.readouterr().out
    monkeypatch.setenv("ANYBRAIN_TODAY", "2026-12-30")
    t.import_sessions(vault)
    weekly = json.loads((vault / t.WEEKLY_FILE).read_text(encoding="utf-8"))
    assert set(weekly["weeks"]) == {"2026-W45", "2026-W47"} and records(vault) == []


def test_a_record_of_a_week_already_summed_is_added_to_it_when_it_expires(vault, home, monkeypatch):
    early = ask_session(vault, session="aaaa0000-0000-0000-0000-000000000000", start="2026-11-09T09:00:00").cost(0.1)
    early.write(home)
    late = ask_session(vault, session="bbbb0000-0000-0000-0000-000000000000", start="2026-11-13T09:00:00")
    late_path = late.write(home)  # still open: stored later
    monkeypatch.setenv("ANYBRAIN_TODAY", "2026-11-19")
    t.import_sessions(vault)
    monkeypatch.setenv("ANYBRAIN_TODAY", "2026-12-10")  # the first record expires: week 46 is summed
    t.import_sessions(vault)
    late.cost(0.4).write(home)
    assert late_path.exists()
    t.import_sessions(vault)  # the late question arrives
    monkeypatch.setenv("ANYBRAIN_TODAY", "2026-12-14")  # and expires in turn
    t.import_sessions(vault)
    [row] = json.loads((vault / t.WEEKLY_FILE).read_text(encoding="utf-8"))["weeks"]["2026-W46"]
    assert row["runs"] == 2 and row["cost_usd"] == 0.5 and row["files_read"] == [2, 2]


def test_a_transcript_in_an_unknown_format_is_reported_not_recorded(vault, home, capsys):
    folder = home / "projects" / t.project_dir_name(str(vault))
    folder.mkdir(parents=True)
    (folder / "s.jsonl").write_text(json.dumps({"kind": "turn", "cwd": str(vault)}) + "\n", encoding="utf-8")
    assert t.import_sessions(vault) == 1
    assert records(vault) == []
    assert "1 transcript(s) in a format this reader does not know" in capsys.readouterr().out


def test_no_claude_folder_imports_nothing(vault, home, capsys):
    assert t.import_sessions(vault) == 0
    assert "imported 0 question(s) from 0 Claude Code session(s)" in capsys.readouterr().out


def test_project_folder_name_follows_claude_code():
    assert t.project_dir_name("/Users/me/Library/Mobile Documents/iCloud~md~obsidian/Documents/brain") == \
        "-Users-me-Library-Mobile-Documents-iCloud-md-obsidian-Documents-brain"


def test_cli_trace_import_and_bad_date(vault, home, capsys):
    ask_session(vault).cost(0.25).write(home)
    assert cli.main(["trace", str(vault), "import", "--since", "2026-11-01"]) == 0
    assert len(records(vault)) == 1
    assert cli.main(["trace", str(vault), "import", "--since", "yesterday"]) == 2
    assert re.search(r"--since needs a date", capsys.readouterr().err)
