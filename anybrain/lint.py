"""Deterministic vault checks (PRD sections 13.6 and 15.4).

`anybrain lint <vault> [--schema --links --graph --stale --protected --quotes --secrets --feedback --icloud]`
No flag runs every check. Exit code 1 on any finding unless `--report-only`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import vault as v

TYPES = ("book", "concept", "note", "project", "profile", "brief", "report")
BOOK_STATUS = ("to-read", "reading", "read", "abandoned")
PROJECT_STATUS = ("idea", "active", "paused", "shipped", "dropped")
PROJECT_KIND = ("portfolio", "mission", "venture", "personal")
CONFIDENCE = ("low", "medium", "high")
LANGUAGES = ("fr", "en")
DESCRIPTION_MAX = 150
YEAR_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


@dataclass(frozen=True)
class Finding:
    level: str  # "error" or "warning"
    check: str
    message: str

    def __str__(self) -> str:
        return f"{self.level:<7} {self.check}: {self.message}"


class Context:
    """What the checks share: the vault path and its pages, loaded once."""

    def __init__(self, vault: Path):
        self.vault = vault
        self._pages: list[v.Page] | None = None

    @property
    def pages(self) -> list[v.Page]:
        if self._pages is None:
            self._pages = v.load_pages(self.vault)
        return self._pages


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
        for tag in fm.get(field) or []:
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
        for problem in _check_page_schema(page):
            findings.append(Finding("error", "schema", f"{page.rel}: {problem}"))
    return findings


# ---------------------------------------------------------------------------- registry and runner

CHECKS = {
    "schema": check_schema,
}
PLANNED = ("links", "graph", "stale", "protected", "quotes", "secrets", "feedback", "icloud")


def run(vault: Path, selected: list[str] | None = None, report_only: bool = False) -> int:
    """Run the selected checks (all when none), print findings, return the exit code."""
    ctx = Context(vault)
    names = selected or list(CHECKS)
    findings: list[Finding] = []
    for name in names:
        if name not in CHECKS:
            print(f"lint --{name}: not built yet (PRD section 24 step 5)")
            continue
        for finding in CHECKS[name](ctx):
            if finding not in findings:
                findings.append(finding)
    for finding in findings:
        print(finding)
    errors = sum(f.level == "error" for f in findings)
    warnings = len(findings) - errors
    print(f"lint: {errors} error(s), {warnings} warning(s)")
    return 1 if findings and not report_only else 0
