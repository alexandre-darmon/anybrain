"""Usage counts from log.md, per week and per skill (PRD sections 6 and 9.3).

Log lines look like `YYYY-MM-DD · <skill> · <what> · <files>`. Weeks are ISO weeks, the current one last.
Section 6 sets the usage target (at least 2 `ingest` and 3 `ask` per week) and the kill criterion (fewer
than 2 ingests per week for 4 weeks in a row). For content (P1b), it prints the edit ratio of each published
piece, totals per channel, and whether the ratio is going down (16.5).
"""
from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

from . import vault as v

LOG_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s*·\s*([^\s·]+)\s*·")
TARGETS = {"ingest": 2, "ask": 3}
KILL_WEEKS = 4
EDIT_RATIO_TARGET = 0.30  # section 6: at most 30% by the 8th published piece
CONVERGENCE_PIECES = 8  # 16.5: the ratio should decrease over 8 pieces
METRICS = ("impressions", "reactions", "comments", "leads")


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
    print_content(vault)
    return 0


def published_pieces(vault: Path) -> list[dict]:
    """Published pieces, oldest first, with their date, channel, edit ratio and owner metrics."""
    folder = vault / "content" / "published"
    pieces = []
    for path in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        fm, *_ = v.parse_frontmatter(v.read_text(path))
        if not fm or fm.get("type") != "published":
            continue
        ratio = fm.get("edit_ratio")
        pieces.append({
            "rel": v.rel_path(vault, path),
            "date": v.as_date(fm.get("published")),
            "channel": str(fm.get("channel") or "?"),
            "ratio": float(ratio) if isinstance(ratio, (int, float)) and not isinstance(ratio, bool) else None,
            **{m: fm.get(m) for m in METRICS if isinstance(fm.get(m), int) and not isinstance(fm.get(m), bool)},
        })
    return sorted(pieces, key=lambda p: (str(p["date"] or ""), p["rel"]))


def _percent(ratio: float | None) -> str:
    return "?" if ratio is None else f"{round(ratio * 100)}%"


def print_content(vault: Path) -> None:
    pieces = published_pieces(vault)
    if not pieces:
        return
    print(f"content: {len(pieces)} published piece(s), oldest first")
    for piece in pieces:
        date = piece["date"].isoformat() if piece["date"] else "?"
        print(f"  {date}  {piece['channel']:<11} edit {_percent(piece['ratio']):>4}  {piece['rel']}")
    by_channel: dict[str, list[dict]] = {}
    for piece in pieces:
        by_channel.setdefault(piece["channel"], []).append(piece)
    for channel, items in sorted(by_channel.items()):
        ratios = sorted(p["ratio"] for p in items if p["ratio"] is not None)
        median = ratios[len(ratios) // 2] if len(ratios) % 2 else (
            (ratios[len(ratios) // 2 - 1] + ratios[len(ratios) // 2]) / 2 if ratios else None)
        totals = [f"{m} {sum(p.get(m, 0) for p in items)}" for m in METRICS if any(m in p for p in items)]
        print(f"  {channel}: {len(items)} piece(s), median edit {_percent(median)}" + (f", {', '.join(totals)}" if totals else ""))
    ratios = [p["ratio"] for p in pieces if p["ratio"] is not None]
    if len(ratios) >= CONVERGENCE_PIECES:
        eighth = ratios[CONVERGENCE_PIECES - 1]
        verdict = "met" if eighth <= EDIT_RATIO_TARGET else "not met"
        print(f"edit ratio target (at most {_percent(EDIT_RATIO_TARGET)} by the 8th piece): {verdict} ({_percent(eighth)})")
        last = ratios[-CONVERGENCE_PIECES:]
        older, newer = last[:CONVERGENCE_PIECES // 2], last[CONVERGENCE_PIECES // 2:]
        if sum(newer) / len(newer) >= sum(older) / len(older):
            print("the edit ratio did not go down over the last 8 pieces: review me/voice.md and the channel guides (16.5)")
