"""Usage counts from log.md, per week and per skill (PRD sections 6 and 9.3).

Log lines look like `YYYY-MM-DD · <skill> · <what> · <files>`. Weeks are ISO weeks, the current one last.
Section 6 sets the usage target (at least 2 `ingest` and 3 `ask` per week) and the kill criterion (fewer
than 2 ingests per week for 4 weeks in a row). Edit ratios and published-piece metrics arrive with P1b.
"""
from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

from . import vault as v

LOG_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s*·\s*([^\s·]+)\s*·")
TARGETS = {"ingest": 2, "ask": 3}
KILL_WEEKS = 4


def read_log(vault: Path) -> tuple[list[tuple[_dt.date, str]], int]:
    """(date, skill) for each log line, and the number of lines that do not follow the format."""
    path = vault / "log.md"
    if not path.is_file():
        return [], 0
    entries, skipped = [], 0
    for line in v.read_text(path).split("\n"):
        if not line.strip():
            continue
        match = LOG_RE.match(line.strip())
        date = v.as_date(match.group(1)) if match else None
        if date is None:
            skipped += 1
            continue
        entries.append((date, match.group(2)))
    return entries, skipped


def week_start(day: _dt.date) -> _dt.date:
    return day - _dt.timedelta(days=day.weekday())


def counts_by_week(entries: list[tuple[_dt.date, str]], weeks: int, today: _dt.date) -> list[tuple[_dt.date, dict]]:
    """The last `weeks` ISO weeks, oldest first, with a count per skill."""
    current = week_start(today)
    starts = [current - _dt.timedelta(weeks=n) for n in range(weeks - 1, -1, -1)]
    table = {start: {} for start in starts}
    for date, skill in entries:
        start = week_start(date)
        if start in table and date <= today:
            table[start][skill] = table[start].get(skill, 0) + 1
    return [(start, table[start]) for start in starts]


def run(vault: Path, weeks: int = 4) -> int:
    if weeks < 1:
        raise v.VaultError("--weeks must be at least 1")
    today = v.today()
    entries, skipped = read_log(vault)
    rows = counts_by_week(entries, weeks, today)
    skills = sorted({s for _, counts in rows for s in counts} | set(TARGETS))
    width = max(len(s) for s in skills)
    header = "week      " + "".join(s.rjust(width + 2) for s in skills) + "total".rjust(7)
    print(header)
    met = 0
    for start, counts in rows:
        year, week, _ = start.isocalendar()
        line = f"{year}-W{week:02d}  " + "".join(str(counts.get(s, 0)).rjust(width + 2) for s in skills)
        line += str(sum(counts.values())).rjust(7)
        ok = all(counts.get(s, 0) >= n for s, n in TARGETS.items())
        met += ok
        print(line + ("   target met" if ok else ""))
    target = ", ".join(f"{s} at least {n}" for s, n in TARGETS.items())
    print(f"usage target per week ({target}): met in {met} of {len(rows)} week(s)")
    if len(rows) >= KILL_WEEKS and all(counts.get("ingest", 0) < TARGETS["ingest"] for _, counts in rows[-KILL_WEEKS:]):
        print(f"kill criterion (PRD section 6): fewer than {TARGETS['ingest']} ingests per week for {KILL_WEEKS} weeks "
              "in a row; stop adding features and run a retro in journal/")
    if skipped:
        print(f"{skipped} line(s) of log.md do not follow `YYYY-MM-DD · <skill> · ...` and were skipped")
    return 0
