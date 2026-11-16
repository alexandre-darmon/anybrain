"""Learned rules from corrections (PRD section 16). Counting is deterministic; the LLM only drafts wording.

Log format, one line per correction, in `.harness/feedback.log`:
    2026-11-16 · topic:no-em-dash · skill:ingest · owner asked: no em dashes in summaries
A rule in `me/preferences.md` carries `topic:<slug>` and, when the agent appended it, `learned YYYY-MM-DD`.
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from pathlib import Path

from . import vault as v

LOG = ".harness/feedback.log"
PREFERENCES = "me/preferences.md"
THRESHOLD = 3  # corrections on one topic before a rule is proposed (16.2)
WINDOW_DAYS = 90  # proposals count corrections over the last 90 days (16.2)
REWORD_DAYS = 30  # two "rule not followed" in 30 days: suggest rewording (16.4)
SEP = " · "

LINE_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})\s*·\s*topic:(?P<topic>[a-z0-9]+(?:-[a-z0-9]+)*)\s*·\s*"
    r"skill:(?P<skill>[^\s·]+)\s*·\s*(?P<note>.*)$"
)
RULE_TOPIC_RE = re.compile(r"topic:([a-z0-9]+(?:-[a-z0-9]+)*)(?![a-z0-9-])")
LEARNED_RE = re.compile(r"learned (\d{4}-\d{2}-\d{2})")


@dataclass(frozen=True)
class Correction:
    date: _dt.date
    topic: str
    skill: str
    note: str
    line: str


@dataclass(frozen=True)
class Rule:
    topic: str
    learned: _dt.date | None
    line: str


def read_log(vault: Path) -> tuple[list[Correction], list[int]]:
    """Corrections in file order, and the line numbers of malformed lines."""
    path = vault / LOG
    if not path.is_file():
        return [], []
    corrections, bad = [], []
    for number, raw in enumerate(v.read_text(path).split("\n"), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = LINE_RE.match(line)
        date = v.as_date(match.group("date")) if match else None
        if not match or date is None:
            bad.append(number)
            continue
        corrections.append(Correction(date, match.group("topic"), match.group("skill"), match.group("note").strip(), line))
    return corrections, bad


def read_rules(vault: Path) -> dict[str, Rule]:
    """Rules found anywhere in me/preferences.md, by topic. The latest learned date wins."""
    path = vault / PREFERENCES
    rules: dict[str, Rule] = {}
    if not path.is_file():
        return rules
    for line in v.read_text(path).split("\n"):
        for topic in RULE_TOPIC_RE.findall(line):
            learned_match = LEARNED_RE.search(line)
            learned = v.as_date(learned_match.group(1)) if learned_match else None
            old = rules.get(topic)
            if old is None or (learned and (old.learned is None or learned > old.learned)):
                rules[topic] = Rule(topic, learned, line.strip())
    return rules


def topic_counts(corrections: list[Correction]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in corrections:
        counts[c.topic] = counts.get(c.topic, 0) + 1
    return counts


@dataclass
class Proposal:
    topic: str
    count: int
    evidence: list[Correction]  # the 3 most recent


def proposals(vault: Path, today: _dt.date | None = None) -> list[Proposal]:
    """Topics with at least 3 corrections in the last 90 days and no rule (16.2)."""
    today = today or v.today()
    since = today - _dt.timedelta(days=WINDOW_DAYS)
    corrections, _ = read_log(vault)
    rules = read_rules(vault)
    recent = [c for c in corrections if since <= c.date <= today]
    found = []
    for topic, count in topic_counts(recent).items():
        if count >= THRESHOLD and topic not in rules:
            evidence = sorted((c for c in recent if c.topic == topic), key=lambda c: c.date, reverse=True)[:3]
            found.append(Proposal(topic, count, evidence))
    return sorted(found, key=lambda p: (-p.count, p.topic))


@dataclass
class NotFollowed:
    rule: Rule
    corrections: list[Correction]  # after the rule was learned, most recent first
    reword: bool  # two or more in the last 30 days


def rules_not_followed(vault: Path, today: _dt.date | None = None) -> list[NotFollowed]:
    """Corrections logged after a rule on the same topic was learned (16.4).

    A rule without a `learned` date is skipped: its evidence cannot be told apart from a violation.
    """
    today = today or v.today()
    since = today - _dt.timedelta(days=WINDOW_DAYS)
    corrections, _ = read_log(vault)
    found = []
    for topic, rule in sorted(read_rules(vault).items()):
        if rule.learned is None:
            continue
        after = sorted((c for c in corrections if c.topic == topic and c.date > rule.learned and since <= c.date <= today),
                       key=lambda c: c.date, reverse=True)
        if after:
            recent = [c for c in after if c.date >= today - _dt.timedelta(days=REWORD_DAYS)]
            found.append(NotFollowed(rule, after, len(recent) >= 2))
    return found


# ---------------------------------------------------------------------------- commands (9.3)

def add(vault: Path, topic: str, skill: str, note: str) -> str:
    """Append one correction to the log and return the line written."""
    if not v.is_kebab(topic):
        raise v.VaultError(f"topic must be kebab-case, like no-em-dash: {topic!r}")
    if not v.is_kebab(skill):
        raise v.VaultError(f"skill must be a skill name, like ingest: {skill!r}")
    note = " ".join(note.split())
    if not note:
        raise v.VaultError("note is empty")
    line = f"{v.today().isoformat()}{SEP}topic:{topic}{SEP}skill:{skill}{SEP}{note}"
    path = vault / LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = v.read_text(path) if path.is_file() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    v.write_text(path, existing + line + "\n")
    return line


def run_add(vault: Path, topic: str, skill: str, note: str) -> int:
    line = add(vault, topic, skill, note)
    print(f"wrote {LOG}: {line}")
    return 0


def run_topics(vault: Path) -> int:
    corrections, _ = read_log(vault)
    counts = topic_counts(corrections)
    if not counts:
        print("no topics yet")
        return 0
    rules = read_rules(vault)
    width = max(len(t) for t in counts)
    for topic in sorted(counts, key=lambda t: (-counts[t], t)):
        rule = "  (rule in me/preferences.md)" if topic in rules else ""
        print(f"{topic.ljust(width)}  {counts[topic]}{rule}")
    return 0


def run_proposals(vault: Path) -> int:
    found = proposals(vault)
    if not found:
        print(f"no proposals: no topic has {THRESHOLD} corrections in {WINDOW_DAYS} days without a rule")
        return 0
    for proposal in found:
        print(f"topic:{proposal.topic}  {proposal.count} corrections in {WINDOW_DAYS} days, no rule in {PREFERENCES}")
        for correction in proposal.evidence:
            print(f"  {correction.line}")
    return 0
