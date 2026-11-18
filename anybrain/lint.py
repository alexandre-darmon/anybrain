"""Deterministic vault checks (PRD sections 12.3, 13.6 and 15.4).

`anybrain lint <vault> [--schema --links --graph --stale --protected --quotes --secrets --feedback --icloud]
[--report-only] [--report FILE] [--accept PATH]`

No check flag runs every check. Exit code 1 on any finding unless `--report-only`. `--content` arrives
with P1b (PRD section 24 step 15). On a profile other than `local`, findings about a confidential page
show the path only (docs/decisions.md D016).
"""
from __future__ import annotations

import datetime as _dt
import difflib
import os
import posixpath
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from . import anonymize as pii
from . import feedback as fb
from . import graph as g
from . import protect
from . import vault as v

TYPES = ("book", "concept", "note", "project", "profile", "brief", "report", "draft", "published", "channel")
BOOK_STATUS = ("to-read", "reading", "read", "abandoned")
PROJECT_STATUS = ("idea", "active", "paused", "shipped", "dropped")
PROJECT_KIND = ("portfolio", "mission", "venture", "personal")
CONFIDENCE = ("low", "medium", "high")
# P1b content (PRD 11.10 to 11.12)
CHANNELS = ("linkedin", "article", "newsletter", "site", "portfolio", "application", "bio", "email")
TAILORED_CHANNELS = ("application", "bio", "email")  # these need a `brief` in raw/inbox/
DRAFT_STATUS = ("idea", "drafting", "ready", "published", "dropped")
PLACEHOLDER_RE = re.compile(r"\[YOUR (?:STORY|NUMBER):")
SRC_COMMENT_RE = re.compile(r"<!--\s*src:\s*(.*?)\s*-->")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
NOTES_HEADING = "notes for the owner"
LANGUAGES = ("fr", "en")
DESCRIPTION_MAX = 150
YEAR_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

ORPHAN_GRACE_DAYS = 7  # 13.6
MAX_OUT_LINKS = 20  # 13.3 rule 3
NEAR_DUPLICATE_RATIO = 0.85  # 13.6
ONE_SOURCE_DAYS = 60  # 13.6
READING_STALE_DAYS = 90  # 15.4
NOW_LAG_DAYS = 14  # 15.4
ICLOUD_HINT = "Right-click the vault folder in Finder and choose Keep Downloaded."

NAKED_LINK_RE = re.compile(
    r"^\s*(?:(?:[-*+]|\d+[.)])\s+)?(?:\[[ xX]\]\s+)?"
    r"(?:!?\[\[[^\[\]\n]+\]\]|!?\[[^\[\]\n]*\]\([^()\n]+\))\s*[.,;:]?\s*$"
)
QUOTE_OPEN = "\"\u201c\u00ab\u201e"
QUOTE_CLOSE = "\"\u201d\u00bb\u201c"
QUOTE_FOLD = str.maketrans({"\u201c": '"', "\u201d": '"', "\u201e": '"', "\u00ab": '"', "\u00bb": '"',
                            "\u2018": "'", "\u2019": "'", "\u00a0": " ", "\u202f": " "})


@dataclass(frozen=True)
class Finding:
    level: str  # "error" or "warning"
    check: str
    message: str
    path: str = ""  # the page or file concerned, for masking and reports

    def __str__(self) -> str:
        return f"{self.level:<7} {self.check}: {self.message}"


class Context:
    """What the checks share: the vault, its pages and its graph, loaded once."""

    def __init__(self, vault: Path):
        self.vault = vault
        self._pages: list[v.Page] | None = None
        self._graph: g.Graph | None = None
        self.today = v.today()
        self.local = g.current_profile() == "local"

    @property
    def pages(self) -> list[v.Page]:
        if self._pages is None:
            self._pages = v.load_pages(self.vault)
        return self._pages

    @property
    def graph(self) -> g.Graph:
        if self._graph is None:
            self._graph = g.Graph(self.vault, self.pages)
        return self._graph


def _days_since(value, today: _dt.date) -> int | None:
    date = v.as_date(value)
    return (today - date).days if date else None


# ---------------------------------------------------------------------------- --schema

def _is_str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value)


def _check_page_schema(page: v.Page) -> list[str]:
    """Problems with one page's frontmatter and filename (PRD 11.1 to 11.7, 13.2)."""
    problems = []
    if not v.is_kebab(page.stem):
        problems.append(f"filename is not ASCII kebab-case: {page.path.name}")
    if page.error:
        problems.append(page.error)
        return problems
    fm = page.meta

    def missing(field: str) -> bool:
        value = fm.get(field)
        return value is None or (isinstance(value, str) and not value.strip())

    for field in ("title", "type", "description", "language", "created", "updated", "sensitivity"):
        if missing(field):
            problems.append(f"missing `{field}`")
    for field in ("tags", "sources"):
        if field not in fm:
            problems.append(f"missing `{field}` (may be an empty list)")
        elif fm[field] is not None and not isinstance(fm[field], list):
            problems.append(f"`{field}` must be a list")
        elif fm[field] and not _is_str_list(fm[field]):
            problems.append(f"`{field}` must be a list of strings")

    page_type = fm.get("type")
    if not missing("type") and page_type not in TYPES:
        problems.append(f"`type` is {page_type!r}, expected one of {', '.join(TYPES)}")
    if not missing("description"):
        description = str(fm["description"]).strip()
        if len(description) > DESCRIPTION_MAX:
            problems.append(f"`description` is {len(description)} characters (max {DESCRIPTION_MAX})")
    if not missing("language") and fm["language"] not in LANGUAGES:
        problems.append(f"`language` is {fm['language']!r}, expected fr or en")
    if not missing("sensitivity") and fm["sensitivity"] not in v.SENSITIVITY_LEVELS:
        problems.append(f"`sensitivity` is {fm['sensitivity']!r}, expected one of {', '.join(v.SENSITIVITY_LEVELS)}")
    for field in ("created", "updated"):
        if not missing(field) and v.as_date(fm[field]) is None:
            problems.append(f"`{field}` is not a valid date (YYYY-MM-DD)")
    for field in ("tags",):
        for tag in fm.get(field) if isinstance(fm.get(field), list) else []:
            if isinstance(tag, str) and not v.is_kebab(tag):
                problems.append(f"tag {tag!r} is not kebab-case")
    for field in ("aliases", "protected"):
        if fm.get(field) not in (None, []) and not _is_str_list(fm.get(field)):
            problems.append(f"`{field}` must be a list of strings")

    if page_type == "book":
        if missing("author"):
            problems.append("missing `author`")
        status = fm.get("status")
        if missing("status"):
            problems.append("missing `status`")
        elif status not in BOOK_STATUS:
            problems.append(f"`status` is {status!r}, expected one of {', '.join(BOOK_STATUS)}")
        rating = fm.get("rating")
        if rating is not None:
            if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5:
                problems.append(f"`rating` is {rating!r}, expected an integer from 1 to 5")
            elif status not in ("read", "abandoned"):
                problems.append("`rating` is set but `status` is not read or abandoned")
        date_read = fm.get("date_read")
        if date_read is not None and not YEAR_MONTH_RE.match(str(date_read)):
            problems.append(f"`date_read` is {date_read!r}, expected YYYY-MM")
    elif page_type == "concept":
        if missing("confidence"):
            problems.append("missing `confidence`")
        elif fm["confidence"] not in CONFIDENCE:
            problems.append(f"`confidence` is {fm['confidence']!r}, expected one of {', '.join(CONFIDENCE)}")
    elif page_type == "project":
        for field, allowed in (("status", PROJECT_STATUS), ("kind", PROJECT_KIND)):
            if missing(field):
                problems.append(f"missing `{field}`")
            elif fm[field] not in allowed:
                problems.append(f"`{field}` is {fm[field]!r}, expected one of {', '.join(allowed)}")
        if missing("next_review"):
            problems.append("missing `next_review`")
        elif v.as_date(fm["next_review"]) is None:
            problems.append("`next_review` is not a valid date (YYYY-MM-DD)")
    elif page_type == "draft":
        problems += _draft_schema(fm, missing)
    elif page_type == "published":
        problems += _published_schema(fm, missing)
    if page_type in ("draft", "published", "channel"):
        folder = {"draft": "content/drafts/", "published": "content/published/", "channel": "content/channels/"}[page_type]
        if not page.rel.startswith(folder):
            problems.append(f"a page of type {page_type} belongs in {folder}")
    return problems


def _draft_schema(fm: dict, missing) -> list[str]:
    """PRD 11.10."""
    problems = []
    channel = fm.get("channel")
    if missing("channel"):
        problems.append("missing `channel`")
    elif channel not in CHANNELS:
        problems.append(f"`channel` is {channel!r}, expected one of {', '.join(CHANNELS)}")
    for field in ("audience", "angle"):
        if missing(field):
            problems.append(f"missing `{field}`")
    if missing("status"):
        problems.append("missing `status`")
    elif fm["status"] not in DRAFT_STATUS:
        problems.append(f"`status` is {fm['status']!r}, expected one of {', '.join(DRAFT_STATUS)}")
    length = fm.get("target_length")
    if length is not None and (isinstance(length, bool) or not isinstance(length, int) or length <= 0):
        problems.append(f"`target_length` is {length!r}, expected a number of words")
    if channel in TAILORED_CHANNELS:
        brief = fm.get("brief")
        if missing("brief"):
            problems.append(f"missing `brief`: a {channel} needs the brief file in raw/inbox/")
        elif not str(brief).removeprefix("./").startswith("raw/inbox/"):
            problems.append(f"`brief` is {brief!r}, expected a file in raw/inbox/")
    return problems


def _published_schema(fm: dict, missing) -> list[str]:
    """PRD 11.11."""
    problems = []
    if missing("channel"):
        problems.append("missing `channel`")
    elif fm["channel"] not in CHANNELS:
        problems.append(f"`channel` is {fm['channel']!r}, expected one of {', '.join(CHANNELS)}")
    for field in ("url", "draft"):
        if missing(field):
            problems.append(f"missing `{field}`")
    if missing("published"):
        problems.append("missing `published`")
    elif v.as_date(fm["published"]) is None:
        problems.append("`published` is not a valid date (YYYY-MM-DD)")
    ratio = fm.get("edit_ratio")
    if ratio is None:
        problems.append("missing `edit_ratio`")
    elif isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not 0 <= ratio <= 1:
        problems.append(f"`edit_ratio` is {ratio!r}, expected a number from 0 to 1")
    for field in ("impressions", "reactions", "comments", "leads"):
        value = fm.get(field)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            problems.append(f"`{field}` is {value!r}, expected a whole number")
    return problems


def check_schema(ctx: Context) -> list[Finding]:
    findings = []
    for page in ctx.pages:
        try:
            problems = _check_page_schema(page)
        except Exception as exc:  # an unexpected value type must become a finding, never stop the run
            problems = [f"frontmatter could not be checked ({exc.__class__.__name__}: {exc})"]
        for problem in problems:
            findings.append(Finding("error", "schema", f"{page.rel}: {problem}", page.rel))
    return findings




# ---------------------------------------------------------------------------- --links and --graph (13.6)

def check_links(ctx: Context) -> list[Finding]:
    findings = []
    for rel, node in ctx.graph.nodes.items():
        for link in node.broken:
            findings.append(Finding("error", "links", f"{rel}:{link.line}: broken link {link.raw}", rel))
    return findings


def _naked_links(page: v.Page) -> list[tuple[int, str]]:
    found = []
    for i, line in enumerate(g.scannable_lines(page.body)):
        if NAKED_LINK_RE.match(line):
            found.append((page.body_offset + i + 1, line.strip()))
    return found


def _similar(matcher: difflib.SequenceMatcher, other: str) -> bool:
    matcher.set_seq1(other)  # seq2 stays cached inside the matcher
    return (matcher.real_quick_ratio() >= NEAR_DUPLICATE_RATIO and matcher.quick_ratio() >= NEAR_DUPLICATE_RATIO
            and matcher.ratio() >= NEAR_DUPLICATE_RATIO)


def _near_duplicates(concepts: list[v.Page]) -> list[tuple[v.Page, v.Page]]:
    """Concept pairs whose titles or slugs have a difflib ratio of at least 0.85 (13.6)."""
    pairs = []
    for i, a in enumerate(concepts):
        by_title = difflib.SequenceMatcher(None, autojunk=False)
        by_title.set_seq2(a.title.lower())
        by_slug = difflib.SequenceMatcher(None, autojunk=False)
        by_slug.set_seq2(a.stem)
        for b in concepts[i + 1:]:
            if _similar(by_title, b.title.lower()) or _similar(by_slug, b.stem):
                pairs.append((a, b))
    return pairs


# An author made of one of these words is no author: "Unknown", "Auteur inconnu", "Collectif", "None".
PLACEHOLDER_AUTHOR_WORDS = {"unknown", "inconnu", "inconnue", "anonymous", "anonyme", "collectif", "collective",
                            "various", "divers", "none", "null", "tbd", "nobody"}
PLACEHOLDER_AUTHORS = {"n-a", "na", "author", "auteur", "authors", "auteurs", "x"}
MAX_CANDIDATES = 3
GENERIC_TAG_PAGES = 10  # a tag on more pages, or on more than half the pages of the type, is generic


def _author_slugs(page: v.Page) -> set[str]:
    slugs = set()
    for name in v.as_list(page.meta.get("author")):
        text = str(name).strip()
        if not text or "{{" in text:
            continue
        try:
            slug = v.slugify(text)
        except ValueError:
            continue
        if slug and slug not in PLACEHOLDER_AUTHORS and not set(slug.split("-")) & PLACEHOLDER_AUTHOR_WORDS:
            slugs.add(slug)
    return slugs


def _tags(page: v.Page) -> set[str]:
    return {str(t).lower() for t in v.as_list(page.meta.get("tags"))}


def _connects_local_only(page: v.Page) -> bool:
    """Pages whose link can connect a local-only compiled page: compiled pages and confidential folders."""
    return page.is_compiled or page.folder in v.CONFIDENTIAL_FOLDERS


class CandidateIndex:
    """Facts for orphan candidates, built once per lint run and per output (local or not)."""

    def __init__(self, ctx: Context, local: bool):
        pages = [n.page for n in ctx.graph.nodes.values() if _connects_local_only(n.page)]
        self.local = local
        self.visible = {p.rel: p for p in pages if local or not p.local_only}
        self.by_author: dict[str, list[str]] = {}
        self.by_tag: dict[str, list[str]] = {}
        self.per_type: dict[str, int] = {}
        self.tag_per_type: dict[tuple[str, str], int] = {}
        for rel, page in self.visible.items():
            for slug in _author_slugs(page):
                self.by_author.setdefault(slug, []).append(rel)
            if page.is_compiled:
                self.per_type[page.type] = self.per_type.get(page.type, 0) + 1
            for tag in _tags(page):
                self.by_tag.setdefault(tag, []).append(rel)
                if page.is_compiled:
                    self.tag_per_type[(page.type, tag)] = self.tag_per_type.get((page.type, tag), 0) + 1


def candidate_index(ctx: Context, local: bool) -> CandidateIndex:
    cache = ctx.__dict__.setdefault("_candidates", {})
    if local not in cache:
        cache[local] = CandidateIndex(ctx, local)
    return cache[local]


def orphan_candidates(ctx: Context, rel: str, local: bool | None = None) -> str:
    """Pages an orphan may relate to, from facts in the vault: the same author, the pages it links to (the
    relation can be stated back), and tags it shares that most pages do not carry. Code never writes the link;
    the agent writes one only when the relation is real, or asks the owner (Q28, D039).

    `local` is the output the text goes to: outside the local profile, no local-only page is named. A
    local-only orphan only gets local-only candidates, because a public or personal page must not link to it.
    """
    local = ctx.local if local is None else local
    index = candidate_index(ctx, local)
    graph = ctx.graph
    node = graph.nodes[rel]
    page = node.page

    def allowed(other: str) -> bool:
        target = index.visible.get(other)
        if target is None or other == rel:
            return False
        return (target.local_only and _connects_local_only(target)) if page.local_only else target.is_compiled

    parts = []
    same_author = sorted({r for slug in _author_slugs(page) for r in index.by_author.get(slug, []) if allowed(r)})
    if same_author:
        parts.append("same author: " + ", ".join(same_author[:MAX_CANDIDATES]))
    linked = [e.other for e in node.out if allowed(e.other)][:MAX_CANDIDATES]
    if linked:
        parts.append("it links to " + ", ".join(linked) + " (state the relation back there if it is real)")
    same_type = index.per_type.get(page.type, 0)
    for tag in sorted(_tags(page)):
        carriers = index.by_tag.get(tag, [])
        typed = index.tag_per_type.get((page.type, tag), 0)
        # a tag most pages carry ("book", "livre") says nothing about a relation
        if len(carriers) > GENERIC_TAG_PAGES or (same_type >= 4 and typed * 2 > same_type):
            continue
        sharing = [r for r in carriers if allowed(r)]
        if sharing:
            parts.append(f"shares the tag {tag} with " + ", ".join(sharing[:MAX_CANDIDATES]))
    return "candidates: " + "; ".join(parts) if parts else ""


def orphan_finding(ctx: Context, rel: str, local: bool | None = None) -> Finding:
    page = ctx.graph.nodes[rel].page
    created = v.as_date(page.meta.get("created"))
    age = (ctx.today - created).days if created is not None else None
    young = age is not None and 0 <= age < ORPHAN_GRACE_DAYS
    where = "library/, wiki/, projects/, company/ or finance/" if page.local_only else "library/, wiki/ or projects/"
    message = f"{rel}: orphan, no page in {where} links to it"
    if young:
        message += f" (an error from {(created + _dt.timedelta(days=ORPHAN_GRACE_DAYS)).isoformat()})"
    hint = orphan_candidates(ctx, rel, local)
    message += f"; {hint}" if hint else "; no candidate found: ask the owner which page it relates to"
    return Finding("warning" if young else "error", "graph", message, rel)


def check_graph(ctx: Context) -> list[Finding]:
    graph = ctx.graph
    findings = check_links(ctx)
    for stem, rels in sorted(graph.resolver.duplicate_stems().items()):
        findings.append(Finding("error", "graph", f"duplicate filename {stem}.md: {', '.join(rels)}", rels[0]))
    for rel, node in graph.nodes.items():
        if node.sensitivity == "confidential":
            continue
        reported: set[str] = set()
        for link, target in node.targets:
            if target in reported or target == rel:
                continue
            in_confidential_folder = target.split("/", 1)[0] in v.CONFIDENTIAL_FOLDERS
            if target in graph.nodes:
                page = graph.nodes[target].page
                # a page counts as confidential when its frontmatter cannot be read; outside the confidential
                # folders that alone is a --schema problem, not a leak
                leak = in_confidential_folder or (page.sensitivity == "confidential" and page.fm is not None)
                kind = "page"
            else:
                leak, kind = in_confidential_folder, "file"
            if leak:
                reported.add(target)
                findings.append(Finding("error", "graph",
                                        f"{rel}:{link.line}: {node.sensitivity} page links to confidential {kind} {target}",
                                        rel))
    for rel in graph.orphans():
        findings.append(orphan_finding(ctx, rel))
    for rel, node in graph.nodes.items():
        for line, text in _naked_links(node.page):
            findings.append(Finding("warning", "graph", f"{rel}:{line}: naked link, say why: {text}", rel))
        if len(node.out) > MAX_OUT_LINKS:
            findings.append(Finding("warning", "graph",
                                    f"{rel}: {len(node.out)} outgoing links (max {MAX_OUT_LINKS}); split the page", rel))
    concepts = [n.page for n in graph.nodes.values() if n.page.type == "concept"]
    for a, b in _near_duplicates(concepts):
        findings.append(Finding("warning", "graph", f"near-duplicate concepts: {a.rel} and {b.rel}; merge?", a.rel))
    for page in concepts:
        sources = v.as_list(page.meta.get("sources"))
        age = _days_since(page.meta.get("created"), ctx.today)
        if len(sources) < 2 and age is not None and age >= ONE_SOURCE_DAYS:
            findings.append(Finding("warning", "graph",
                                    f"{page.rel}: concept with {len(sources)} source for {age} days (needs 2)", page.rel))
    return findings


# ---------------------------------------------------------------------------- --stale (15.4)

def _now_date(page: v.Page) -> _dt.date | None:
    """Latest date written in `## Now`, else the page's `updated` (docs/decisions.md D015)."""
    section = page.section("Now")
    dates = [v.as_date(d) for d in DATE_RE.findall(section.content)] if section else []
    dates = [d for d in dates if d]
    return max(dates) if dates else v.as_date(page.meta.get("updated"))


def _newest_timeline(page: v.Page) -> _dt.date | None:
    section = page.section("Timeline")
    if not section:
        return None
    dates = []
    for line in section.lines:
        match = re.match(r"^\s*(?:[-*+]\s+)?(\d{4}-\d{2}-\d{2})", line)
        if match and v.as_date(match.group(1)):
            dates.append(v.as_date(match.group(1)))
    return max(dates) if dates else None


def check_stale(ctx: Context) -> list[Finding]:
    findings = []
    for page in ctx.pages:
        fm = page.meta
        if page.type == "project":
            review = v.as_date(fm.get("next_review"))
            if fm.get("status") == "active" and review and review < ctx.today:
                findings.append(Finding("warning", "stale", f"{page.rel}: active project past next_review {review}", page.rel))
            now, newest = _now_date(page), _newest_timeline(page)
            if now and newest and (newest - now).days > NOW_LAG_DAYS:
                findings.append(Finding("warning", "stale",
                                        f"{page.rel}: Now is older than the newest Timeline entry ({newest}) by "
                                        f"{(newest - now).days} days", page.rel))
        if page.type == "book" and fm.get("status") == "reading":
            age = _days_since(fm.get("updated"), ctx.today)
            if age is not None and age > READING_STALE_DAYS:
                findings.append(Finding("warning", "stale", f"{page.rel}: book `reading` for {age} days", page.rel))
    return findings


# ---------------------------------------------------------------------------- --protected (12.3)

def check_protected(ctx: Context) -> list[Finding]:
    problems, written = protect.check(ctx.vault, ctx.pages)
    if written:
        print(f"wrote {protect.STORE}")
    findings = []
    for problem in problems:
        hint = f" (if the change is yours: anybrain lint <vault> --accept {problem.file})"
        findings.append(Finding("error", problem.kind, problem.message() + hint, problem.file))
    return findings


# ---------------------------------------------------------------------------- --quotes (15.4)

def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", text.translate(QUOTE_FOLD)).strip()


def extract_quote(line: str) -> str | None:
    """The quoted text of a `## Quotes` bullet: from the first opening to the last closing quote mark."""
    match = re.match(r"^\s*[-*+]\s+(.*)$", line)
    if not match:
        return None
    text = match.group(1)
    start = next((i for i, ch in enumerate(text) if ch in QUOTE_OPEN), None)
    if start is None:
        return None
    end = max((i for i, ch in enumerate(text) if ch in QUOTE_CLOSE and i > start), default=None)
    if end is None:
        return None
    quote = text[start + 1:end].strip()
    return quote or None


def check_quotes(ctx: Context) -> list[Finding]:
    findings = []
    for page in ctx.pages:
        section = page.section("Quotes")
        if not section:
            continue
        lines = g.scannable_lines(section.content)
        quotes = [(i, q) for i, line in enumerate(lines) if (q := extract_quote(line))]
        if not quotes:
            continue
        haystacks = []
        skipped = 0
        for source in v.as_list(page.meta.get("sources")):
            if not isinstance(source, str) or g.URL_SCHEME_RE.match(source):
                continue
            source = source.strip().removeprefix("./")
            path = ctx.vault / source
            if path.is_file() and not ctx.local and _local_only_path(ctx, source):
                skipped += 1  # comparing would let a session confirm a guess about a hidden file (D026)
                continue
            if path.is_file():
                try:
                    haystacks.append(_fold(v.read_text(path)))
                except UnicodeDecodeError:
                    continue
        first_line = page.body_offset + section.heading_line + 2
        if skipped and not haystacks:
            findings.append(Finding("warning", "quotes", f"{page.rel}: quotes not checked, their sources are "
                                    "local-only; run lint with ANYBRAIN_PROFILE=local", page.rel))
            continue
        for i, quote in quotes:
            if not any(_fold(quote) in hay for hay in haystacks):
                reason = "not found in its sources" if haystacks else "no readable file in `sources`"
                short = quote if len(quote) <= 60 else quote[:57] + "..."
                findings.append(Finding("error", "quotes", f'{page.rel}:{first_line + i}: quote {reason}: "{short}"', page.rel))
    return findings


# ---------------------------------------------------------------------------- --secrets (15.4, 19.4)

PII_FREE_FOLDERS = ("company/", "finance/", "raw/")


def _client_names(vault: Path) -> list[str]:
    path = vault / ".harness" / "clients.txt"
    if not path.is_file():
        return []
    names = [line.strip() for line in v.read_text(path).split("\n")]
    return sorted({n for n in names if n and not n.startswith("#")}, key=len, reverse=True)


def client_matchers(vault: Path) -> list[tuple[str, re.Pattern]]:
    """One pattern per client name, matching its spaced, joined, kebab, snake, line-wrapped and unaccented
    forms (anonymize.name_pattern, D026)."""
    return [(name, pii.name_pattern(unicodedata.normalize("NFC", name).translate(QUOTE_FOLD)))
            for name in _client_names(vault)]


def client_texts(text: str) -> list[str]:
    """The text as a client-name search must see it: composed accents, plain spaces and apostrophes, no
    emphasis stars; and the same text without accents. Both keep every line break, so line numbers hold."""
    base = unicodedata.normalize("NFC", pii.fold_spaces(text)).translate(QUOTE_FOLD).replace("*", "")
    plain = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode("ascii")
    return [base] if plain == base else [base, plain]


def _slug_in_path(name: str, rel: str) -> bool:
    """`Acme Bank` matches `raw/inbox/acme-bank-brief.md`: compare kebab-case forms."""
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", folded).strip("-")
    if not slug:
        return False
    flat = re.sub(r"[^a-z0-9]+", "-", rel.lower())
    return re.search(r"(?<![a-z0-9])" + re.escape(slug) + r"(?![a-z0-9])", flat) is not None


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def check_secrets(ctx: Context) -> list[Finding]:
    findings = []
    clients = client_matchers(ctx.vault)
    mission_pages = {p.rel for p in ctx.pages if p.folder == "projects" and p.meta.get("kind") == "mission"}
    page_rels = {p.rel for p in ctx.pages}
    for path in v.iter_files(ctx.vault):
        rel = v.rel_path(ctx.vault, path)
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\0" in raw[:4096]:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        # isbn values look like phone numbers or card numbers; they are public data
        scan = pii.fold_spaces(re.sub(r"(?m)^isbn:.*$", "", text))
        hits: list[tuple[int, str]] = []
        for label, pattern in pii.API_KEY_PATTERNS:
            hits += [(_line_of(scan, m.start()), label) for m in pattern.finditer(scan)]
        if not rel.startswith(PII_FREE_FOLDERS):
            hits += [(_line_of(scan, start), "IBAN") for start, _ in pii.find_ibans(scan)]
            hits += [(_line_of(scan, start), "card number") for start, _ in pii.find_cards(scan)]
            hits += [(_line_of(scan, start), "email address") for start, _ in pii.find_emails(scan)]
            hits += [(_line_of(scan, start), "phone number") for start, _ in pii.find_phones(scan)]
        if rel in page_rels and rel not in mission_pages:
            for name, pattern in clients:
                lines = {_line_of(form, m.start()) for form in client_texts(scan) for m in pattern.finditer(form)}
                hits += [(line, "client name from .harness/clients.txt") for line in sorted(lines)]
        if v.in_raw(rel):
            for name, pattern in clients:
                if pattern.search(rel) or _slug_in_path(name, rel):
                    hits.append((0, "client name from .harness/clients.txt in a raw/ filename"))
        for line, label in sorted(set(hits)):
            where = f"{rel}:{line}" if line else rel
            findings.append(Finding("error", "secrets", f"{where}: {label}", rel))
    return findings


# ---------------------------------------------------------------------------- --feedback (16.2, 16.4)

def check_feedback(ctx: Context) -> list[Finding]:
    findings = []
    _, bad = fb.read_log(ctx.vault)
    for number in bad:
        findings.append(Finding("warning", "feedback", f"{fb.LOG}:{number}: line does not follow the log format", fb.LOG))
    for proposal in fb.proposals(ctx.vault, ctx.today):
        findings.append(Finding("warning", "feedback",
                                f"topic:{proposal.topic} has {proposal.count} corrections in {fb.WINDOW_DAYS} days and no rule "
                                f"in {fb.PREFERENCES}; see anybrain feedback <vault> proposals", fb.LOG))
    for item in fb.rules_not_followed(ctx.vault, ctx.today):
        latest = item.corrections[0]
        reword = "; the rule was not followed twice in 30 days, consider rewording it" if item.reword else ""
        findings.append(Finding("warning", "feedback",
                                f"rule topic:{item.rule.topic} not followed: {latest.line}{reword}", fb.LOG))
    return findings


# ---------------------------------------------------------------------------- --icloud (9.5)

def check_icloud(ctx: Context) -> list[Finding]:
    findings = []
    for path in sorted(ctx.vault.rglob("*.icloud")):
        rel = v.rel_path(ctx.vault, path)
        if rel.startswith(".git/") or not path.is_file():
            continue
        findings.append(Finding("error", "icloud", f"{rel}: iCloud placeholder, the file is not downloaded. {ICLOUD_HINT}", rel))
    return findings


# ---------------------------------------------------------------------------- runner and report

# ---------------------------------------------------------------------------- --content (P1b, PRD 15.4, 19.4b)

def banned_patterns(vault: Path) -> tuple[list[tuple[str, re.Pattern]], list[str]]:
    """The patterns of `## Banned` in me/voice.md (11.13): one per line, a literal (case ignored) or `regex:`
    followed by a Python regex. Returns the patterns and the problems found reading them."""
    path = vault / "me" / "voice.md"
    if not path.is_file():
        return [], []
    section = v.find_section(v.parse_frontmatter(v.read_text(path))[1], "Banned")
    patterns, problems = [], []
    for line in (section.lines if section else []):
        text = re.sub(r"^[-*]\s+", "", HTML_COMMENT_RE.sub("", line).strip()).strip()
        if not text:
            continue
        if text.startswith("regex:"):
            try:
                patterns.append((text, re.compile(text[len("regex:"):].strip())))
            except re.error as exc:
                problems.append(f"me/voice.md: banned pattern {text!r} is not a valid regex ({exc})")
        else:
            literal = text.strip("`\"'")
            if literal:
                patterns.append((literal, re.compile(re.escape(literal), re.I)))
    return patterns, problems


def content_text(page: v.Page) -> str:
    """The text a reader sees: the body without HTML comments, and for a draft without its notes for the owner."""
    body = page.body
    if page.type == "draft":
        notes = v.find_section(body, NOTES_HEADING)
        if notes:
            lines = body.split("\n")
            body = "\n".join(lines[:notes.heading_line] + lines[notes.end_line:])
    return HTML_COMMENT_RE.sub("", body)


LINK_ITEM_RE = re.compile(r"\[\[[^\]]+\]\]|\[[^\]]*\]\([^)]+\)")


def content_sources(page: v.Page) -> list[str]:
    """The sources a draft or published piece names, as written: each item of `sources`, and each path,
    wikilink or Markdown link in a `<!-- src: ... -->` comment. URLs are left out."""
    found: list[str] = []
    items = [str(x) for x in v.as_list(page.meta.get("sources"))]
    for match in SRC_COMMENT_RE.finditer(page.body):
        text = match.group(1)
        items += LINK_ITEM_RE.findall(text)
        items += [part for part in re.split(r"[,;\s]+", LINK_ITEM_RE.sub(" ", text)) if part]
    for item in items:
        item = item.strip().strip("`'\"").rstrip(",;.").strip()
        if item and not item.startswith(("http://", "https://")) and item not in found:
            found.append(item)
    return found


def resolve_source(ctx: Context, item: str) -> tuple[str | None, str]:
    """A source as written (a path, `[[wikilink]]`, `[text](path)`, with `#anchor` or `|alias`), resolved
    the way links resolve (13.2). Returns (vault path, "") or (None, why it did not resolve)."""
    text = item
    wiki = re.fullmatch(r"\[\[([^\]]+)\]\]", text)
    markdown = re.fullmatch(r"\[[^\]]*\]\(([^)]+)\)", text)
    target = (wiki or markdown).group(1) if (wiki or markdown) else text
    target = target.split("|", 1)[0].split("#", 1)[0].strip().removeprefix("./")
    if not target:
        return None, "empty"
    norm = posixpath.normpath(target)
    if norm == ".." or norm.startswith("../") or os.path.isabs(norm):
        return None, "outside the vault"
    if (ctx.vault / norm).is_file():
        return norm, ""
    if not norm.endswith(".md") and (ctx.vault / f"{norm}.md").is_file():
        return f"{norm}.md", ""
    found = ctx.graph.resolver.resolve_wiki(norm)
    return (found, "") if found else (None, "not found in the vault")


def content_findings(ctx: Context, page: v.Page, patterns=None, clients=None) -> list[Finding]:
    """The checks of `lint --content` on one draft or published piece (15.4, 19.4b).

    Banned patterns and placeholders are errors on a draft at `ready`, the status the checks gate (19.4b);
    banned patterns are warnings on a draft still in progress. A published piece is the owner's final, fully
    protected text: those two are warnings there. A draft already published or dropped is history: they are
    not checked (docs/decisions.md D044).
    """
    if patterns is None:
        patterns = banned_patterns(ctx.vault)[0]
    if clients is None:
        clients = client_matchers(ctx.vault)
    rel, fm, findings = page.rel, page.meta, []
    is_draft = page.type == "draft"
    status = fm.get("status") if is_draft else "published"
    text = content_text(page)

    def add(level: str, message: str) -> None:
        findings.append(Finding(level, "content", f"{rel}: {message}", rel))

    if not is_draft or status not in ("published", "dropped"):
        level = "error" if is_draft and status == "ready" else "warning"
        for label, pattern in patterns:
            match = pattern.search(text)
            if match:
                add(level, f"banned pattern {label!r} from me/voice.md: {match.group(0)!r}")
        placeholders = len(PLACEHOLDER_RE.findall(text))
        if placeholders and (status == "ready" or not is_draft):
            add("error" if is_draft else "warning",
                f"{placeholders} placeholder(s) [YOUR STORY: ...] or [YOUR NUMBER: ...] left")
    sources = content_sources(page)
    if is_draft and status != "dropped" and not sources:
        add("error", "no `sources`: a draft is built from vault pages (11.10)")
    for source in sources:
        rel_source, why = resolve_source(ctx, source)
        if rel_source is None:
            # fail closed: a source nobody can check must not reach `ready`
            add("error" if status in ("ready", "published") else "warning", f"source {source} {why}")
            continue
        named = source if rel_source == source else f"{source} ({rel_source})"
        if rel_source.split("/", 1)[0].lower() in v.CONFIDENTIAL_FOLDERS:
            add("error", f"source {named} is confidential (19.4b)")
        elif v.is_page_path(rel_source) and (ctx.vault / rel_source).is_file():
            other = v.load_page(ctx.vault, ctx.vault / rel_source)
            if other.sensitivity == "confidential" or other.meta.get("kind") == "mission":
                add("error", f"source {named} is a confidential or mission page (19.4b)")
    shown = "\n".join([str(fm.get(f) or "") for f in ("title", "description", "angle")] + [text])
    if any(pattern.search(form) for _, pattern in clients for form in client_texts(shown)):
        add("error", "names a client from .harness/clients.txt (19.4)")
    channel = fm.get("channel")
    if isinstance(channel, str) and channel and not (ctx.vault / "content" / "channels" / f"{channel}.md").is_file():
        add("error", f"no channel guide content/channels/{channel}.md")
    return findings


def check_content(ctx: Context) -> list[Finding]:
    patterns, problems = banned_patterns(ctx.vault)
    clients = client_matchers(ctx.vault)
    findings = [Finding("warning", "content", message, "me/voice.md") for message in problems]
    for page in ctx.pages:
        if page.type in ("draft", "published") and page.rel.startswith(("content/drafts/", "content/published/")):
            findings += content_findings(ctx, page, patterns, clients)
    return findings


CHECKS = {
    "schema": check_schema,
    "content": check_content,
    "links": check_links,
    "graph": check_graph,
    "stale": check_stale,
    "protected": check_protected,
    "quotes": check_quotes,
    "secrets": check_secrets,
    "feedback": check_feedback,
    "icloud": check_icloud,
}


def _local_only_path(ctx: Context, rel: str) -> bool:
    """Local-only by routing (D025, D027), by page sensitivity, or because it is in .harness/."""
    if v.is_local_only_path(ctx.vault, rel):
        return True
    node = ctx.graph.nodes.get(rel)
    return bool(node and node.local_only)


def masked(finding: Finding, ctx: Context, local: bool) -> Finding:
    """For an output only the local profile may read, the finding as it is. For any other output, a finding
    about a local-only file keeps only its path (D016, D025), and an orphan's candidates are recomputed so that
    they name no local-only page, whatever profile the session runs (D032, D039)."""
    if local or not finding.path:
        return finding
    if _local_only_path(ctx, finding.path):
        return Finding(finding.level, finding.check,
                       f"{finding.path}: details hidden (confidential: local profile only)", finding.path)
    if finding.check == "graph" and finding.message.startswith(f"{finding.path}: orphan,") \
            and finding.path in ctx.graph.nodes:
        return orphan_finding(ctx, finding.path, local=False)
    return finding


def _report_is_local(ctx: Context, target: str) -> bool:
    """True when the report lands somewhere only the local profile may read.

    Both the path as written and the place it resolves to through symlinks must route local: a link from
    `journal/` to `journal/reports/` or `wiki/` would otherwise receive unmasked findings. A target that is
    itself a symlink is never trusted.
    """
    path = Path(target).expanduser()
    full = path if path.is_absolute() else ctx.vault / path
    if full.is_symlink():
        return False
    vault = ctx.vault.resolve()
    lexical = Path(os.path.normpath(full))
    resolved = Path(os.path.realpath(lexical.parent)) / lexical.name
    for form in (lexical, resolved):
        try:
            rel = v.rel_path(vault, form)
        except ValueError:
            return False  # outside the vault: assume anyone may read it
        if not v.is_local_only_path(ctx.vault, rel):
            return False
    return True


def write_report(ctx: Context, target: str, findings: list[Finding]) -> str:
    """Write the findings as a `type: report` page.

    Findings sit in fenced blocks, so the links they quote are not links of the report (the next lint would
    report them again). An existing file is replaced only when it is itself a report (D026).
    """
    path = Path(target).expanduser()
    if not path.is_absolute():
        path = ctx.vault / target
    if path.exists():
        fm, *_ = v.parse_frontmatter(v.read_text(path)) if path.is_file() else (None,)
        if not path.is_file() or not fm or fm.get("type") != "report":
            raise v.VaultError(f"refusing to overwrite {target}: it is not a lint report")
    errors = [f for f in findings if f.level == "error"]
    warnings = [f for f in findings if f.level == "warning"]
    day = ctx.today.isoformat()
    lines = [
        "---",
        f"title: Lint report {day}",
        "type: report",
        f"description: {len(errors)} error(s) and {len(warnings)} warning(s) from anybrain lint.",
        "tags: [lint]",
        "language: en",
        f"created: {day}",
        f"updated: {day}",
        "sources: []",
        "sensitivity: personal",
        "---",
        "",
    ]
    for title, group in (("Errors", errors), ("Warnings", warnings)):
        lines += [f"## {title}", ""]
        if group:
            lines += ["```text", *(f"{f.check}: {f.message}" for f in group), "```"]
        else:
            lines.append("None.")
        lines.append("")
    v.write_text(path, "\n".join(lines))
    try:
        return v.rel_path(ctx.vault, path.resolve())
    except ValueError:
        return str(path)


def run(vault: Path, selected: list[str] | None = None, report_only: bool = False,
        report: str | None = None, accept: list[str] | None = None) -> int:
    """Run the selected checks (all when none), print findings, return the exit code."""
    if accept:
        for rel in protect.accept(vault, accept):
            print(f"accepted {rel}: its protected and append-only sections are recorded as they are now")
        print(f"wrote {protect.STORE}")
        if not selected and not report:
            return 0
    ctx = Context(vault)
    local = ctx.local
    raw_findings: list[Finding] = []
    seen = set()
    for name in selected or list(CHECKS):
        for finding in CHECKS[name](ctx):
            key = (finding.level, finding.check, finding.message)
            if key not in seen:
                seen.add(key)
                raw_findings.append(finding)
    findings = [masked(f, ctx, local) for f in raw_findings]
    for finding in findings:
        print(finding)
    errors = sum(f.level == "error" for f in findings)
    warnings = len(findings) - errors
    if report:
        # the destination decides the masking: a report that frontier sessions may read never holds
        # local-only details, whichever profile wrote it (docs/decisions.md D032)
        dest_local = ctx.local and _report_is_local(ctx, report)
        unmasked = {(f.level, f.check, f.message): f for f in raw_findings}
        to_write = [masked(unmasked.get((f.level, f.check, f.message), f), ctx, dest_local) for f in raw_findings]
        print(f"wrote {write_report(ctx, report, to_write)}")
    print(f"lint: {errors} error(s), {warnings} warning(s)")
    return 1 if findings and not report_only else 0
