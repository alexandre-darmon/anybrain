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
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from . import anonymize as pii
from . import feedback as fb
from . import graph as g
from . import protect
from . import vault as v

TYPES = ("book", "concept", "note", "project", "profile", "brief", "report")
BOOK_STATUS = ("to-read", "reading", "read", "abandoned")
PROJECT_STATUS = ("idea", "active", "paused", "shipped", "dropped")
PROJECT_KIND = ("portfolio", "mission", "venture", "personal")
CONFIDENCE = ("low", "medium", "high")
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
        age = _days_since(graph.nodes[rel].page.meta.get("created"), ctx.today)
        if age is not None and age < ORPHAN_GRACE_DAYS:
            continue
        findings.append(Finding("warning", "graph", f"{rel}: orphan, no page links to it", rel))
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


def _name_re(name: str) -> re.Pattern:
    return re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)", re.I)


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
    clients = [(name, _name_re(name)) for name in _client_names(ctx.vault)]
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
                hits += [(_line_of(scan, m.start()), "client name from .harness/clients.txt") for m in pattern.finditer(scan)]
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

CHECKS = {
    "schema": check_schema,
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
    """Local-only by folder (D025), by page sensitivity, or because it is raw/docs/ or .harness/ (10.3)."""
    top = rel.split("/", 1)[0]
    if top in v.LOCAL_ONLY_FOLDERS or rel.startswith(("raw/docs/", ".harness/")):
        return True
    node = ctx.graph.nodes.get(rel)
    return bool(node and node.local_only)


def masked(finding: Finding, ctx: Context, local: bool) -> Finding:
    """On a non-local profile, a finding about a local-only file keeps only its path (D016, D025)."""
    if local or not finding.path or not _local_only_path(ctx, finding.path):
        return finding
    return Finding(finding.level, finding.check,
                   f"{finding.path}: details hidden (confidential: local profile only)", finding.path)


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
    findings: list[Finding] = []
    seen = set()
    for name in selected or list(CHECKS):
        for finding in CHECKS[name](ctx):
            key = (finding.level, finding.check, finding.message)
            if key not in seen:
                seen.add(key)
                findings.append(masked(finding, ctx, local))
    for finding in findings:
        print(finding)
    errors = sum(f.level == "error" for f in findings)
    warnings = len(findings) - errors
    if report:
        print(f"wrote {write_report(ctx, report, findings)}")
    print(f"lint: {errors} error(s), {warnings} warning(s)")
    return 1 if findings and not report_only else 0
