"""Vault access: page loading, frontmatter, sections, slugs, sensitivity defaults, path helpers.

Every other module reads the vault through this one, so the rules of PRD sections 10 to 13
(what counts as a page, how a slug is built, which sensitivity a folder implies) live here once.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

try:  # the C loader is much faster on large vaults (PRD 9.2: index and lint < 3 s on 2,000 pages)
    _Loader = yaml.CSafeLoader
except AttributeError:  # pragma: no cover
    _Loader = yaml.SafeLoader


class VaultError(Exception):
    """A problem with the vault itself (missing path, unreadable page). Reported with exit code 2."""


# ---------------------------------------------------------------------------- layout (PRD 10.2)

DOMAINS = ["me", "library", "wiki", "projects", "company", "finance", "journal", "content"]
COMPILED = ("library", "wiki", "projects")
CONFIDENTIAL_FOLDERS = ("company", "finance")
GENERATED_NAMES = {"index.md"}
ROOT_NON_PAGES = {"log.md", "AGENTS.md", "CLAUDE.md"}
APPEND_ONLY_FILES = ("log.md", "content/ideas.md")

SENSITIVITY_LEVELS = ("public", "personal", "confidential")
_RANK = {name: i for i, name in enumerate(SENSITIVITY_LEVELS)}

FM_RE = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.S)
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
FENCE_RE = re.compile(r"^[ \t]*(```|~~~)")
KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


# ---------------------------------------------------------------------------- paths

def framework_root() -> Path:
    """Directory holding `skills/` and `templates/`.

    From a checkout (editable install) it is the repo root; from a wheel it is the copy shipped
    under `anybrain/_framework/` (docs/decisions.md D002).
    """
    package = Path(__file__).resolve().parent
    repo = package.parent
    if (repo / "templates" / "AGENTS.vault.md").is_file():
        return repo
    shipped = package / "_framework"
    if (shipped / "templates" / "AGENTS.vault.md").is_file():
        return shipped
    raise VaultError("cannot find the anybrain templates; reinstall the package")


def resolve_vault(value: str | os.PathLike | None) -> Path:
    """Vault path from the argument, else ANYBRAIN_VAULT (PRD 9.2). Must be an existing folder."""
    raw = value if value else os.environ.get("ANYBRAIN_VAULT")
    if not raw:
        raise VaultError("no vault given: pass the vault path or set ANYBRAIN_VAULT")
    path = Path(raw).expanduser().resolve()
    if not path.is_dir():
        raise VaultError(f"vault not found: {path}")
    return path


def rel_path(vault: Path, path: Path) -> str:
    """Vault-relative POSIX path."""
    return path.relative_to(vault).as_posix()


def is_hidden(rel: str) -> bool:
    """True for anything under a dot folder (.harness, .obsidian, .git, .claude, .trash)."""
    return any(part.startswith(".") for part in rel.split("/")[:-1])


def in_raw(rel: str) -> bool:
    return rel == "raw" or rel.startswith("raw/")


def today() -> _dt.date:
    """Today's date. ANYBRAIN_TODAY=YYYY-MM-DD pins it, for tests and CI (docs/decisions.md D008)."""
    pinned = os.environ.get("ANYBRAIN_TODAY")
    if pinned:
        return _dt.date.fromisoformat(pinned)
    return _dt.date.today()


def as_date(value) -> _dt.date | None:
    """A YAML date, or an ISO string, as a date; None when absent or invalid."""
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, str):
        try:
            return _dt.date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def read_text(path: Path) -> str:
    """UTF-8 text with LF line endings (PRD 9.2)."""
    return path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


# ---------------------------------------------------------------------------- frontmatter

def split_frontmatter(text: str) -> tuple[str | None, str, int]:
    """Return (frontmatter text or None, body, number of lines before the body)."""
    match = FM_RE.match(text)
    if not match:
        return None, text, 0
    head = text[: match.end()]
    return match.group(1), text[match.end():], head.count("\n")


def parse_frontmatter(text: str) -> tuple[dict | None, str, int, str | None]:
    """Return (frontmatter dict or None, body, body line offset, error message or None)."""
    fm_text, body, offset = split_frontmatter(text)
    if fm_text is None:
        return None, body, offset, "no frontmatter"
    try:
        data = yaml.load(fm_text, Loader=_Loader)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 2})" if mark is not None else ""
        return None, body, offset, f"invalid YAML frontmatter{where}"
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return None, body, offset, "frontmatter is not a mapping"
    return data, body, offset, None


# ---------------------------------------------------------------------------- sections

@dataclass
class Section:
    """A heading and its content up to the next heading of the same or higher level (PRD 12.1)."""

    level: int
    title: str
    heading_line: int  # index in the body's lines
    end_line: int  # exclusive index in the body's lines
    lines: list[str] = field(default_factory=list)  # content lines, heading excluded

    @property
    def heading(self) -> str:
        return "#" * self.level + " " + self.title

    @property
    def content(self) -> str:
        return "\n".join(self.lines)


def headings(body: str) -> list[tuple[int, int, str]]:
    """(line index, level, title) for every heading outside fenced code blocks."""
    found = []
    fenced = False
    for i, line in enumerate(body.split("\n")):
        if FENCE_RE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = HEADING_RE.match(line)
        if match:
            found.append((i, len(match.group(1)), match.group(2).strip()))
    return found


def sections(body: str) -> list[Section]:
    lines = body.split("\n")
    heads = headings(body)
    result = []
    for n, (index, level, title) in enumerate(heads):
        end = len(lines)
        for later_index, later_level, _ in heads[n + 1:]:
            if later_level <= level:
                end = later_index
                break
        result.append(Section(level, title, index, end, lines[index + 1:end]))
    return result


def normalise_heading(value: str) -> str:
    """`## My take`, `My take` and `my take ` all compare equal."""
    return re.sub(r"\s+", " ", value.lstrip("#").strip()).lower()


def find_section(body: str, heading: str) -> Section | None:
    wanted = normalise_heading(heading)
    for section in sections(body):
        if normalise_heading(section.title) == wanted:
            return section
    return None


# ---------------------------------------------------------------------------- pages

@dataclass
class Page:
    path: Path
    rel: str
    text: str
    fm: dict | None
    body: str
    body_offset: int  # number of file lines before the body (for line numbers in findings)
    error: str | None = None

    @property
    def stem(self) -> str:
        return self.path.stem

    @property
    def folder(self) -> str:
        """Top-level folder (`library`, `wiki`...), or "" for a root file."""
        parts = self.rel.split("/")
        return parts[0] if len(parts) > 1 else ""

    @property
    def meta(self) -> dict:
        return self.fm or {}

    @property
    def type(self) -> str | None:
        value = self.meta.get("type")
        return str(value) if value is not None else None

    @property
    def title(self) -> str:
        value = self.meta.get("title")
        return str(value) if value else self.stem

    @property
    def description(self) -> str:
        value = self.meta.get("description")
        return str(value).strip() if value else ""

    @property
    def sensitivity(self) -> str:
        return effective_sensitivity(self.rel, self.meta)

    @property
    def is_compiled(self) -> bool:
        return self.folder in COMPILED

    def sections(self) -> list[Section]:
        return sections(self.body)

    def section(self, heading: str) -> Section | None:
        return find_section(self.body, heading)


def load_page(vault: Path, path: Path) -> Page:
    rel = rel_path(vault, path)
    try:
        text = read_text(path)
    except (OSError, UnicodeDecodeError) as exc:
        return Page(path, rel, "", None, "", 0, f"unreadable: {exc.__class__.__name__}")
    fm, body, offset, error = parse_frontmatter(text)
    return Page(path, rel, text, fm, body, offset, error)


def is_page_path(rel: str) -> bool:
    """Markdown files that are vault pages: outside raw/ and dot folders, not generated, not root files."""
    if not rel.endswith(".md") or in_raw(rel) or is_hidden(rel):
        return False
    name = rel.rsplit("/", 1)[-1]
    if name in GENERATED_NAMES:
        return False
    if "/" not in rel and name in ROOT_NON_PAGES:
        return False
    if rel in APPEND_ONLY_FILES:
        return False
    return True


def iter_page_paths(vault: Path) -> list[Path]:
    paths = []
    for path in vault.rglob("*.md"):
        if path.is_file() and is_page_path(rel_path(vault, path)):
            paths.append(path)
    return sorted(paths, key=lambda p: rel_path(vault, p))


def load_pages(vault: Path) -> list[Page]:
    """Every page of the vault, sorted by path."""
    return [load_page(vault, path) for path in iter_page_paths(vault)]


def iter_files(vault: Path, include_hidden: bool = False) -> list[Path]:
    """Every file in the vault (raw included), sorted. Dot folders only when asked."""
    found = []
    for path in vault.rglob("*"):
        if not path.is_file():
            continue
        rel = rel_path(vault, path)
        if not include_hidden and (is_hidden(rel) or rel.startswith(".")):
            continue
        found.append(path)
    return sorted(found, key=lambda p: rel_path(vault, p))


# ---------------------------------------------------------------------------- slugs (PRD 13.2)

_LEADING_ARTICLE = re.compile(r"^(?:(?:the|a|an|le|la|les|un|une)\s+|l')")


def slugify(text: str) -> str:
    """ASCII kebab-case, no accents, no leading article.

    `The Snow Leopard` -> `snow-leopard`; `création de niche` -> `creation-de-niche`;
    `$100M Offers` -> `100m-offers`. Only a leading article is dropped (docs/decisions.md D007).
    """
    value = str(text).replace(chr(0x2019), "'").replace(chr(0x2018), "'")
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii").lower().strip()
    value = _LEADING_ARTICLE.sub("", value)
    value = value.replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    if not value:
        raise ValueError(f"cannot build a slug from {text!r}")
    return value


def is_kebab(stem: str) -> bool:
    return bool(KEBAB_RE.match(stem))


# ---------------------------------------------------------------------------- sensitivity (PRD 10.3)

def default_sensitivity(rel: str, fm: dict | None = None) -> str:
    """Folder default from PRD 10.3, including the `kind: mission` and `channel: application` cases."""
    meta = fm or {}
    top = rel.split("/", 1)[0] if "/" in rel else ""
    if top in CONFIDENTIAL_FOLDERS:
        return "confidential"
    if top in ("library", "wiki"):
        return "public"
    if top == "projects":
        return "confidential" if meta.get("kind") == "mission" else "personal"
    if top == "content":
        if rel.startswith("content/drafts/") and meta.get("channel") == "application":
            return "personal"
        return "public"
    # me/, raw/, journal/ and anything else
    return "personal"


def effective_sensitivity(rel: str, fm: dict | None = None) -> str:
    """The stricter of the declared `sensitivity` and the folder default (docs/decisions.md D009).

    A page can make itself more private than its folder, never less.
    """
    default = default_sensitivity(rel, fm)
    declared = (fm or {}).get("sensitivity")
    if declared in _RANK and _RANK[declared] > _RANK[default]:
        return declared
    return default


def stricter(a: str, b: str) -> str:
    return a if _RANK.get(a, 0) >= _RANK.get(b, 0) else b
