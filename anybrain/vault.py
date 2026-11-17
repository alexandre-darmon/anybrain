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
    _BaseLoader = yaml.CSafeLoader
except AttributeError:  # pragma: no cover
    _BaseLoader = yaml.SafeLoader


class _Loader(_BaseLoader):
    """Safe loader where an impossible date (2026-02-30) stays a string instead of crashing.

    `lint --schema` then reports it as an invalid date, and every other command keeps working.
    """


def _timestamp_or_string(loader, node):
    try:
        return yaml.constructor.SafeConstructor.construct_yaml_timestamp(loader, node)
    except ValueError:
        return loader.construct_scalar(node)


_Loader.add_constructor("tag:yaml.org,2002:timestamp", _timestamp_or_string)


class VaultError(Exception):
    """A problem with the vault itself (missing path, unreadable page). Reported with exit code 2."""


# ---------------------------------------------------------------------------- layout (PRD 10.2)

DOMAINS = ["me", "library", "wiki", "projects", "company", "finance", "journal", "content"]
COMPILED = ("library", "wiki", "projects")
CONFIDENTIAL_FOLDERS = ("company", "finance")
PROFILES = ("frontier", "local")
# Always local, whatever models.toml says: confidential folders, API keys, the name lists used for masking,
# and the eval runs and traces, which hold answers and paths (docs/decisions.md D033). Folders end with "/".
ALWAYS_LOCAL = (
    "company/", "finance/",
    ".harness/.env", ".harness/clients.txt", ".harness/names.txt",
    ".harness/traces/", ".harness/baseline/",
)
MODELS_FILE = ".harness/models.toml"

# PRD 18.2 routing, the default when a vault has no models.toml. Folder keys end with "/"; file keys are
# exact paths and win over folders. Everything routed `local` is denied to frontier sessions by the
# harness configs and hidden by the scripts outside the local profile (docs/decisions.md D025, D027).
DEFAULT_ROUTING = {
    "default": "frontier",
    "me/": "local",
    "me/preferences.md": "frontier",
    "journal/": "local",
    "company/": "local",
    "finance/": "local",
    "raw/docs/": "local",
    "journal/reports/": "frontier",
}
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
    """Vault-relative POSIX path. String slicing first: Path.relative_to is slow on large vaults."""
    base, full = vault.as_posix().rstrip("/") + "/", path.as_posix()
    if full.startswith(base):
        return full[len(base):]
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
    """UTF-8 text with LF line endings (PRD 9.2). A byte order mark at the start is dropped."""
    return path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")


def icloud_placeholders(vault: Path) -> list[str]:
    """`*.icloud` files: iCloud evicted the real file ("Optimize Mac Storage", PRD 9.5)."""
    if not vault.is_dir():
        return []
    return sorted(rel_path(vault, p) for p in vault.rglob("*.icloud")
                  if p.is_file() and not rel_path(vault, p).startswith(".git/"))


def as_list(value) -> list:
    """A frontmatter list field as a list: a single string becomes a one-item list, None an empty one."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


# ---------------------------------------------------------------------------- routing (PRD 18.2)

@dataclass(frozen=True)
class Routing:
    """Which profile may read a path. Confidential folders are always local, whatever the file says (G6)."""

    default: str
    rules: tuple[tuple[str, str], ...]  # (normalised key, profile), folder keys end with "/"
    ignored: tuple[tuple[str, str], ...] = ()  # entries refused, with the reason

    def route(self, rel: str) -> str:
        rel = rel.strip().removeprefix("./")
        if _always_local(rel):
            return "local"
        best_len, best = -1, self.default
        for key, profile in self.rules:
            if not key.endswith("/"):
                if rel == key:
                    return profile
            elif rel.startswith(key) and len(key) > best_len:
                best_len, best = len(key), profile
        return best

    def is_local(self, rel: str) -> bool:
        return self.route(rel) == "local"

    def local_folders(self) -> list[str]:
        """Folders routed local, the always-local ones included, without nested duplicates."""
        folders = {key for key in ALWAYS_LOCAL if key.endswith("/")}
        folders |= {key for key, profile in self.rules if key.endswith("/") and profile == "local"}
        return sorted(f for f in folders if not any(f != other and f.startswith(other) for other in folders))

    def local_files(self) -> list[str]:
        """Single files routed local outside any local folder (`.harness/.env`, a page routed local)."""
        files = {key for key in ALWAYS_LOCAL if not key.endswith("/")}
        files |= {key for key, profile in self.rules if not key.endswith("/") and profile == "local"}
        folders = self.local_folders()
        return sorted(f for f in files if not any(f.startswith(folder) for folder in folders))

    def exceptions(self) -> list[str]:
        """Paths routed frontier that would be local without their own entry: what the harness must allow
        inside a denied folder (`me/preferences.md` inside `me/`)."""
        found = []
        for key, profile in self.rules:
            if profile != "frontier":
                continue
            without = Routing(self.default, tuple(rule for rule in self.rules if rule[0] != key))
            probe = key + "x" if key.endswith("/") else key
            if without.route(probe) == "local":
                found.append(key)
        return sorted(found)


def parse_routing(table: dict | None) -> Routing:
    """A `[routing]` table as a Routing. Entries that would open a confidential folder are ignored."""
    table = dict(DEFAULT_ROUTING if table is None else table)
    default = table.pop("default", "frontier")
    ignored: list[tuple[str, str]] = []
    if default not in PROFILES:
        ignored.append(("default", f"unknown profile {default!r}; using frontier"))
        default = "frontier"
    rules = []
    for raw_key, profile in table.items():
        key = str(raw_key).strip().removeprefix("./")
        if profile not in PROFILES:
            ignored.append((raw_key, f"unknown profile {profile!r}"))
            continue
        if not key or key.startswith("/") or ".." in key.split("/"):
            ignored.append((raw_key, "not a vault-relative path"))
            continue
        if profile == "frontier" and (_always_local(key) or any(a.startswith(key) for a in ALWAYS_LOCAL if key.endswith("/"))):
            ignored.append((raw_key, "confidential folders and private .harness files stay on the local profile (PRD G6)"))
            continue
        rules.append((key, profile))
    return Routing(default, tuple(sorted(rules)), tuple(ignored))


def _always_local(rel: str) -> bool:
    return any(rel == key or (key.endswith("/") and rel.startswith(key)) for key in ALWAYS_LOCAL)


DEFAULT_ROUTING_OBJ = parse_routing(None)
_ROUTING_CACHE: dict[str, tuple[float, Routing]] = {}


def load_routing(vault: Path | None) -> Routing:
    """The vault's routing from `.harness/models.toml`, or the default of PRD 18.2 when there is none."""
    if vault is None:
        return DEFAULT_ROUTING_OBJ
    path = Path(vault) / MODELS_FILE
    if not path.is_file():
        return DEFAULT_ROUTING_OBJ
    key, mtime = str(path), path.stat().st_mtime_ns
    cached = _ROUTING_CACHE.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    import tomllib

    try:
        data = tomllib.loads(read_text(path))
    except tomllib.TOMLDecodeError as exc:
        raise VaultError(f"{MODELS_FILE} is not valid TOML: {exc}") from exc
    routing = parse_routing(data.get("routing")) if "routing" in data else DEFAULT_ROUTING_OBJ
    _ROUTING_CACHE[key] = (mtime, routing)
    return routing


def is_local_only_path(vault: Path | None, rel: str) -> bool:
    """A file whose content scripts must not show outside the local profile: routed local, or always local
    (confidential folders, keys, name lists, eval runs and traces). Templates, graph.json and models.toml are
    meant to be read by every agent (PRD 10.3)."""
    return load_routing(vault).is_local(rel)


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
    vault: Path | None = None  # to read the vault's routing

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
        """Effective sensitivity. A page whose frontmatter cannot be read is treated as confidential
        until it is fixed, since it may declare `sensitivity: confidential` or `kind: mission` (D025)."""
        if self.fm is None:
            return "confidential"
        return effective_sensitivity(self.rel, self.meta)

    @property
    def local_only(self) -> bool:
        """True when scripts must not show this page's content outside the local profile: a confidential
        page, or a page the vault's routing sends to the local profile (D012, D025, D027)."""
        return self.sensitivity == "confidential" or is_local_only_path(self.vault, self.rel)

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
        return Page(path, rel, "", None, "", 0, f"unreadable: {exc.__class__.__name__}", vault)
    fm, body, offset, error = parse_frontmatter(text)
    return Page(path, rel, text, fm, body, offset, error, vault)


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
