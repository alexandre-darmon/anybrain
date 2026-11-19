"""Usage counts from log.md, per week and per skill, and the observability figures (PRD 6, 9.3, 22.6, 22.7).

Log lines look like `YYYY-MM-DD · <skill> · <what> · <files>`. Weeks are ISO weeks, the current one last.
Section 6 sets the usage target (at least 2 `ingest` and 3 `ask` per week) and the kill criterion (fewer
than 2 ingests per week for 4 weeks in a row). For content (P1b), it prints the edit ratio of each published
piece, totals per channel, and whether the ratio is going down (16.5). From session traces (22.6), it prints
local-only reads on the frontier profile, cost per skill and per week, and the median files read per `ask`.
`anybrain stats --dashboard` writes the same figures, with eval runs, graph figures, usage and rule proposals,
to `journal/dashboard.md` for Obsidian (22.7).
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


def run(vault: Path, weeks: int = 4, dashboard: bool = False) -> int:
    if weeks < 1:
        raise v.VaultError("--weeks must be at least 1")
    if dashboard:
        return write_dashboard(vault, weeks)
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
    print_sessions(vault, weeks, today)
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
            "ratio": float(ratio) if isinstance(ratio, (int, float)) and not isinstance(ratio, bool) and 0 <= ratio <= 1 else None,
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
        newer_mean = sum(newer) / len(newer)
        if newer_mean >= sum(older) / len(older):
            advice = ("review me/voice.md and the channel guides" if newer_mean > EDIT_RATIO_TARGET
                      else f"it stays under the {_percent(EDIT_RATIO_TARGET)} target")
            print(f"the edit ratio did not go down over the last 8 pieces: {advice} (16.5)")


# ---------------------------------------------------------------------------- session traces (22.6)

FILES_TARGET = 8  # section 6: median files opened per answer, at most 8
DASHBOARD = "journal/dashboard.md"
BAR_WIDTH = 20
EVAL_RUNS_SHOWN = 5


def session_records(vault: Path) -> list[dict]:
    """Records of imported sessions, oldest first. Eval traces are tests, not use, and are not counted here."""
    from . import trace

    found = [r for path in trace._trace_files(vault) for r in trace._read_jsonl(path) if r.get("trigger") == "session"]
    return sorted(found, key=lambda r: str(r.get("started") or ""))


def _day(record: dict) -> _dt.date | None:
    return v.as_date(str(record.get("started") or "")[:10])


def session_weeks(vault: Path, weeks: int, today: _dt.date) -> list[tuple[_dt.date, list[dict], list[dict]]]:
    """The last `weeks` ISO weeks, oldest first: (week start, rows per skill, raw records). A week whose records
    have expired comes from its weekly aggregate, with no raw record left."""
    from . import trace

    aggregated = trace.load_weekly(vault)["weeks"]
    records = session_records(vault)
    current = week_start(today)
    result = []
    for n in range(weeks - 1, -1, -1):
        start = current - _dt.timedelta(weeks=n)
        key = trace.iso_week(start)
        raw = [r for r in records if _day(r) and week_start(_day(r)) == start and _day(r) <= today]
        rows = aggregated[key] if key in aggregated else trace.aggregate(raw)
        result.append((start, rows, [] if key in aggregated else raw))
    return result


def _skill_name(skill: str | None) -> str:
    return "(no skill)" if skill in (None, "", "none") else str(skill)


def _usd(value) -> str:
    return f"{value:.2f}" if isinstance(value, (int, float)) else "?"


def _week_label(start: _dt.date) -> str:
    year, week, _ = start.isocalendar()
    return f"{year}-W{week:02d}"


def _local_reads(raw: list[dict]) -> list[dict]:
    return [r for r in raw if r.get("local_only_read_on_frontier")]


def _ask_counts(raw: list[dict]) -> list[int]:
    return [len(r.get("files_read") or []) for r in raw if r.get("skill") == "ask"]


def print_sessions(vault: Path, weeks: int, today: _dt.date) -> None:
    from . import trace

    table = session_weeks(vault, weeks, today)
    if not any(rows for _, rows, _ in table):
        print("sessions: no run traces in these weeks; `anybrain trace import` reads finished Claude Code sessions")
        return
    raw = [r for _, _, records in table for r in records]
    local = sum(row.get("local_only_reads") or 0 for _, rows, _ in table for row in rows)
    print(f"sessions (run traces, 22.6), last {weeks} week(s):")
    print(f"  local-only files read on the frontier profile: {local} (target 0)")
    for record in _local_reads(raw):
        print(f"    {record.get('started', '')[:10]}  {_skill_name(record.get('skill'))}: "
              f"{', '.join(record['local_only_read_on_frontier'])}")
    print("  week      skill            runs  cost USD  median files read")
    for start, rows, _ in table:
        label = _week_label(start)
        for row in rows:
            median = row.get("files_read_median")
            print(f"  {label:<9} {_skill_name(row.get('skill')):<15} {row.get('runs', 0):>5}  {_usd(row.get('cost_usd')):>8}"
                  f"  {'?' if median is None else f'{median:g}':>17}")
            label = ""
    asks = _ask_counts(raw)
    if asks:
        median = trace._median(asks)
        verdict = "within target" if median <= FILES_TARGET else "above target"
        print(f"  median files read per ask: {median:g} over {len(asks)} run(s), {verdict} (at most {FILES_TARGET})")
        differ = [r for r in raw if r.get("skill") == "ask" and r.get("self_reported_files_opened") is not None
                  and r["self_reported_files_opened"] != len(r.get("files_read") or [])]
        if differ:
            print(f"  {len(differ)} ask run(s) said a different `files opened` than measured (section 6)")
    shared = sum(1 for r in raw if r.get("cost_shared"))
    if shared:
        print(f"  {shared} run(s) share their session's measured cost by tokens: exact per session, estimated per "
              "question (D049)")


# ---------------------------------------------------------------------------- dashboard (22.7)

def _bar(value: float, top: float) -> str:
    if not isinstance(value, (int, float)) or top <= 0 or value <= 0:
        return ""
    return "\u2588" * max(1, round(BAR_WIDTH * value / top))


def _cell(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _eval_runs(vault: Path) -> list[tuple[str, dict]]:
    import json

    from . import trace

    runs = []
    for path in sorted((vault / trace.BASELINE_DIR).glob("run-*.json")):
        try:
            data = json.loads(v.read_text(path))
        except ValueError:
            continue
        if isinstance(data, dict):
            runs.append((path.name, data))
    return sorted(runs, key=lambda item: (str(item[1].get("date") or ""), item[0]))[-EVAL_RUNS_SHOWN:]


def dashboard_text(vault: Path, weeks: int, today: _dt.date) -> str:
    from . import feedback
    from . import graph as g
    from . import trace
    from .index import HEADER

    table = session_weeks(vault, weeks, today)
    raw = [r for _, _, records in table for r in records]
    evals = _eval_runs(vault)
    out = [HEADER, "", "# Dashboard", "",
           f"Written by `anybrain stats --dashboard` on {today.isoformat()}, over the last {weeks} week(s). Rewritten "
           "each time: edit nothing here. It summarises run traces, so it stays in `journal/`, local only (22.7).", ""]

    out += ["## Local-only files read on the frontier profile", "", "Target: 0 (section 6).", ""]
    rows = [(r.get("started", "")[:10], "session", _skill_name(r.get("skill")), r["local_only_read_on_frontier"])
            for r in _local_reads(raw)]
    for name, data in evals:
        if data.get("local_only_reads") and str(data.get("date") or "") >= (today - _dt.timedelta(weeks=weeks)).isoformat():
            rows.append((str(data.get("date")), f"eval {data.get('harness', '?')}", "golden set", data["local_only_reads"]))
    aggregated = sum(row.get("local_only_reads") or 0 for _, rs, records in table if not records for row in rs)
    if rows:
        out += ["| Date | Run | Skill | Files |", "| --- | --- | --- | --- |"]
        out += [f"| {d} | {_cell(run)} | {_cell(skill)} | {_cell(', '.join(files))} |" for d, run, skill, files in sorted(rows)]
    elif not aggregated:
        out.append("None.")
    if aggregated:
        out.append(f"{aggregated} more in weeks kept only as weekly totals.")
    out.append("")

    out += ["## Cost per skill and per week", "",
            "From imported sessions (`anybrain trace import`), in USD as the harness reports it.", ""]
    cost_rows = [(_week_label(start), row) for start, rows_, _ in table for row in rows_]
    if cost_rows:
        top = max((row.get("cost_usd") or 0) for _, row in cost_rows)
        out += ["| Week | Skill | Runs | Cost | |", "| --- | --- | ---: | ---: | --- |"]
        out += [f"| {week} | {_cell(_skill_name(row.get('skill')))} | {row.get('runs', 0)} | {_usd(row.get('cost_usd'))} | "
                f"{_bar(row.get('cost_usd') or 0, top)} |" for week, row in cost_rows]
        shared = sum(1 for r in raw if r.get("cost_shared"))
        if shared:
            out += ["", f"{shared} run(s) share their session's measured cost by tokens: exact per session, estimated "
                    "per question."]
    else:
        out.append("No session traces in these weeks.")
    out.append("")

    out += ["## Files read per ask", "", f"Target: a median of at most {FILES_TARGET} (section 6).", ""]
    ask_rows = []
    for start, rows_, records in table:
        counts = _ask_counts(records)
        if counts:
            ask_rows.append((_week_label(start), len(counts), trace._median(counts)))
        else:
            ask = next((row for row in rows_ if row.get("skill") == "ask"), None)
            if ask:
                ask_rows.append((_week_label(start), ask.get("runs", 0), ask.get("files_read_median")))
    if ask_rows:
        out += ["| Week | Asks | Median files read | |", "| --- | ---: | ---: | --- |"]
        out += [f"| {week} | {n} | {'?' if m is None else f'{m:g}'} | {_bar(m or 0, max(FILES_TARGET * 2, m or 0))} |"
                for week, n, m in ask_rows]
    else:
        out.append("No `ask` run traced in these weeks.")
    out.append("")

    out += ["## Eval runs", ""]
    if evals:
        out += ["| Date | Harness | Profile | Pass rate | Passed | Cost | Gates passed |",
                "| --- | --- | --- | ---: | ---: | ---: | ---: |"]
        for name, data in evals:
            gates = data.get("gates") or []
            rate = data.get("pass_rate")
            out.append(f"| {data.get('date', '?')} | {_cell(data.get('harness', '?'))} | {_cell(data.get('profile', '?'))} | "
                       f"{'?' if rate is None else f'{rate:.0%}'} | {data.get('passed', '?')}/{data.get('scored', '?')} | "
                       f"{_usd(data.get('cost_usd'))} | {sum(1 for gate in gates if gate.get('pass'))}/{len(gates)} |")
        last = evals[-1][1]
        if last.get("by_tag"):
            out += ["", f"Last run, pass rate per tag: "
                    + ", ".join(f"{tag} {value:.0%}" for tag, value in last["by_tag"].items()) + "."]
        if last.get("gates"):
            out += ["", "| Gate (17.3) | Value | Passed |", "| --- | --- | --- |"]
            out += [f"| {_cell(gate.get('name', '?'))} | {_cell(gate.get('shown', gate.get('value')))} | "
                    f"{'yes' if gate.get('pass') else 'no'} |" for gate in last["gates"]]
    else:
        out.append("No eval run yet (`anybrain eval`).")
    out.append("")

    stats = g.Graph(vault).stats()
    out += ["## Graph", "", "| Pages | Links | Orphans | Broken links |", "| ---: | ---: | ---: | ---: |",
            f"| {stats['pages']} | {stats['edges']} | {stats['orphans']} | {stats['broken']} |", ""]

    entries, _ = read_log(vault)
    usage = counts_by_week(entries, weeks, today)
    skills = sorted({s for _, counts in usage for s in counts} | set(TARGETS))
    out += ["## Usage from log.md", "", "Target per week: " + ", ".join(f"{s} at least {n}" for s, n in TARGETS.items())
            + " (section 6).", "", "| Week | " + " | ".join(skills) + " |", "| --- |" + " ---: |" * len(skills)]
    out += [f"| {_week_label(start)} | " + " | ".join(str(counts.get(s, 0)) for s in skills) + " |" for start, counts in usage]
    out.append("")

    proposals = feedback.proposals(vault, today)
    out += ["## Rule proposals", ""]
    if proposals:
        out += [f"- `{_cell(p.topic)}`: {p.count} corrections in {feedback.WINDOW_DAYS} days, no rule yet. "
                "Review them with the `feedback` skill." for p in proposals]
    else:
        out.append("None pending.")
    return "\n".join(out) + "\n"


def write_dashboard(vault: Path, weeks: int = 4) -> int:
    from .index import HEADER

    path = vault / DASHBOARD
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise v.VaultError(f"{DASHBOARD} is not a plain file: nothing written")
    if path.is_file() and not v.read_text(path).startswith(HEADER):
        print(f"stats: kept {DASHBOARD}: it was not generated by anybrain. Rename it so the dashboard can be "
              "written there")
        return 1
    v.write_text(path, dashboard_text(vault, weeks, v.today()))
    print(f"wrote {DASHBOARD}")
    return 0
