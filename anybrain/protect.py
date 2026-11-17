"""Protected and append-only content (PRD section 12).

`.harness/protected.json` stores, per file (keys are file paths, so a `#` in a filename is harmless):
- for each protected section, or fully protected file, the SHA-256 of its normalised content;
- for each append-only section, or append-only file, the set of hashes of its lines.

`lint --protected` compares the vault with the store. The first time a page is seen, it is recorded
without a finding. A changed protected section, or a missing append-only line, is a finding until the
owner runs `anybrain lint <vault> --accept <path>`. Files without findings are re-recorded on every run.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import vault as v

STORE = ".harness/protected.json"
STORE_VERSION = 2

# 12.1: folder defaults, file-specific sections, fully protected files.
FOLDER_PROTECTED = {"library/": ["My take"], "projects/": ["Why"], "wiki/concepts/": ["My position"]}
FILE_PROTECTED = {"me/preferences.md": ["Owner rules"]}
FULLY_PROTECTED_FILES = ("me/aspirations.md", "me/principles.md", "me/profile.md", "me/voice.md")
FULLY_PROTECTED_DIRS = ("content/published/", "content/channels/")
# 12.2: append-only sections and files.
APPEND_ONLY_HEADINGS = ("Timeline", "Decisions", "Learned rules")
APPEND_ONLY_FILES = v.APPEND_ONLY_FILES

AGENT_NOTE_RE = re.compile(r"^>\s*Agent note \d{4}-\d{2}-\d{2}:")
COMMENT_LINE_RE = re.compile(r"^\s*<!--.*-->\s*$")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def strip_agent_notes(text: str) -> str:
    """Drop the dated agent notes the agent may append below a protected section (12.1)."""
    paragraphs = re.split(r"\n\s*\n", text.strip())
    while paragraphs:
        lines = paragraphs[-1].strip().split("\n")
        if AGENT_NOTE_RE.match(lines[0]) and all(line.lstrip().startswith(">") for line in lines):
            paragraphs.pop()
        else:
            break
    return "\n\n".join(paragraphs)


def normalise(text: str) -> str:
    """Trimmed, LF line endings, trailing spaces removed, agent notes at the end removed."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return strip_agent_notes(text).strip()


def line_hashes(lines: list[str]) -> list[str]:
    """Hashes of the non-blank lines, ignoring lines that are only an HTML guidance comment."""
    found = []
    for line in lines:
        stripped = line.strip()
        if not stripped or COMMENT_LINE_RE.match(stripped):
            continue
        digest = _sha(stripped)
        if digest not in found:
            found.append(digest)
    return found


def is_fully_protected(rel: str) -> bool:
    return rel in FULLY_PROTECTED_FILES or rel.startswith(FULLY_PROTECTED_DIRS)


def protected_headings(rel: str, fm: dict | None) -> list[str]:
    heads: list[str] = []
    for prefix, names in FOLDER_PROTECTED.items():
        if rel.startswith(prefix):
            heads += names
    heads += FILE_PROTECTED.get(rel, [])
    heads += [h for h in v.as_list((fm or {}).get("protected")) if isinstance(h, str)]
    wanted, seen = [], set()
    for head in heads:
        key = v.normalise_heading(head)
        if key and key not in seen:
            seen.add(key)
            wanted.append(key)
    return wanted


WHOLE_FILE = ""  # section key of a fully protected file


@dataclass
class Records:
    """What the store holds for one file, by section key (`My take`, `Timeline (2)`, or "" for the file)."""

    protected: dict[str, str] = field(default_factory=dict)  # section -> hash
    append_only: dict[str, list[str]] = field(default_factory=dict)  # section -> line hashes

    def as_json(self) -> dict:
        data = {}
        if self.protected:
            data["protected"] = dict(sorted(self.protected.items()))
        if self.append_only:
            data["append_only"] = dict(sorted(self.append_only.items()))
        return data


def _keyed_sections(body: str) -> list[tuple[str, v.Section]]:
    """Sections with a unique key `Title`, `Title (2)`... when a heading repeats."""
    counts: dict[str, int] = {}
    keyed = []
    for section in v.sections(body):
        counts[section.title] = counts.get(section.title, 0) + 1
        n = counts[section.title]
        keyed.append((section.title if n == 1 else f"{section.title} ({n})", section))
    return keyed


def records_for_file(vault: Path, rel: str, page: v.Page | None = None) -> Records:
    """Records of one file; pass its already loaded `page` to avoid reading and parsing it again."""
    path = vault / rel
    records = Records()
    if page is None and not path.is_file():
        return records
    text = page.text if page is not None else v.read_text(path)
    if rel in APPEND_ONLY_FILES:
        records.append_only[WHOLE_FILE] = line_hashes(text.split("\n"))
        return records
    if is_fully_protected(rel):
        records.protected[WHOLE_FILE] = _sha(normalise(text))
    if page is not None:
        fm, body = page.fm, page.body
    else:
        fm, body, _, _ = v.parse_frontmatter(text)
    heads = protected_headings(rel, fm)
    append_heads = {v.normalise_heading(h) for h in APPEND_ONLY_HEADINGS}
    for key, section in _keyed_sections(body):
        title = v.normalise_heading(section.title)
        if title in heads and not is_fully_protected(rel):
            records.protected[key] = _sha(normalise(section.content))
        if title in append_heads:
            records.append_only[key] = line_hashes(section.lines)
    return records


def is_tracked(rel: str) -> bool:
    return rel in APPEND_ONLY_FILES or v.is_page_path(rel)


def current_records(vault: Path, pages: list[v.Page] | None = None) -> dict[str, Records]:
    """Records of every page and append-only file of the vault."""
    pages = pages if pages is not None else v.load_pages(vault)
    found = {p.rel: records_for_file(vault, p.rel, p) for p in pages}
    for rel in APPEND_ONLY_FILES:
        if (vault / rel).is_file():
            found[rel] = records_for_file(vault, rel)
    return dict(sorted(found.items()))


# ---------------------------------------------------------------------------- store

def display(rel: str, section: str) -> str:
    return rel if section == WHOLE_FILE else f"{rel}#{section}"


def load_store(vault: Path) -> dict[str, Records]:
    """The store as {file: Records}. A version 1 store (keys `path#section`) is converted."""
    path = vault / STORE
    if not path.is_file():
        return {}
    try:
        data = json.loads(v.read_text(path))
    except ValueError as exc:
        raise v.VaultError(f"{STORE} is not valid JSON; fix or delete it ({exc})") from exc
    store: dict[str, Records] = {}
    if data.get("version", 1) == 1:
        for bucket in ("protected", "append_only"):
            for key, value in (data.get(bucket) or {}).items():
                rel, _, section = key.partition("#")
                getattr(store.setdefault(rel, Records()), bucket)[section] = value
        return store
    for rel, item in (data.get("files") or {}).items():
        store[rel] = Records(dict(item.get("protected") or {}), dict(item.get("append_only") or {}))
    return store


def save_store(vault: Path, store: dict[str, Records]) -> bool:
    """Write the store if it changed. Return True when written."""
    files = {rel: r.as_json() for rel, r in sorted(store.items()) if r.protected or r.append_only}
    text = json.dumps({"version": STORE_VERSION, "files": files}, indent=1, ensure_ascii=False) + "\n"
    path = vault / STORE
    if path.is_file() and v.read_text(path) == text:
        return False
    v.write_text(path, text)
    return True


@dataclass
class Problem:
    kind: str  # "protected" or "append-only"
    file: str
    section: str
    detail: str = ""

    @property
    def key(self) -> str:
        return display(self.file, self.section)

    def message(self) -> str:
        if self.kind == "protected":
            return f"{self.key} changed{self.detail}"
        return f"{self.key} {self.detail or 'line removed'}"


def check(vault: Path, pages: list[v.Page] | None = None) -> tuple[list[Problem], bool]:
    """Compare the vault with the store; re-record files without problems. Return (problems, written)."""
    store = load_store(vault)
    current = current_records(vault, pages)
    problems = []
    for rel, old in sorted(store.items()):
        new = current.get(rel, Records())
        exists = (vault / rel).is_file()
        for section, old_hash in sorted(old.protected.items()):
            new_hash = new.protected.get(section)
            if new_hash is None:
                problems.append(Problem("protected", rel, section, " (file missing)" if not exists else " (section missing)"))
            elif new_hash != old_hash:
                problems.append(Problem("protected", rel, section))
        for section, old_lines in sorted(old.append_only.items()):
            present = set(new.append_only.get(section, []))
            missing = [h for h in old_lines if h not in present]
            if missing:
                detail = "line removed" if len(missing) == 1 else f"{len(missing)} lines removed"
                if not exists:
                    detail += " (file missing)"
                problems.append(Problem("append-only", rel, section, detail))
    blocked = {p.file for p in problems}
    for rel, records in current.items():
        if rel not in blocked:
            store[rel] = records
    return problems, save_store(vault, store)


def canonical_rel(vault: Path, value: str) -> str:
    """A path typed by the owner as the vault-relative path the store uses, or VaultError."""
    raw = Path(value.strip()).expanduser()
    target = Path(os.path.normpath(raw if raw.is_absolute() else vault / raw))
    if target.exists():
        target = target.resolve()
    try:
        return v.rel_path(vault, target)
    except ValueError as exc:
        raise v.VaultError(f"not inside the vault: {value}") from exc


def accept(vault: Path, values: list[str]) -> list[str]:
    """Record the current content of these files as the owner's (12.3). Return the paths accepted."""
    vault = vault.resolve()
    store = load_store(vault)
    done = []
    for value in values:
        rel = canonical_rel(vault, value)
        exists = (vault / rel).is_file()
        if not exists and rel not in store:
            raise v.VaultError(f"nothing to accept: {rel} is not in the vault and has no record")
        if exists and not is_tracked(rel):
            raise v.VaultError(f"nothing to accept: {rel} is not a page or an append-only file")
        if exists:
            store[rel] = records_for_file(vault, rel)
        else:
            store.pop(rel, None)  # the owner confirms the file is gone
        done.append(rel)
    save_store(vault, store)
    return done
