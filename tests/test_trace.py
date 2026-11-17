"""Run traces (PRD 22.5): adapters, records, local-only reads on frontier, trace show."""
from __future__ import annotations

import json

import pytest

from anybrain import cli
from anybrain import trace as t
from conftest import page


def claude_stream(root, calls, answer="Done.\n\nfiles opened: 2", cost=0.12):
    """A Claude Code stream-json transcript: init, one tool_use and tool_result per call, result."""
    lines = [{"type": "system", "subtype": "init", "model": "test-model", "claude_code_version": "9.9.9", "cwd": str(root)}]
    for i, (tool, data, error) in enumerate(calls):
        lines.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": f"t{i}", "name": tool, "input": data}]}})
        result = {"type": "tool_result", "tool_use_id": f"t{i}", "content": error or "ok"}
        if error:
            result["is_error"] = True
        lines.append({"type": "user", "message": {"content": [result]}})
    lines.append({"type": "result", "subtype": "success", "is_error": False, "result": answer,
                  "total_cost_usd": cost, "duration_ms": 1234,
                  "usage": {"input_tokens": 10, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 5,
                            "output_tokens": 20}})
    return "\n".join(json.dumps(line) for line in lines) + "\n"


@pytest.fixture
def vault(make_vault):
    return make_vault({
        "wiki/concepts/a.md": page("A", "concept", confidence="low"),
        "me/preferences.md": page("Preferences", "profile"),
        "me/profile.md": page("Profile", "profile"),
        "projects/mission.md": page("Mission", "project", kind="mission"),
        "log.md": "2026-11-01 · init · created\n",
    })


def test_claude_adapter_reads_writes_denials_tokens_and_cost(vault):
    stream = claude_stream(vault, [
        ("Read", {"file_path": str(vault / "wiki/concepts/a.md")}, None),
        ("Read", {"file_path": str(vault / "me/profile.md")}, "File is in a directory that is denied by your permission settings."),
        ("Bash", {"command": "cat me/preferences.md; echo done >> log.md"}, None),
        ("Edit", {"file_path": str(vault / "wiki/concepts/a.md")}, None),
        ("Bash", {"command": "rm -rf /"}, "Some unrelated failure"),
    ])
    run = t.ClaudeAdapter().parse(stream, vault)
    assert run.ok and run.model == "test-model" and run.harness_version == "9.9.9"
    assert run.files_read == ["wiki/concepts/a.md", "me/preferences.md"]
    assert run.files_written == ["log.md", "wiki/concepts/a.md"]
    assert [c.result for c in run.tool_calls] == ["ok", "denied", "ok", "ok", "error"]
    assert run.tokens == {"input": 115, "output": 20, "cached": 100}
    assert run.cost_usd == 0.12


def test_record_flags_local_only_reads_on_frontier(vault):
    stream = claude_stream(vault, [
        ("Read", {"file_path": str(vault / "me/profile.md")}, None),
        ("Read", {"file_path": str(vault / "projects/mission.md")}, None),
        ("Read", {"file_path": str(vault / "me/preferences.md")}, None),
    ], answer="Answer.\nfiles opened: 1")
    run = t.ClaudeAdapter().parse(stream, vault)
    record = t.build_record(run, vault, "frontier", "eval", "2026-11-17T09:00:00Z", case_id="g030")
    assert record["local_only_read_on_frontier"] == ["me/profile.md", "projects/mission.md"]
    assert record["self_reported_files_opened"] == 1 and len(record["files_read"]) == 3
    assert t.build_record(run, vault, "local", "eval", "x")["local_only_read_on_frontier"] == []
    for forbidden in ("Answer.", "Profile"):
        assert forbidden not in json.dumps(record), "a trace never holds the answer or file content"


def test_denied_reads_are_not_counted_as_reads(vault):
    stream = claude_stream(vault, [("Read", {"file_path": str(vault / "me/profile.md")},
                                    "File is in a directory that is denied by your permission settings.")])
    record = t.build_record(t.ClaudeAdapter().parse(stream, vault), vault, "frontier", "eval", "x")
    assert record["files_read"] == [] and record["local_only_read_on_frontier"] == []
    assert record["denied"] == [{"tool": "Read", "target": "me/profile.md", "result": "denied"}]


def test_files_from_shell(vault):
    read, written = t.files_from_shell("cat -n me/profile.md wiki/concepts/a.md | head -5 > /tmp/x; grep -r foo me", vault)
    assert read == ["me/profile.md", "wiki/concepts/a.md"]
    assert written == ["/tmp/x"]


def test_claude_output_args():
    assert t.ClaudeAdapter().output_args("frontier", None) == ["--output-format", "stream-json", "--verbose"]
    assert t.ClaudeAdapter().output_args("frontier", 0.5)[-2:] == ["--max-budget-usd", "0.50"]


def test_opencode_adapter_reads_text_and_known_tool_events(vault):
    events = [
        {"type": "tool", "part": {"type": "tool", "tool": "read", "state": {"status": "completed",
                                  "input": {"filePath": str(vault / "me/profile.md")}}}},
        {"type": "text", "part": {"type": "text", "text": "Final answer"}},
    ]
    run = t.OpenCodeAdapter().parse("\n".join(json.dumps(e) for e in events), vault)
    assert run.answer == "Final answer" and run.files_read == ["me/profile.md"] and run.parsed_tool_calls
    assert t.OpenCodeAdapter().output_args("local", None) == ["--format", "json", "--agent", "local"]
    blind = t.OpenCodeAdapter().parse(json.dumps({"type": "text", "part": {"type": "text", "text": "x"}}), vault)
    assert not blind.parsed_tool_calls


def test_unknown_harness():
    from anybrain import vault as v

    with pytest.raises(v.VaultError, match="no trace adapter"):
        t.adapter_for("aider")


def test_trace_show_lists_runs_and_puts_local_only_reads_first(vault, capsys):
    stream_ok = claude_stream(vault, [("Read", {"file_path": str(vault / "wiki/concepts/a.md")}, None)])
    stream_bad = claude_stream(vault, [("Read", {"file_path": str(vault / "me/profile.md")}, None)], answer="x")
    ok = t.build_record(t.ClaudeAdapter().parse(stream_ok, vault), vault, "frontier", "eval", "2026-11-17T10:00:00Z", case_id="g001")
    bad = t.build_record(t.ClaudeAdapter().parse(stream_bad, vault), vault, "frontier", "eval", "2026-11-17T09:00:00Z", case_id="g030")
    (vault / ".harness" / "baseline").mkdir(parents=True)
    (vault / ".harness" / "baseline" / "run-claude-frontier-2026-11-17.json").write_text(
        json.dumps({"results": [{"trace": ok}, {"trace": bad}]}))
    assert cli.main(["trace", str(vault), "show"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "1 run(s) read a local-only file on the frontier profile:"
    assert "g030" in out[1] and out[2] == "  LOCAL-ONLY READ ON FRONTIER: me/profile.md"
    assert "g001" in out[3] and "0.12 USD" in out[3]
    assert "said files opened: 2, measured: 1" in out[4]


def test_trace_show_with_no_traces(vault, capsys):
    assert cli.main(["trace", str(vault), "show"]) == 0
    assert "no traces yet" in capsys.readouterr().out
