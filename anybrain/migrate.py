"""Migration from an existing Obsidian vault (PRD section 21).

`anybrain migrate <vault> inventory <legacy_path>` copies the legacy vault, read-only, to
`raw/legacy/<date>/` and writes a report listing every file with a suggested target (table of 21.1) and a
status for the migrate skill to fill. `anybrain migrate <vault> check` verifies that every paragraph the owner
wrote in the legacy notes exists verbatim in the vault outside `raw/`, and that no file is still pending.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import stat
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from . import graph as g
from . import vault as v

LEGACY_DIR = "raw/legacy"
STATUS_RE = re.compile(r"^- `(?P<path>[^`]+)` · .*· status: (?P<status>.+)$")
LINK_RE = re.compile(r"\[\[[^\[\]\n]+\]\]|\[[^\[\]\n]*\]\([^()\s]+\)")
FOLD = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', " ": " ", " ": " "})
MIN_WORDS = 3
# A project's review date is decided by code, never by the agent (docs/decisions.md D037): the date already on
# the legacy note under one of these keys (frontmatter, or a Dataview `key:: value` line), else the default.
# Ranked: a note's own next review date wins over a review date, which wins over a due date or deadline.
REVIEW_KEY_RANKS = (
    {"next_review", "nextreview", "prochaine_revue", "prochainerevue"},
    {"review", "review_date", "reviewdate", "revue", "date_revue", "daterevue"},
    {"due", "due_date", "duedate", "deadline", "echeance"},
)
DEFAULT_REVIEW_DAYS = 30
# Dataview inline fields: `key:: value` on its own line, or `[key:: value]` / `(key:: value)` anywhere in a line.
INLINE_FIELD_RE = re.compile(r"^\s*[-*]?\s*([^:\n`\[\]()]{2,40})::\s*(.+?)\s*$", re.M)
BRACKET_FIELD_RE = re.compile(r"[\[(]\s*([^:\n`\[\]()]{2,40})::\s*([^\])\n]+?)\s*[\])]")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PROJECT_PATH_RE = re.compile(r"projects/[\w.-]+")

# Table of 21.1, by top-level folder name (accents and case ignored).
FOLDER_TARGETS = {
    "inbox": "raw/inbox/",
    "readwise": "raw/highlights/",
    "pieces jointes": "raw/attachments/",
    "attachments": "raw/attachments/",
    "archive": "raw/archive/ (not ingested unless the owner asks)",
    "summaries": "library/<slug>.md",
    "index": "dropped: replaced by generated indexes (kept in the snapshot)",
    "vues": "dropped: replaced by generated indexes (kept in the snapshot)",
    "modeles": "dropped: replaced by .harness/templates/ (kept in the snapshot)",
    "coaching lab": "ask the owner: projects/coaching-lab.md or wiki/",
    "cmd": "ask the owner: prompts become candidate skills listed in the report",
    "obsidian": "ask the owner: notes about tools go to wiki/notes/",
}


def _fold_name(name: str) -> str:
    return unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower().strip()


def suggested_target(rel: str, size: int) -> str:
    parts = rel.split("/")
    if len(parts) > 1:
        return FOLDER_TARGETS.get(_fold_name(parts[0]), "ask the owner")
    name = parts[0]
    if _fold_name(name) == "readme.md" and size == 0:
        return "dropped: empty"
    if not name.endswith(".md"):
        return "raw/attachments/"
    stem = name[:-3]
    try:
        if _fold_name(stem).startswith("moc - "):
            return f"wiki/concepts/{v.slugify(stem.split('-', 1)[1])}.md (a MOC becomes a concept with its sources)"
        return f"library/{v.slugify(stem)}.md (if it is a book note)"
    except ValueError:
        return "ask the owner"


# ---------------------------------------------------------------------------- owner text (21.2 step 1)

def _units(body: str) -> list[str]:
    """Paragraphs, with each list item of a list paragraph as its own unit; fenced code removed."""
    lines, kept, fenced = body.split("\n"), [], False
    for line in lines:
        if v.FENCE_RE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            kept.append(line)
    units: list[str] = []
    for block in re.split(r"\n\s*\n", "\n".join(kept)):
        block = block.strip("\n")
        if not block.strip():
            continue
        items = re.split(r"\n(?=\s*(?:[-*+]|\d+[.)])\s)", block)
        if len(items) > 1 or re.match(r"^\s*(?:[-*+]|\d+[.)])\s", block):
            units.extend(item for item in items if item.strip())
        else:
            units.append(block)
    return units


def clean(text: str) -> str:
    """Owner text without list markers, emphasis and note labels, whitespace collapsed."""
    text = text.translate(FOLD)
    text = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", text, flags=re.M)
    text = re.sub(r"\*\*(?:note|notes)\s*:?\*\*\s*:?", "", text, flags=re.I)
    text = text.replace("**", "").replace("__", "")
    return re.sub(r"\s+", " ", text).strip()


def owner_paragraphs(rel: str, text: str) -> list[str]:
    """Paragraphs the owner wrote: not a heading, a quote block, a template placeholder, a highlight, a list
    of links only, or a line shorter than three words (21.2 step 1, docs/decisions.md D035)."""
    top = _fold_name(rel.split("/", 1)[0]) if "/" in rel else ""
    if top in ("modeles", "templates"):
        return []
    _, body, _, _ = v.parse_frontmatter(text)
    found = []
    for unit in _units(body):
        stripped = unit.strip()
        if stripped.startswith(("#", ">")) or "{{" in stripped or "|" in stripped.split("\n", 1)[0][:1]:
            continue
        if top == "readwise" and not re.search(r"\*\*notes?\s*:?\*\*", stripped, re.I):
            continue  # a highlight or metadata line; only the owner's notes on highlights count
        if top == "readwise":
            stripped = stripped[re.search(r"\*\*notes?\s*:?\*\*", stripped, re.I).start():]
        text_only = clean(stripped)
        if len(clean(LINK_RE.sub(" ", stripped)).split()) < MIN_WORDS:
            continue  # links only, or too short to be the owner's writing

        found.append(text_only)
    return found


# ---------------------------------------------------------------------------- inventory

@dataclass
class Entry:
    rel: str
    size: int
    is_markdown: bool
    owner: list[str]
    links: int
    keys: list[str]
    review: tuple[str, str] | None = None  # (value as written, where it was found)


def _review_rank(name) -> int | None:
    folded = _fold_name(str(name)).strip("*_ ").replace(" ", "_").replace("-", "_")
    for rank, keys in enumerate(REVIEW_KEY_RANKS):
        if folded in keys or folded.replace("_", "") in keys:
            return rank
    return None


def _as_text(value) -> str:
    if isinstance(value, _dt.datetime):
        return value.date().isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, default=lambda d: d.isoformat() if hasattr(d, "isoformat") else str(d))
    return str(value).strip()


def valid_date(text: str) -> bool:
    """A real calendar date written YYYY-MM-DD (2026-02-30 is not one)."""
    if not ISO_DATE_RE.match(text or ""):
        return False
    try:
        _dt.date.fromisoformat(text)
    except ValueError:
        return False
    return True


def review_date(fm: dict | None, body: str) -> tuple[str, str] | None:
    """The review or due date already written on a legacy note, from the frontmatter or a Dataview inline
    field. The best-ranked key wins (next review, then review, then due); within a rank, the frontmatter first."""
    found: list[tuple[int, int, str, str]] = []
    for key, value in (fm or {}).items():
        rank = _review_rank(key)
        if rank is not None and value not in (None, "", []):
            found.append((rank, 0, _as_text(value), f"`{key}`"))
    for pattern in (INLINE_FIELD_RE, BRACKET_FIELD_RE):
        for match in pattern.finditer(body or ""):
            key = match.group(1).strip()
            rank = _review_rank(key)
            if rank is not None:
                found.append((rank, 1, match.group(2).strip(), f"`{key.strip('*_ ')}::`"))
    if not found:
        return None
    rank, _, value, where = min(found, key=lambda item: (item[0], item[1]))
    return value, where


FRONTMATTER_LINE_RE = re.compile(r"^\s*([^:#\n]{1,40}):\s*(.*?)\s*$")


def _frontmatter_lines(text: str) -> dict:
    """The `key: value` lines of a frontmatter that is not valid YAML, values as written (quotes removed)."""
    fm_text, _, _ = v.split_frontmatter(text)
    found = {}
    for line in (fm_text or "").split("\n"):
        match = FRONTMATTER_LINE_RE.match(line)
        if match and match.group(1).strip() not in found:
            found[match.group(1).strip()] = match.group(2).strip().strip("'\"")
    return found


def default_review(day: str) -> str:
    return (_dt.date.fromisoformat(day) + _dt.timedelta(days=DEFAULT_REVIEW_DAYS)).isoformat()


def _snapshot_entries(snapshot: Path) -> list[Entry]:
    entries = []
    for path in sorted(snapshot.rglob("*"), key=lambda p: p.as_posix()):
        if not path.is_file():
            continue
        rel = v.rel_path(snapshot, path)
        is_md = rel.endswith(".md")
        text = v.read_text(path) if is_md else ""
        fm, body, _, error = v.parse_frontmatter(text) if is_md else (None, "", 0, None)
        if is_md and fm is None and error and error != "no frontmatter":
            fm = _frontmatter_lines(text)  # invalid YAML: read `key: value` lines, so a review date is not lost
        entries.append(Entry(rel, path.stat().st_size, is_md, owner_paragraphs(rel, text) if is_md else [],
                             len(LINK_RE.findall(body)) if is_md else 0, sorted(str(k) for k in (fm or {}).keys()),
                             review_date(fm, body) if is_md else None))
    return entries


def _copy_snapshot(legacy: Path, target: Path) -> list[str]:
    """Copy without hidden entries (`.obsidian/` may hold plugin tokens); files become read-only."""
    skipped = sorted(p.name + ("/" if p.is_dir() else "") for p in legacy.iterdir() if p.name.startswith("."))

    def ignore(directory: str, names: list[str]) -> set[str]:
        return {n for n in names if n.startswith(".")}

    shutil.copytree(legacy, target, ignore=ignore)
    for path in target.rglob("*"):
        if path.is_file():
            path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    return skipped


def report_path(vault: Path, day: str) -> Path:
    """Where the session can read it (Q22): journal/ on the local profile, journal/reports/ otherwise."""
    local = g.current_profile() == "local"
    return vault / ("journal" if local else "journal/reports") / f"migration-{day}.md"


def write_inventory(vault: Path, snapshot: Path, legacy_name: str, hidden: list[str], day: str) -> Path:
    entries = _snapshot_entries(snapshot)
    markdown = [e for e in entries if e.is_markdown]
    owner_files = [e for e in markdown if e.owner]
    keys: dict[str, int] = {}
    for entry in markdown:
        for key in entry.keys:
            keys[key] = keys.get(key, 0) + 1
    folders: dict[str, list[Entry]] = {}
    for entry in entries:
        folders.setdefault(entry.rel.split("/", 1)[0] if "/" in entry.rel else "(root)", []).append(entry)
    snap_rel = v.rel_path(vault, snapshot)
    lines = [
        "---",
        f"title: {json.dumps(f'Migration inventory {day}')}",
        "type: report",
        f"description: {json.dumps(f'Legacy vault snapshot of {day}: {len(entries)} files, {len(owner_files)} with owner-written text.')}",
        "tags: [migration]",
        "language: en",
        f"created: {day}",
        f"updated: {day}",
        "sources: []",
        "sensitivity: personal",
        "---",
        "",
        "## Summary",
        "",
        f"- Snapshot: `{snap_rel}/`, a read-only copy of `{legacy_name}`.",
        f"- Files: {len(entries)} ({len(markdown)} Markdown, {len(entries) - len(markdown)} attachments or other files).",
        f"- Links found: {sum(e.links for e in markdown)}.",
        "- Frontmatter keys: " + (", ".join(f"`{k}` ({n})" for k, n in sorted(keys.items())) or "none") + ".",
        f"- Files with owner-written text: {len(owner_files)} ({sum(len(e.owner) for e in owner_files)} paragraphs). "
        "Each paragraph goes into the vault verbatim (21.2 step 5); `anybrain migrate . check` verifies it.",
        "- Not copied: " + (", ".join(f"`{h}`" for h in hidden) or "nothing") + ". Hidden folders are not snapshotted: "
        "`.obsidian/` can hold plugin tokens. Its settings are merged by `anybrain init` and stay in the legacy vault.",
        f"- Review dates: a project page takes the `review date` listed on its legacy file below, the earliest if "
        f"several files make one page; with none, `next_review: {default_review(day)}` ({DEFAULT_REVIEW_DAYS} days "
        "after this migration). Copy the date as it is; `anybrain migrate . check` verifies it.",
        "",
        "## Folders",
        "",
        "| Folder | Files | Suggested target (PRD 21.1) |",
        "| --- | --- | --- |",
    ]
    for folder, items in sorted(folders.items()):
        target = suggested_target(items[0].rel, items[0].size) if folder != "(root)" else "per file, below"
        lines.append(f"| `{folder}` | {len(items)} | {target} |")
    lines += [
        "",
        "## Files",
        "",
        "<!-- The migrate skill replaces each `pending` with `mapped to <path>` or `skipped: <reason>`. "
        "anybrain migrate <vault> check reports any line still pending. -->",
        "",
    ]
    for entry in entries:
        owner = f"owner text: {len(entry.owner)}" if entry.owner else "no owner text"
        review = ""
        if entry.review:
            value, where = entry.review
            review = (f" · review date: {value} (from {where})" if valid_date(value)
                      else f" · review date: not a valid YYYY-MM-DD date ({where}: {json.dumps(value, ensure_ascii=False)}), ask the owner")
        lines.append(f"- `{entry.rel}` · {owner} · suggested: {suggested_target(entry.rel, entry.size)}{review}"
                     " · status: pending")
    lines.append("")
    path = report_path(vault, day)
    v.write_text(path, "\n".join(lines))
    return path


def inventory(vault: Path, legacy_path: str) -> int:
    legacy = Path(legacy_path).expanduser().resolve()
    if not legacy.is_dir():
        raise v.VaultError(f"legacy vault not found: {legacy_path}")
    if legacy == vault.resolve() or vault.resolve() in legacy.parents or legacy in vault.resolve().parents:
        raise v.VaultError("the legacy vault and the new vault must be separate folders")
    day = v.today().isoformat()
    snapshot = vault / LEGACY_DIR / day
    if snapshot.exists():
        hidden = sorted(p.name + ("/" if p.is_dir() else "") for p in legacy.iterdir() if p.name.startswith("."))
        print(f"kept {v.rel_path(vault, snapshot)}/: a snapshot was already taken today")
    else:
        hidden = _copy_snapshot(legacy, snapshot)
        print(f"wrote {v.rel_path(vault, snapshot)}/ (read-only copy)")
    path = report_path(vault, day)
    if path.exists():
        print(f"kept {v.rel_path(vault, path)}: it already exists, so its statuses are not reset")
        return 0
    print(f"wrote {v.rel_path(vault, write_inventory(vault, snapshot, legacy.name, hidden, day))}")
    return 0


# ---------------------------------------------------------------------------- check (21.2 step 6)

def latest_snapshot(vault: Path) -> Path | None:
    base = vault / LEGACY_DIR
    snapshots = sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else []
    return snapshots[-1] if snapshots else None


def _vault_text(vault: Path) -> str:
    """Where owner text counts as preserved: every page, and raw/ files outside the legacy snapshots, which the
    agent never edits (an Inbox note moved to raw/inbox/ by 21.1 is not lost; docs/decisions.md D035)."""
    parts = [clean(page.body) for page in v.load_pages(vault)]
    raw = vault / "raw"
    for path in sorted(raw.rglob("*.md")) if raw.is_dir() else []:
        rel = v.rel_path(vault, path)
        if not rel.startswith(LEGACY_DIR + "/"):
            parts.append(clean(v.read_text(path)))
    return "\n".join(parts)


def check(vault: Path) -> int:
    snapshot = latest_snapshot(vault)
    if snapshot is None:
        raise v.VaultError("no snapshot in raw/legacy/: run anybrain migrate <vault> inventory <legacy_path> first")
    day = snapshot.name
    status_of: dict[str, str] = {}
    for candidate in (vault / "journal" / f"migration-{day}.md", vault / "journal" / "reports" / f"migration-{day}.md"):
        if candidate.is_file():
            for line in v.read_text(candidate).split("\n"):
                match = STATUS_RE.match(line.strip())
                if match:
                    status_of[match.group("path")] = match.group("status").strip()
    pending = [(rel, s) for rel, s in status_of.items() if not s.startswith(("mapped", "skipped"))]
    entries = _snapshot_entries(snapshot)
    wrong_dates = check_review_dates(vault, day, entries, status_of)
    haystack = _vault_text(vault)
    missing, kept_in_snapshot, total = [], [], 0
    for entry in entries:
        for paragraph in entry.owner:
            total += 1
            if paragraph in haystack:
                continue
            if status_of.get(entry.rel, "").startswith("skipped"):
                kept_in_snapshot.append((entry.rel, paragraph))
            else:
                missing.append((entry.rel, paragraph))
    for rel, paragraph in missing:
        short = paragraph if len(paragraph) <= 80 else paragraph[:77] + "..."
        print(f"missing owner text from {rel}: \"{short}\"")
    for rel in sorted({rel for rel, _ in kept_in_snapshot}):
        print(f"kept only in the snapshot (skipped by the owner): {rel}")
    for rel, status in pending:
        print(f"not migrated yet: {rel} (status: {status})")
    if not status_of:
        print(f"note: no migration report for {day} in journal/ or journal/reports/")
    for line in wrong_dates:
        print(line)
    print(f"migrate: {total - len(missing) - len(kept_in_snapshot)} of {total} owner paragraph(s) found in the vault, "
          f"{len(kept_in_snapshot)} kept only in the snapshot, {len(missing)} missing; {len(pending)} file(s) still pending; "
          f"{len(wrong_dates)} project review date(s) to fix")
    return 1 if missing or pending or wrong_dates else 0


def check_review_dates(vault: Path, day: str, entries: list[Entry], status_of: dict[str, str]) -> list[str]:
    """Each project page made from legacy files has the review date the inventory computed for it."""
    reviews = {e.rel: e.review for e in entries}
    sources: dict[str, list[str]] = {}
    for rel, status in status_of.items():
        if not status.startswith("mapped"):
            continue
        for target in PROJECT_PATH_RE.findall(status):
            target = target.rstrip(".")
            target = target if target.endswith(".md") else target + ".md"
            if rel not in sources.setdefault(target, []):
                sources[target].append(rel)
    problems = []
    for page_rel, legacy in sorted(sources.items()):
        found = [reviews[r][0] for r in legacy if reviews.get(r)]
        if any(not valid_date(value) for value in found):
            continue  # one of its notes needs the owner's date (the report asks them): theirs is not checked
        dates = sorted(found)
        expected = dates[0] if dates else default_review(day)
        why = "from the legacy note" if dates else f"default, {DEFAULT_REVIEW_DAYS} days after the migration"
        path = vault / page_rel
        if not path.is_file():
            continue  # a missing page is reported by the owner-text check or lint
        fm, *_ = v.parse_frontmatter(v.read_text(path))
        value = (fm or {}).get("next_review")
        actual = value.isoformat() if isinstance(value, _dt.date) else str(value or "")
        if actual != expected:
            problems.append(f"next_review of {page_rel}: {actual or 'missing'}, expected {expected} ({why})")
    return problems
