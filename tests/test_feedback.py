"""Learned rules from corrections (PRD 16, acceptance of 16.5)."""
from __future__ import annotations

import datetime as dt

import pytest

from anybrain import cli, feedback, lint
from conftest import page

TODAY = dt.date(2026, 11, 16)


def log(*lines):
    return "".join(f"{line}\n" for line in lines)


def entry(date, topic, skill="ingest", note="owner corrected it"):
    return f"{date} · topic:{topic} · skill:{skill} · {note}"


PREFS_NO_RULE = page("Preferences", "profile", body="## Owner rules\n\n## Learned rules\n")
PREFS_WITH_RULE = page("Preferences", "profile", body=(
    "## Owner rules\n\n## Learned rules\n"
    "- Never use em dashes in any page or answer. (topic:no-em-dash · learned 2026-11-10)\n"))


@pytest.fixture
def three(make_vault):
    return make_vault({
        "me/preferences.md": PREFS_NO_RULE,
        ".harness/feedback.log": log(entry("2026-11-01", "no-em-dash"), entry("2026-11-03", "no-em-dash", "ask"),
                                     entry("2026-11-05", "no-em-dash", note="third time")),
    })


# ---------------------------------------------------------------- acceptance of 16

def test_three_corrections_make_one_proposal(three):
    (proposal,) = feedback.proposals(three, TODAY)
    assert proposal.topic == "no-em-dash" and proposal.count == 3
    assert [c.date.isoformat() for c in proposal.evidence] == ["2026-11-05", "2026-11-03", "2026-11-01"]


def test_no_proposal_once_a_rule_carries_the_topic(three):
    (three / "me" / "preferences.md").write_text(PREFS_WITH_RULE)
    assert feedback.proposals(three, TODAY) == []
    assert feedback.rules_not_followed(three, TODAY) == []


def test_a_fourth_correction_after_the_rule_is_rule_not_followed(three):
    (three / "me" / "preferences.md").write_text(PREFS_WITH_RULE)
    with open(three / ".harness" / "feedback.log", "a") as handle:
        handle.write(entry("2026-11-14", "no-em-dash", "draft", "em dash again") + "\n")
    (item,) = feedback.rules_not_followed(three, TODAY)
    assert item.rule.topic == "no-em-dash"
    assert [c.note for c in item.corrections] == ["em dash again"]
    assert not item.reword
    messages = [str(f) for f in lint.check_feedback(lint.Context(three))]
    assert messages == ["warning feedback: rule topic:no-em-dash not followed: "
                        "2026-11-14 · topic:no-em-dash · skill:draft · em dash again"]


def test_two_violations_in_30_days_suggest_rewording(three):
    (three / "me" / "preferences.md").write_text(PREFS_WITH_RULE)
    with open(three / ".harness" / "feedback.log", "a") as handle:
        handle.write(entry("2026-11-12", "no-em-dash") + "\n" + entry("2026-11-15", "no-em-dash") + "\n")
    (item,) = feedback.rules_not_followed(three, TODAY)
    assert item.reword
    assert "consider rewording it" in str(lint.check_feedback(lint.Context(three))[0])


# ---------------------------------------------------------------- counting rules

def test_two_corrections_are_not_enough(make_vault):
    root = make_vault({".harness/feedback.log": log(entry("2026-11-01", "x"), entry("2026-11-02", "x"))})
    assert feedback.proposals(root, TODAY) == []


def test_only_the_last_90_days_count(make_vault):
    # 2026-11-16 minus 90 days is 2026-08-18: that day counts, the day before does not
    inside = make_vault({".harness/feedback.log": log(
        entry("2026-08-17", "x"), entry("2026-08-18", "x"), entry("2026-09-01", "x"), entry("2026-11-01", "x"))},
        name="inside")
    (proposal,) = feedback.proposals(inside, TODAY)
    assert proposal.count == 3
    assert min(c.date for c in proposal.evidence) == dt.date(2026, 8, 18)
    outside = make_vault({".harness/feedback.log": log(
        entry("2026-08-16", "x"), entry("2026-08-17", "x"), entry("2026-11-01", "x"))}, name="outside")
    assert feedback.proposals(outside, TODAY) == []


def test_rule_topic_must_match_exactly(make_vault):
    root = make_vault({
        "me/preferences.md": page("Preferences", "profile", body="- Rule. (topic:no-em-dash-ever · learned 2026-11-10)\n"),
        ".harness/feedback.log": log(*(entry(f"2026-11-0{i}", "no-em-dash") for i in (1, 2, 3))),
    })
    assert [p.topic for p in feedback.proposals(root, TODAY)] == ["no-em-dash"]


def test_owner_rule_without_learned_date_stops_proposals_but_not_violations(make_vault):
    root = make_vault({
        "me/preferences.md": page("Preferences", "profile", body="## Owner rules\n- Short answers. (topic:short)\n"),
        ".harness/feedback.log": log(*(entry(f"2026-11-0{i}", "short") for i in (1, 2, 3, 4))),
    })
    assert feedback.proposals(root, TODAY) == []
    assert feedback.rules_not_followed(root, TODAY) == []


def test_malformed_lines_are_reported_not_counted(make_vault):
    root = make_vault({".harness/feedback.log": "# header\n" + log("garbage", entry("2026-13-01", "x"), entry("2026-11-01", "x"))})
    corrections, bad = feedback.read_log(root)
    assert [c.topic for c in corrections] == ["x"]
    assert bad == [2, 3]


# ---------------------------------------------------------------- commands

def test_add_appends_the_exact_format(make_vault, capsys):
    root = make_vault({})
    assert cli.main(["feedback", str(root), "add", "--topic", "no-em-dash", "--skill", "ingest",
                     "--note", "owner asked:\nno em dashes in summaries"]) == 0
    line = "2026-11-16 · topic:no-em-dash · skill:ingest · owner asked: no em dashes in summaries"
    assert (root / ".harness" / "feedback.log").read_text() == line + "\n"
    assert f"wrote .harness/feedback.log: {line}" in capsys.readouterr().out
    cli.main(["feedback", str(root), "add", "--topic", "no-em-dash", "--skill", "ask", "--note", "again"])
    assert (root / ".harness" / "feedback.log").read_text().count("\n") == 2


@pytest.mark.parametrize("args, message", [
    (["--topic", "No Em Dash", "--skill", "ingest", "--note", "x"], "topic must be kebab-case"),
    (["--topic", "x", "--skill", "Ingest Skill", "--note", "x"], "skill must be a skill name"),
    (["--topic", "x", "--skill", "ingest", "--note", "   "], "note is empty"),
    (["--topic", "x", "--skill", "ingest"], "feedback add needs --note"),
])
def test_add_rejects_bad_input(make_vault, capsys, args, message):
    root = make_vault({})
    assert cli.main(["feedback", str(root), "add", *args]) == 2
    assert message in capsys.readouterr().err
    assert not (root / ".harness" / "feedback.log").exists()


def test_topics_and_proposals_commands(fixture_vault, capsys):
    root = fixture_vault("feedback")
    assert cli.main(["feedback", str(root), "topics"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].split() == ["no-em-dash", "5", "(rule", "in", "me/preferences.md)"]
    assert cli.main(["feedback", str(root), "proposals"]) == 0
    out = capsys.readouterr().out
    assert "topic:short-summaries  3 corrections in 90 days, no rule in me/preferences.md" in out
    assert "old-topic" not in out


def test_feedback_reads_vault_from_environment(make_vault, monkeypatch, capsys):
    root = make_vault({})
    monkeypatch.setenv("ANYBRAIN_VAULT", str(root))
    assert cli.main(["feedback", "topics"]) == 0
    assert "no topics yet" in capsys.readouterr().out
    assert cli.main(["feedback", "nonsense"]) == 2
