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
    read, written, searched = t.files_from_shell(
        "cat -n me/profile.md wiki/concepts/a.md | head -5 > /tmp/x; grep -r foo me", vault)
    assert read == ["me/profile.md", "wiki/concepts/a.md"]
    assert written == ["/tmp/x"]
    assert [x.folder for x in searched] == ["me"]


@pytest.mark.parametrize("command", [
    'grep -n -E "Mission|pricing" projects/mission.md',  # a pipe inside quotes is not a pipe
    "cat wiki/../projects/mission.md",
    "cat ./wiki/concepts/../../projects/mission.md",
    "cd wiki && cat ../projects/mission.md",
    "echo start\ncat projects/mission.md",
    "wc -l < projects/mission.md",
    "python3 -c \"print(open('projects/mission.md').read())\"",
    "git show HEAD:projects/mission.md",
    "awk '{print}' projects/mission.md 2>&1",
    "cat 'projects/mission.md",  # unbalanced quote: plain split
])
def test_shell_reads_are_seen_in_every_form(vault, command):
    read, written, _ = t.files_from_shell(command, vault)
    assert read == ["projects/mission.md"], command
    assert "1" not in written


def test_listing_or_naming_a_file_is_not_a_read(vault):
    read, _, searched = t.files_from_shell("ls me/profile.md; echo me/profile.md; anybrain neighbors . me/profile.md", vault)
    assert read == [] and searched == []


def test_recursive_search_counts_local_only_files_under_the_folder(vault):
    _, _, searched = t.files_from_shell("grep -rn Mission .", vault)
    assert [x.folder for x in searched] == ["."]
    found = t.local_only_reads(vault, [], searched=searched)
    assert "me/profile.md" in found and "projects/mission.md" in found
    assert "me/preferences.md" not in found and "wiki/concepts/a.md" not in found
    assert [x.folder for x in t.files_from_shell("rg Mission", vault)[2]] == ["."]
    assert t.files_from_shell("grep -n Mission wiki", vault)[2] == []
    assert t.files_from_shell("grep -rn me wiki", vault)[2][0].folder == "wiki", "the pattern is not a folder"


def test_search_filters_narrow_what_counts(vault):
    for rel in ("me/index.md", "wiki/index.md", ".harness/.env"):
        (vault / rel).parent.mkdir(parents=True, exist_ok=True)
        (vault / rel).write_text("x\n")

    def local(command):
        return t.local_only_reads(vault, [], searched=t.files_from_shell(command, vault)[2])

    # the command of a real eval case (D036): only index files were opened
    assert local('grep -ril "languag" --include=index.md .') == ["me/index.md"]
    assert local("grep -r x --include '*.md' --exclude-dir me .") == ["projects/mission.md"]
    assert ".harness/.env" in local("grep -r x .")
    assert ".harness/.env" not in local("rg x") and ".harness/.env" in local("rg --hidden x")
    assert local("rg x -g '!me/**' -g '!projects/**'") == []


def test_failed_and_refused_shell_commands(vault):
    stream = claude_stream(vault, [
        ("Bash", {"command": "cat me/profile.md; ls nothing"}, "Exit code 2\nProfile text\nls: nothing: No such file"),
        ("Bash", {"command": "cat projects/mission.md"}, "This command requires approval"),
        ("Bash", {"command": "cat $X"}, "A variable in this command can't be checked before it runs"),
        ("Bash", {"command": "cat wiki/concepts/a.md; cat locked"}, "Exit code 1\ncat: locked: Permission denied"),
    ])
    run = t.ClaudeAdapter().parse(stream, vault)
    assert [c.result for c in run.tool_calls] == ["error", "denied", "denied", "error"]
    assert run.files_read == ["me/profile.md", "wiki/concepts/a.md"]
    record = t.build_record(run, vault, "frontier", "eval", "x")
    assert record["local_only_read_on_frontier"] == ["me/profile.md"]
    assert len(record["denied"]) == 2


def test_paths_into_the_real_vault_dotdot_and_symlinks_are_routed(vault, tmp_path):
    copy = tmp_path / "copy"
    import shutil
    shutil.copytree(vault, copy, symlinks=True)
    (copy / "wiki" / "mine").symlink_to(vault / "me")  # absolute link back into the real vault
    (copy / "wiki" / "mine2").symlink_to("../me")
    stream = claude_stream(copy, [
        ("Read", {"file_path": str(vault / "me/profile.md")}, None),
        ("Read", {"file_path": "wiki/../projects/mission.md"}, None),
        ("Read", {"file_path": str(copy / "wiki/mine/profile.md")}, None),
        ("Bash", {"command": "head wiki/mine2/profile.md"}, None),
    ])
    run = t.ClaudeAdapter().parse(stream, copy, origin=vault)
    assert run.files_read[:2] == ["me/profile.md", "projects/mission.md"]
    record = t.build_record(run, copy, "frontier", "eval", "x", origin=vault)
    # me/ is routed local, the mission page is confidential, and both links resolve into me/
    assert record["local_only_read_on_frontier"] == [
        "me/profile.md", "projects/mission.md", "wiki/mine/profile.md", "wiki/mine2/profile.md"]


def test_merge_records_keeps_every_attempt(vault):
    leak = t.build_record(t.ClaudeAdapter().parse(claude_stream(vault, [
        ("Read", {"file_path": str(vault / "me/profile.md")}, None)], cost=0.3), vault), vault, "frontier", "eval", "a")
    clean = t.build_record(t.ClaudeAdapter().parse(claude_stream(vault, [], cost=0.1), vault),
                           vault, "frontier", "eval", "b")
    merged = t.merge_records([leak, clean])
    assert merged["attempts"] == 2 and merged["cost_usd"] == 0.4
    assert merged["local_only_read_on_frontier"] == ["me/profile.md"]
    assert merged["files_read"] == ["me/profile.md"] and merged["started"] == "a"
    assert merged["tokens"]["input"] == 230
    assert t.merge_records([clean]) is clean


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


def test_writes_into_the_real_vault_are_escapes(vault, tmp_path):
    import shutil
    copy = tmp_path / "copy"
    shutil.copytree(vault, copy)
    stream = claude_stream(copy, [
        ("Write", {"file_path": str(vault / "wiki/x.md")}, None),
        ("Write", {"file_path": str(copy / "wiki/y.md")}, None),
        ("Bash", {"command": f"echo hi > {vault}/log.md; cd wiki && echo ok > z.md"}, None),
    ])
    run = t.ClaudeAdapter().parse(stream, copy, origin=vault)
    assert run.escaped_writes == [str(vault / "wiki/x.md"), str(vault / "log.md")]
    assert t.build_record(run, copy, "frontier", "eval", "x", origin=vault)["escaped_writes"] == run.escaped_writes


@pytest.fixture
def copy_and_origin(vault, tmp_path):
    import shutil
    copy = tmp_path / "copy"
    shutil.copytree(vault, copy)
    return copy, vault


@pytest.mark.parametrize("command", [
    "cp index.md {o}/wiki/b.md", "mv log.md {o}/log.md", "rm {o}/log.md", "sed -i s/a/b/ {o}/log.md",
    "touch {o}/new.md", "python3 -c \"open('{o}/log.md', 'w').write('x')\"", "ls {o}", "cd {o} && rm log.md",
    "cd .. && rm {name}/log.md",
])
def test_any_command_naming_the_real_vault_is_an_escape(copy_and_origin, command):
    copy, origin = copy_and_origin
    # the copy and the real vault are siblings here, so `..` from the copy reaches the real vault by name
    command = command.format(o=origin, name=origin.name)
    assert t.shell_escapes(command, copy, origin), command


@pytest.mark.parametrize("command", [
    "cat > notes.md <<'EOF'\n{o}/log.md is mentioned in the text\nEOF\necho done > log.md",
    "(cd /tmp && echo x > y.txt); echo z > out.md",
    "cd - && echo z > out.md",
    "echo x > log.md; grep -r foo .",
])
def test_writes_inside_the_copy_are_not_escapes(copy_and_origin, command):
    copy, origin = copy_and_origin
    assert t.shell_escapes(command.format(o=origin), copy, origin) == [], command


def test_a_link_in_the_copy_into_the_real_vault_is_an_escape(copy_and_origin):
    copy, origin = copy_and_origin
    (copy / "wiki" / "mine").symlink_to(origin / "me")
    assert t.escapes(str(copy / "wiki" / "mine" / "x.md"), copy, origin, str(copy))
    assert t.shell_escapes("echo x > wiki/mine/x.md", copy, origin)
    stream = claude_stream(copy, [("NotebookEdit", {"notebook_path": str(origin / "n.ipynb")}, None)])
    assert t.ClaudeAdapter().parse(stream, copy, origin=origin).escaped_writes == [str(origin / "n.ipynb")]


def test_opencode_escapes_and_shell_reads(copy_and_origin):
    copy, origin = copy_and_origin
    events = [
        {"type": "tool", "part": {"type": "tool", "tool": "write", "state": {"status": "completed",
                                  "input": {"filePath": str(origin / "wiki/x.md")}}}},
        {"type": "tool", "part": {"type": "tool", "tool": "bash", "state": {"status": "completed",
                                  "input": {"command": "cat me/profile.md; cp index.md " + str(origin / "y.md")}}}},
        {"type": "text", "part": {"type": "text", "text": "done"}},
    ]
    run = t.OpenCodeAdapter().parse("\n".join(json.dumps(e) for e in events), copy, origin=origin)
    assert run.escaped_writes == [str(origin / "wiki/x.md"), str(origin / "y.md")]
    assert "me/profile.md" in run.files_read


def test_heredocs_run_by_an_interpreter_are_read(copy_and_origin):
    copy, origin = copy_and_origin
    command = f"python3 - <<'EOF'\nopen('{origin}/log.md', 'w').write('x')\nprint(open('me/profile.md').read())\nEOF"
    assert t.shell_escapes(command, copy, origin)
    assert "me/profile.md" in t.files_from_shell(command, copy)[0]
    piped = f"cat <<EOF | sh\nrm {origin}/log.md\nEOF"
    assert t.shell_escapes(piped, copy, origin)


def test_a_vault_path_with_spaces_is_seen_in_code(tmp_path):
    import shutil
    origin = tmp_path / "Mobile Documents" / "brain"
    origin.mkdir(parents=True)
    (origin / "log.md").write_text("x\n")
    copy = tmp_path / "copy"
    shutil.copytree(origin, copy)
    command = f"python3 -c \"open('{origin}/log.md', 'a').write('y')\""
    assert t.shell_escapes(command, copy, origin)
