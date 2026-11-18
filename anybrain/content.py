"""Drafts: first-version snapshots, edit ratio, content checks (PRD sections 11.10, 11.11, 15.10, 15.11).

`anybrain content <vault> snapshot <draft>` saves the agent's first version of a draft to `.harness/drafts/`.
`anybrain content <vault> diff <draft>` prints the edit ratio between that first version and the current
draft, or the published text once a published piece names the draft, and the passages the owner changed.
`anybrain content <vault> check <file>` runs the checks of `lint --content` on one file.
"""
from __future__ import annotations

import difflib
import re
from pathlib import Path

from . import vault as v

SNAPSHOTS = ".harness/drafts"
DRAFTS = "content/drafts/"
PUBLISHED = "content/published/"
MAX_PASSAGES = 20
PASSAGE_CHARS = 120


def _draft_rel(vault: Path, draft: str) -> str:
    path = Path(draft).expanduser()
    full = path if path.is_absolute() else vault / path
    try:
        rel = v.rel_path(vault.resolve(), full.resolve())
    except ValueError as exc:
        raise v.VaultError(f"{draft} is not inside the vault") from exc
    if not rel.startswith(DRAFTS) or not rel.endswith(".md") or rel.count("/") != 2:
        raise v.VaultError(f"{rel} is not a draft: drafts live directly in {DRAFTS}, with no subfolder")
    return rel


def snapshot_path(vault: Path, rel: str) -> Path:
    return vault / SNAPSHOTS / Path(rel).name


def snapshot(vault: Path, draft: str) -> int:
    """Save the agent's first version of a draft. A later call keeps it: the edit ratio measures the owner's
    edits against what the agent first wrote, not against a version the owner already changed."""
    rel = _draft_rel(vault, draft)
    source = vault / rel
    if not source.is_file():
        raise v.VaultError(f"draft not found: {rel}")
    target = snapshot_path(vault, rel)
    if target.exists():
        print(f"kept {v.rel_path(vault, target)}: the first version of {rel} is already saved")
        return 0
    v.write_text(target, v.read_text(source))
    print(f"wrote {v.rel_path(vault, target)}")
    return 0


# ---------------------------------------------------------------------------- edit ratio

def reader_text(text: str, is_draft: bool) -> str:
    """The text a reader sees, for comparison: no frontmatter, no HTML comments, no notes for the owner, and
    whitespace normalised so that reflowing a paragraph is not an edit."""
    from . import lint

    _, body, _, _ = v.parse_frontmatter(text)
    if is_draft:
        notes = v.find_section(body, lint.NOTES_HEADING)
        if notes:
            lines = body.split("\n")
            body = "\n".join(lines[:notes.heading_line] + lines[notes.end_line:])
    body = lint.HTML_COMMENT_RE.sub("", body)
    paragraphs = [" ".join(p.split()) for p in re.split(r"\n\s*\n", body)]
    return "\n\n".join(p for p in paragraphs if p)


TOKEN_RE = re.compile(r"\S+\s*|\s+")  # a word with the spaces after it


def _changed_chars(a: list[str], b: list[str]) -> int:
    changed = 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op != "equal":
            changed += max(sum(map(len, a[i1:i2])), sum(map(len, b[j1:j2])))
    return changed


def edit_ratio(first: str, final: str) -> float:
    """Share of the text changed, by characters (PRD section 6): characters deleted, inserted or replaced,
    a replacement counting as its longer side, over the length of the longer text. 0 means unchanged, 1 means
    rewritten. Additions count, so a draft the owner had to lengthen a lot is not scored as close.

    The texts are aligned word by word, then each changed passage is refined character by character, so an
    article-length text takes milliseconds instead of seconds and a one-letter fix still counts one letter.
    """
    if not first and not final:
        return 0.0
    a, b = TOKEN_RE.findall(first), TOKEN_RE.findall(final)
    changed = 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "replace":
            old, new = "".join(a[i1:i2]), "".join(b[j1:j2])
            if len(old) + len(new) <= 400:
                changed += _changed_chars(list(old), list(new))
            else:
                changed += max(len(old), len(new))
        elif op == "delete":
            changed += sum(map(len, a[i1:i2]))
        elif op == "insert":
            changed += sum(map(len, b[j1:j2]))
    return min(1.0, changed / max(len(first), len(final)))


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= PASSAGE_CHARS else text[:PASSAGE_CHARS - 3] + "..."


def passages(first: str, final: str) -> list[str]:
    """The changed passages, by words, for the owner and for the `publish` skill's summary of corrections."""
    a, b = first.split(), final.split()
    found = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        old, new = " ".join(a[i1:i2]), " ".join(b[j1:j2])
        if op == "delete":
            found.append(f"- removed: {_short(old)}")
        elif op == "insert":
            found.append(f"+ added: {_short(new)}")
        else:
            found.append(f"~ changed: {_short(old)} -> {_short(new)}")
    return found


def published_for(vault: Path, rel: str) -> Path | None:
    """The published piece whose `draft` field names this draft, if any."""
    folder = vault / PUBLISHED
    for path in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        fm, *_ = v.parse_frontmatter(v.read_text(path))
        if fm and str(fm.get("draft") or "").removeprefix("./") == rel:
            return path
    return None


def diff(vault: Path, draft: str) -> int:
    rel = _draft_rel(vault, draft)
    first_path = snapshot_path(vault, rel)
    if not first_path.is_file():
        raise v.VaultError(f"no first version of {rel}: run anybrain content snapshot when the draft is written")
    published = published_for(vault, rel)
    final_path = published or vault / rel
    if not final_path.is_file():
        raise v.VaultError(f"draft not found: {rel}")
    first = reader_text(v.read_text(first_path), is_draft=True)
    final = reader_text(v.read_text(final_path), is_draft=published is None)
    ratio = edit_ratio(first, final)
    against = v.rel_path(vault, final_path)
    print(f"edit ratio {ratio:.2f} ({round(ratio * 100)}%): {v.rel_path(vault, first_path)} -> {against}")
    from . import graph

    page = v.load_page(vault, vault / rel)
    if graph.current_profile() != "local" and (page.local_only or v.is_local_only_path(vault, against)):
        print("changed passages hidden (local-only draft: local profile only)")
        return 0
    changes = passages(first, final)
    for line in changes[:MAX_PASSAGES]:
        print(line)
    if len(changes) > MAX_PASSAGES:
        print(f"... and {len(changes) - MAX_PASSAGES} more change(s)")
    return 0


# ---------------------------------------------------------------------------- check

def check(vault: Path, file: str) -> int:
    """`lint --content` on one file. Exit 1 only on errors: an error blocks `ready` (19.4b), a warning does not."""
    from . import lint

    path = Path(file).expanduser()
    full = path if path.is_absolute() else vault / path
    if not full.is_file():
        raise v.VaultError(f"file not found: {file}")
    try:
        rel = v.rel_path(vault.resolve(), full.resolve())
    except ValueError as exc:
        raise v.VaultError(f"{file} is not inside the vault") from exc
    page = v.load_page(vault, vault / rel)
    schema = [lint.Finding("error", "schema", f"{rel}: {p}", rel) for p in lint._check_page_schema(page)]
    if page.error:
        for finding in schema:
            print(finding)
        print(f"content: {len(schema)} error(s), 0 warning(s)")
        return 1
    if page.type not in ("draft", "published"):
        raise v.VaultError(f"{rel} is not a draft or a published piece (type: {page.type or 'none'})")
    ctx = lint.Context(vault)
    patterns, problems = lint.banned_patterns(vault)
    voice = [lint.Finding("warning", "content", message, "me/voice.md") for message in problems]
    findings = lint.content_findings(ctx, page, patterns)
    for finding in schema + voice + findings:
        print(lint.masked(finding, ctx, ctx.local))
    errors = sum(f.level == "error" for f in schema + voice + findings)
    warnings = len(schema + voice + findings) - errors
    print(f"content: {errors} error(s), {warnings} warning(s)")
    return 1 if errors else 0
