"""Protected and append-only content (PRD section 12).

`.harness/protected.json` stores, per file:
- for each protected section, or fully protected file, the SHA-256 of its normalised content;
- for each append-only section, or append-only file, the set of hashes of its lines.

`lint --protected` compares the vault with the store. The first time a page is seen, it is recorded
without a finding. A changed protected section, or a missing append-only line, is a finding until the
owner runs `anybrain lint <vault> --accept <path>`. Files without findings are re-recorded on every run.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import vault as v

STORE = ".harness/protected.json"
STORE_VERSION = 1

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


@dataclass
class Records:
    """What the store holds for one file."""

    protected: dict[str, str] = field(default_factory=dict)  # key -> hash
    append_only: dict[str, list[str]] = field(default_factory=dict)  # key -> line hashes


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
        records.append_only[rel] = line_hashes(text.split("\n"))
        return records
    if is_fully_protected(rel):
        records.protected[rel] = _sha(normalise(text))
    if page is not None:
        fm, body = page.fm, page.body
    else:
        fm, body, _, _ = v.parse_frontmatter(text)
    heads = protected_headings(rel, fm)
    append_heads = {v.normalise_heading(h) for h in APPEND_ONLY_HEADINGS}
    for key, section in _keyed_sections(body):
        title = v.normalise_heading(section.title)
        if title in heads and not is_fully_protected(rel):
            records.protected[f"{rel}#{key}"] = _sha(normalise(section.content))
        if title in append_heads:
            records.append_only[f"{rel}#{key}"] = line_hashes(section.lines)
    return records


def current_records(vault: Path, pages: list[v.Page] | None = None) -> dict[str, Records]:
    """Records of every page and append-only file of the vault."""
    pages = pages if pages is not None else v.load_pages(vault)
    found = {p.rel: records_for_file(vault, p.rel, p) for p in pages}
    for rel in APPEND_ONLY_FILES:
        if (vault / rel).is_file():
            found[rel] = records_for_file(vault, rel)
    return dict(sorted(found.items()))


# ---------------------------------------------------------------------------- store

def _file_of(key: str) -> str:
    return key.split("#", 1)[0]


def load_store(vault: Path) -> dict:
    path = vault / STORE
    if not path.is_file():
        return {"version": STORE_VERSION, "protected": {}, "append_only": {}}
    try:
        data = json.loads(v.read_text(path))
    except ValueError as exc:
        raise v.VaultError(f"{STORE} is not valid JSON; fix or delete it ({exc})") from exc
    data.setdefault("protected", {})
    data.setdefault("append_only", {})
    data["version"] = STORE_VERSION
    return data


def save_store(vault: Path, data: dict) -> bool:
    """Write the store if it changed. Return True when written."""
    path = vault / STORE
    text = json.dumps({"version": STORE_VERSION,
                       "protected": dict(sorted(data["protected"].items())),
                       "append_only": dict(sorted(data["append_only"].items()))}, indent=1) + "\n"
    if path.is_file() and v.read_text(path) == text:
        return False
    v.write_text(path, text)
    return True


def _keys_by_file(store: dict) -> dict[str, list[tuple[str, str]]]:
    index: dict[str, list[tuple[str, str]]] = {}
    for bucket in ("protected", "append_only"):
        for key in store[bucket]:
            index.setdefault(_file_of(key), []).append((bucket, key))
    return index


def _replace_file_records(store: dict, rel: str, records: Records, index: dict | None = None) -> None:
    index = index if index is not None else _keys_by_file(store)
    for bucket, key in index.get(rel, []):
        store[bucket].pop(key, None)
    store["protected"].update(records.protected)
    store["append_only"].update(records.append_only)


@dataclass
class Problem:
    kind: str  # "protected" or "append-only"
    key: str
    detail: str = ""

    @property
    def file(self) -> str:
        return _file_of(self.key)

    def message(self) -> str:
        if self.kind == "protected":
            return f"{self.key} changed{self.detail}"
        return f"{self.key} {self.detail or 'line removed'}"


def check(vault: Path, pages: list[v.Page] | None = None) -> tuple[list[Problem], bool]:
    """Compare the vault with the store; re-record files without problems. Return (problems, written)."""
    store = load_store(vault)
    current = current_records(vault, pages)
    now_protected = {k: h for r in current.values() for k, h in r.protected.items()}
    now_append = {k: lines for r in current.values() for k, lines in r.append_only.items()}
    problems = []
    for key, old in sorted(store["protected"].items()):
        new = now_protected.get(key)
        if new is None:
            gone = "file missing" if not (vault / _file_of(key)).is_file() else "section missing"
            problems.append(Problem("protected", key, f" ({gone})"))
        elif new != old:
            problems.append(Problem("protected", key))
    for key, old_lines in sorted(store["append_only"].items()):
        new_lines = set(now_append.get(key, []))
        missing = [h for h in old_lines if h not in new_lines]
        if missing:
            detail = "line removed" if len(missing) == 1 else f"{len(missing)} lines removed"
            if key not in now_append and not (vault / _file_of(key)).is_file():
                detail += " (file missing)"
            problems.append(Problem("append-only", key, detail))
    blocked = {p.file for p in problems}
    index = _keys_by_file(store)
    for rel, records in current.items():
        if rel not in blocked:
            _replace_file_records(store, rel, records, index)
    written = save_store(vault, store)
    return problems, written


def accept(vault: Path, rels: list[str]) -> list[str]:
    """Record the current content of these files as the owner's (12.3). Return the paths accepted."""
    store = load_store(vault)
    done = []
    for value in rels:
        path = Path(value)
        rel = v.rel_path(vault, path.resolve()) if path.is_absolute() else value.strip().removeprefix("./")
        _replace_file_records(store, rel, records_for_file(vault, rel))
        done.append(rel)
    save_store(vault, store)
    return done
