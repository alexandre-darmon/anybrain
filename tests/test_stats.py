"""stats: usage counts from log.md per week and per skill (PRD 6, 9.3)."""
from __future__ import annotations

import datetime as dt

from anybrain import cli, stats
from conftest import EXAMPLE

DOT = " · "


def line(date, skill):
    return f"{date}{DOT}{skill}{DOT}did something{DOT}library/x.md\n"


def test_counts_per_iso_week_and_skill(make_vault):
    root = make_vault({"log.md": "".join([
        line("2026-11-02", "ingest"), line("2026-11-03", "ingest"), line("2026-11-04", "ask"),
        line("2026-11-09", "ask"), line("2026-11-16", "lint"), line("2026-10-01", "ingest"),
    ])})
    entries, skipped = stats.read_log(root)
    rows = stats.counts_by_week(entries, 3, dt.date(2026, 11, 16))
    assert [start.isoformat() for start, _ in rows] == ["2026-11-02", "2026-11-09", "2026-11-16"]
    assert [counts for _, counts in rows] == [{"ingest": 2, "ask": 1}, {"ask": 1}, {"lint": 1}]
    assert skipped == 0


def test_output_marks_weeks_that_meet_the_target(make_vault, capsys):
    root = make_vault({"log.md": "".join(
        [line("2026-11-16", "ingest")] * 2 + [line("2026-11-16", "ask")] * 3 + [line("2026-11-10", "ask")])})
    assert cli.main(["stats", str(root), "--weeks", "2"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].split() == ["week", "ask", "ingest", "total"]
    assert out[1].split() == ["2026-W46", "1", "0", "1"]
    assert out[2].split() == ["2026-W47", "3", "2", "5", "target", "met"]
    assert out[3] == "usage target per week (ingest at least 2, ask at least 3): met in 1 of 2 week(s)"


def test_kill_criterion_after_four_weak_weeks(make_vault, capsys):
    root = make_vault({"log.md": line("2026-11-16", "ingest")})
    assert cli.main(["stats", str(root)]) == 0
    assert "kill criterion (PRD section 6)" in capsys.readouterr().out


def test_no_kill_criterion_when_one_week_has_two_ingests(make_vault, capsys):
    root = make_vault({"log.md": line("2026-11-03", "ingest") + line("2026-11-04", "ingest")})
    cli.main(["stats", str(root)])
    assert "kill criterion" not in capsys.readouterr().out


def test_malformed_lines_are_counted_and_skipped(make_vault, capsys):
    root = make_vault({"log.md": "not a log line\n" + line("2026-11-16", "ask")})
    cli.main(["stats", str(root), "--weeks", "1"])
    assert "1 line(s) of log.md do not follow" in capsys.readouterr().out


def test_example_vault_and_missing_log(make_vault, capsys):
    assert cli.main(["stats", str(EXAMPLE), "--weeks", "3"]) == 0
    assert "2026-W45" in capsys.readouterr().out
    assert cli.main(["stats", str(make_vault({}))]) == 0
    assert cli.main(["stats", str(EXAMPLE), "--weeks", "0"]) == 2
