"""Migration from an existing Obsidian vault (PRD section 21).

`anybrain migrate <vault> inventory <legacy_path>` copies the legacy vault, read-only, to
`raw/legacy/<date>/` and writes a report listing every file with a suggested target (table of 21.1) and a
status for the migrate skill to fill. `anybrain migrate <vault> check` verifies that every paragraph the owner
wrote in the legacy notes exists verbatim in the vault outside `raw/`, and that no file is still pending.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from . import graph as g
from . import vault as v

LEGACY_DIR = "raw/legacy"
STATUS_RE = re.compile(r"^- `(?P<path>[^`]+)` · .*· status: (?P<status>.+)$")
LINK_RE = re.compile(r"\[\[[^\[\]\n]+\]\]|\[[^\[\]\n]*\]\([^()\s]+\)")
FOLD = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', " ": " ", " ": " "})
MIN_WORDS = 3

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


def _snapshot_entries(snapshot: Path) -> list[Entry]:
    entries = []
    for path in sorted(snapshot.rglob("*"), key=lambda p: p.as_posix()):
        if not path.is_file():
            continue
        rel = v.rel_path(snapshot, path)
        is_md = rel.endswith(".md")
        text = v.read_text(path) if is_md else ""
        fm, body, _, _ = v.parse_frontmatter(text) if is_md else (None, "", 0, None)
        entries.append(Entry(rel, path.stat().st_size, is_md, owner_paragraphs(rel, text) if is_md else [],
                             len(LINK_RE.findall(body)) if is_md else 0, sorted((fm or {}).keys())))
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
        lines.append(f"- `{entry.rel}` · {owner} · suggested: {suggested_target(entry.rel, entry.size)} · status: pending")
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
    haystack = _vault_text(vault)
    missing, kept_in_snapshot, total = [], [], 0
    for entry in _snapshot_entries(snapshot):
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
    print(f"migrate: {total - len(missing) - len(kept_in_snapshot)} of {total} owner paragraph(s) found in the vault, "
          f"{len(kept_in_snapshot)} kept only in the snapshot, {len(missing)} missing; {len(pending)} file(s) still pending")
    return 1 if missing or pending else 0
