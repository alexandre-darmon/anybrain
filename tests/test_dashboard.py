"""Session figures in `anybrain stats` (22.6) and the generated dashboard page (22.7)."""
from __future__ import annotations

import json
import shutil

import pytest

from anybrain import cli, lint, stats
from anybrain import trace as t
from anybrain import vault as v
from anybrain.index import HEADER
from conftest import EXAMPLE


def record(started, skill="ask", files=2, cost=0.2, local=(), shared=False, said=None, trigger="session"):
    return {"run_id": f"claude:s:{started}:{skill}", "started": started, "trigger": trigger, "skill": skill,
            "harness": "claude", "profile": "frontier", "cost_usd": cost, "cost_shared": shared,
            "tokens": {"input": 100, "output": 10, "cached": 50},
            "files_read": [f"wiki/p{i}.md" for i in range(files)], "files_written": [], "denied": [],
            "local_only_read_on_frontier": list(local), "self_reported_files_opened": said}


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("ANYBRAIN_TODAY", "2026-11-19")
    target = tmp_path / "vault"
    shutil.copytree(EXAMPLE, target)
    return target


def write_records(vault, records):
    folder = vault / t.TRACES_DIR
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "2026-11.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def test_stats_prints_cost_per_skill_and_week_and_files_per_ask(vault, capsys):
    write_records(vault, [
        record("2026-11-10T09:00:00Z", files=4, cost=0.10),
        record("2026-11-11T09:00:00Z", files=6, cost=0.30, said=3),
        record("2026-11-17T09:00:00Z", skill="ingest", files=9, cost=0.40, shared=True),
        record("2026-11-18T09:00:00Z", files=12, cost=0.20, local=["me/profile.md"]),
        record("2026-11-18T10:00:00Z", files=30, cost=9.0, trigger="eval"),  # a test, not use
    ])
    assert cli.main(["stats", str(vault)]) == 0
    out = capsys.readouterr().out
    section = out[out.index("sessions (run traces, 22.6)"):]
    assert "local-only files read on the frontier profile: 1 (target 0)" in section
    assert "2026-11-18  ask: me/profile.md" in section
    rows = [line.split() for line in section.splitlines()]
    assert ["2026-W46", "ask", "2", "0.40", "5"] in rows
    assert ["2026-W47", "ask", "1", "0.20", "12"] in rows
    assert ["ingest", "1", "0.40", "9"] in rows
    assert "median files read per ask: 6 over 3 run(s), within target (at most 8)" in section
    assert "1 ask run(s) said a different `files opened` than measured" in section
    assert "1 run(s) share their session's measured cost by tokens" in section
    assert "9.00" not in section


def test_stats_without_session_traces_says_how_to_get_them(vault, capsys):
    assert cli.main(["stats", str(vault)]) == 0
    assert "sessions: no run traces in these weeks; `anybrain trace import`" in capsys.readouterr().out


def test_weeks_kept_only_as_aggregates_are_shown(vault, capsys):
    (vault / t.TRACES_DIR).mkdir(parents=True)
    (vault / t.WEEKLY_FILE).write_text(json.dumps({"weeks": {"2026-W44": [
        {"skill": "ask", "runs": 5, "cost_usd": 1.5, "files_read_median": 7, "local_only_reads": 2}]}}), encoding="utf-8")
    assert cli.main(["stats", str(vault)]) == 0
    out = capsys.readouterr().out
    assert ["2026-W44", "ask", "5", "1.50", "7"] in [line.split() for line in out.splitlines()]
    assert "local-only files read on the frontier profile: 2 (target 0)" in out


def test_dashboard_is_written_with_every_section(vault, capsys):
    write_records(vault, [record("2026-11-17T09:00:00Z", files=5, cost=0.25, local=["company/q3.md"]),
                          record("2026-11-18T09:00:00Z", skill="ingest", cost=0.50)])
    baseline = vault / ".harness/baseline"
    baseline.mkdir(parents=True)
    (baseline / "run-claude-frontier-2026-11-18.json").write_text(json.dumps({
        "date": "2026-11-18", "harness": "claude", "profile": "frontier", "pass_rate": 0.8, "passed": 8, "scored": 10,
        "cost_usd": 2.0, "by_tag": {"navigation": 1.0, "safety": 0.5}, "local_only_reads": ["me/profile.md"],
        "gates": [{"name": "recall >= 0.90", "value": 1.0, "pass": True, "shown": "1.00"},
                  {"name": "median files read <= 8", "value": 9, "pass": False, "shown": "9"}]}), encoding="utf-8")
    assert cli.main(["stats", str(vault), "--dashboard"]) == 0
    text = (vault / stats.DASHBOARD).read_text(encoding="utf-8")
    assert text.startswith(HEADER)
    sections = ["## Local-only files read on the frontier profile", "## Cost per skill and per week",
                "## Files read per ask", "## Eval runs", "## Graph", "## Usage from log.md", "## Rule proposals"]
    assert [text.index(s) for s in sections] == sorted(text.index(s) for s in sections)  # local-only reads first
    assert "| 2026-11-17 | session | ask | company/q3.md |" in text
    assert "| 2026-11-18 | eval claude | golden set | me/profile.md |" in text
    assert "| 2026-W47 | ingest | 1 | 0.50 | " + "█" * stats.BAR_WIDTH + " |" in text
    assert "| 2026-11-18 | claude | frontier | 80% | 8/10 | 2.00 | 1/2 |" in text
    assert "| median files read <= 8 | 9 | no |" in text
    assert "| 22 | 22 | 0 | 0 |" in text  # vault.example's graph
    assert "wrote journal/dashboard.md" in capsys.readouterr().out


def test_dashboard_is_rewritten_but_never_over_a_hand_written_page(vault, capsys):
    assert cli.main(["stats", str(vault), "--dashboard"]) == 0
    assert cli.main(["stats", str(vault), "--dashboard"]) == 0  # its own page: rewritten
    path = vault / stats.DASHBOARD
    path.write_text("# My own dashboard\n", encoding="utf-8")
    assert cli.main(["stats", str(vault), "--dashboard"]) == 1
    assert path.read_text(encoding="utf-8") == "# My own dashboard\n"
    assert "kept journal/dashboard.md: it was not generated by anybrain" in capsys.readouterr().out


def test_dashboard_never_writes_through_a_link(vault, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text(HEADER + "\n", encoding="utf-8")
    (vault / stats.DASHBOARD).symlink_to(outside)
    assert cli.main(["stats", str(vault), "--dashboard"]) == 2
    assert outside.read_text(encoding="utf-8") == HEADER + "\n"


def test_the_dashboard_is_not_a_page(vault, capsys):
    assert cli.main(["stats", str(vault), "--dashboard"]) == 0
    assert not v.is_page_path(stats.DASHBOARD)
    capsys.readouterr()
    assert lint.run(vault, []) == 0  # no schema error for a generated page without frontmatter
    assert cli.main(["index", str(vault), "--check"]) == 0  # and the journal index does not list it


def test_the_dataview_page_can_be_copied_into_a_vault(vault):
    template = v.framework_root() / "templates" / "obsidian" / "dashboard-vault.md"
    shutil.copy(template, vault / "journal" / "vault-figures.md")
    assert lint.run(vault, ["schema"]) == 0
    text = template.read_text(encoding="utf-8")
    assert text.count("```dataview") == 6 and "anybrain stats --dashboard" in text
