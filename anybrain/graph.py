"""Links and graph: extraction, resolution, `.harness/graph.json`, `neighbors` (PRD section 13).

Resolution order for `[[x]]` (13.2): exact path, then unique path suffix (`[[concepts/x]]`), then unique
filename stem (pages first, then raw files), then frontmatter `aliases`, then `title`. Links to files in
`raw/` become `source` edges. Links inside `raw/` are never parsed.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import posixpath
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from . import vault as v

WIKILINK_RE = re.compile(r"(!?)\[\[([^\[\]\n]+?)\]\]")
# [text](dest), [text](<dest with spaces>), [text](dest(1).md), with an optional "title"
MDLINK_RE = re.compile(
    r"(!?)\[([^\[\]\n]*)\]\(\s*(?:<([^<>\n]+)>|((?:[^()\s]|\([^()\s]*\))+))(?:\s+\"[^\"]*\")?\s*\)"
)
WIKI_PIPE_RE = re.compile(r"\\?\|")
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
URL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
ATTACHMENT_RE = re.compile(r"\.[a-z0-9]{2,5}$")
CONTEXT_MAX = 160
GRAPH_FILE = ".harness/graph.json"
CONFIDENTIAL_MARK = "(confidential: local profile only)"


# ---------------------------------------------------------------------------- extraction

@dataclass
class Link:
    kind: str  # "wiki" or "md"
    target: str  # what was written, without display text and heading
    line: int  # 1-based line number in the file
    context: str  # the source line, cleaned (13.4)
    embed: bool = False
    raw: str = ""  # the full link as written


def _blank(match: re.Match) -> str:
    """Replace a match by spaces, keeping newlines, so line numbers and columns stay right."""
    return re.sub(r"[^\n]", " ", match.group(0))


def scannable_lines(body: str) -> list[str]:
    """Body lines with HTML comments, fenced code and inline code blanked out."""
    text = COMMENT_RE.sub(_blank, body)
    lines = text.split("\n")
    fenced = False
    for i, line in enumerate(lines):
        if v.FENCE_RE.match(line):
            fenced = not fenced
            lines[i] = ""
            continue
        if fenced:
            lines[i] = ""
            continue
        lines[i] = INLINE_CODE_RE.sub(_blank, line)
    return lines


def clean_context(line: str) -> str:
    """The source line stripped of Markdown, truncated to 160 characters. Wikilinks are kept."""
    s = line.strip()
    s = re.sub(r"^>\s*", "", s)
    s = re.sub(r"^#{1,6}\s+", "", s)
    s = re.sub(r"^(?:[-*+]|\d+[.)])\s+", "", s)
    s = re.sub(r"^\[[ xX]\]\s+", "", s)
    s = re.sub(r"!?\[([^\[\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"__(.+?)__", r"\1", s)
    s = re.sub(r"~~(.+?)~~", r"\1", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", s)
    s = re.sub(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])", r"\1", s)
    s = s.replace("`", "")
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > CONTEXT_MAX:
        s = s[: CONTEXT_MAX - 1].rstrip() + "…"
    return s


def extract_links(page: v.Page) -> list[Link]:
    links = []
    original = page.body.split("\n")
    for i, line in enumerate(scannable_lines(page.body)):
        if "[" not in line:
            continue
        context = None
        for match in WIKILINK_RE.finditer(line):
            inner = match.group(2)
            # `[[slug\|text]]` is how Obsidian writes an aliased link inside a table
            target = WIKI_PIPE_RE.split(inner, 1)[0].split("#", 1)[0].strip()
            if not target:
                continue  # [[#heading]] points inside the same page
            context = context or clean_context(original[i])
            links.append(Link("wiki", target, page.body_offset + i + 1, context, bool(match.group(1)), match.group(0)))
        for match in MDLINK_RE.finditer(WIKILINK_RE.sub(_blank, line)):
            target = (match.group(3) or match.group(4) or "").strip()
            if URL_SCHEME_RE.match(target) or target.startswith("#"):
                continue
            target = unquote(target.split("#", 1)[0])
            if not target:
                continue
            context = context or clean_context(original[i])
            links.append(Link("md", target, page.body_offset + i + 1, context, bool(match.group(1)), match.group(0)))
    return links


# ---------------------------------------------------------------------------- resolution

class Resolver:
    """Resolves link targets to vault-relative paths, the way Obsidian does (13.2)."""

    def __init__(self, vault: Path, pages: list[v.Page]):
        self.vault = vault
        self.pages = {p.rel: p for p in pages}
        self.files: set[str] = set()
        self._by_noext: dict[str, list[str]] = {}
        self._page_stems: dict[str, list[str]] = {}
        self._raw_stems: dict[str, list[str]] = {}
        self._basenames: dict[str, list[str]] = {}
        self._aliases: dict[str, list[str]] = {}
        self._titles: dict[str, list[str]] = {}
        for path in v.iter_files(vault):
            rel = v.rel_path(vault, path)
            self.files.add(rel)
            self._basenames.setdefault(path.name.lower(), []).append(rel)
            if rel.endswith(".md") and (rel in self.pages or v.in_raw(rel)):
                self._by_noext.setdefault(rel[:-3].lower(), []).append(rel)
                if v.in_raw(rel):
                    self._raw_stems.setdefault(path.stem.lower(), []).append(rel)
        for rel, page in self.pages.items():
            self._page_stems.setdefault(page.stem.lower(), []).append(rel)
            for alias in v.as_list(page.meta.get("aliases")):
                if isinstance(alias, str) and alias.strip():
                    self._aliases.setdefault(alias.strip().lower(), []).append(rel)
            title = page.meta.get("title")
            if isinstance(title, str) and title.strip():
                self._titles.setdefault(title.strip().lower(), []).append(rel)

    @staticmethod
    def _unique(candidates: list[str] | None) -> str | None:
        if candidates and len(set(candidates)) == 1:
            return candidates[0]
        return None

    def duplicate_stems(self) -> dict[str, list[str]]:
        """Stems shared by several pages outside raw/ (13.2: filenames must be unique)."""
        return {stem: sorted(rels) for stem, rels in self._page_stems.items() if len(rels) > 1}

    def resolve_wiki(self, target: str) -> str | None:
        key = target.strip().lstrip("/")
        lower = key.lower()
        stem_part = lower.rsplit("/", 1)[-1]
        if ATTACHMENT_RE.search(stem_part) and not lower.endswith(".md"):
            # an attachment: [[diagram.png]] or [[raw/docs/report.pdf]]
            if key in self.files:
                return key
            found = self._unique(self._basenames.get(stem_part))
            if found:
                return found
            # not a file after all ("St. Augustine"): fall through to aliases and titles
            return self._unique(self._aliases.get(lower)) or self._unique(self._titles.get(lower))
        if lower.endswith(".md"):
            lower = lower[:-3]
        found = self._unique(self._by_noext.get(lower))
        if found:
            return found
        if "/" in lower:
            suffix = "/" + lower
            matches = [rel for key, rels in self._by_noext.items() if key.endswith(suffix) for rel in rels]
            found = self._unique(matches)
            if found:
                return found
            # not a path after all ("A/B testing"): aliases, then titles (13.2)
            return self._unique(self._aliases.get(lower)) or self._unique(self._titles.get(lower))
        found = self._unique(self._page_stems.get(lower))
        if found:
            return found
        if not self._page_stems.get(lower):
            found = self._unique(self._raw_stems.get(lower))
            if found:
                return found
        found = self._unique(self._aliases.get(lower))
        if found:
            return found
        return self._unique(self._titles.get(lower))

    def resolve_md(self, target: str, from_rel: str) -> str | None:
        base = posixpath.dirname(from_rel)
        for candidate in (posixpath.join(base, target), target.lstrip("/")):
            norm = posixpath.normpath(candidate)
            if norm.startswith("../") or norm == "..":
                continue
            if norm in self.files:
                return norm
        return None

    def resolve(self, link: Link, from_rel: str) -> str | None:
        if link.kind == "wiki":
            return self.resolve_wiki(link.target)
        return self.resolve_md(link.target, from_rel)

    def resolve_page_arg(self, value: str, local: bool = True) -> str | None:
        """A page given on the command line: a vault path, or anything a wikilink accepts.

        Outside the local profile, a local-only page is found by its exact path only, so a session cannot
        confirm a hidden title or alias by guessing it (D025).
        """
        rel = value.strip().removeprefix("./")
        if rel in self.pages:
            return rel
        found = self.resolve_wiki(value)
        if found not in self.pages:
            return None
        if not local and self.pages[found].local_only:
            return None
        return found


# ---------------------------------------------------------------------------- graph

@dataclass
class Edge:
    other: str  # the page at the other end
    context: str
    line: int


@dataclass
class Node:
    page: v.Page
    out: list[Edge] = field(default_factory=list)
    inbound: list[Edge] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    broken: list[Link] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    targets: list[tuple[Link, str]] = field(default_factory=list)  # every resolved link, files included

    @property
    def sensitivity(self) -> str:
        return self.page.sensitivity

    @property
    def local_only(self) -> bool:
        return self.page.local_only


class Graph:
    def __init__(self, vault: Path, pages: list[v.Page] | None = None):
        self.vault = vault
        self.pages = pages if pages is not None else v.load_pages(vault)
        self.resolver = Resolver(vault, self.pages)
        self.nodes: dict[str, Node] = {p.rel: Node(p) for p in self.pages}
        for rel, node in self.nodes.items():
            seen_out: set[str] = set()
            for source in v.as_list(node.page.meta.get("sources")):
                if isinstance(source, str) and source not in node.sources:
                    node.sources.append(source)
            for link in extract_links(node.page):
                node.links.append(link)
                target = self.resolver.resolve(link, rel)
                if target is not None:
                    node.targets.append((link, target))
                if target is None:
                    node.broken.append(link)
                elif target == rel:
                    continue
                elif target in self.nodes:
                    if target not in seen_out:
                        seen_out.add(target)
                        node.out.append(Edge(target, link.context, link.line))
                elif v.in_raw(target):
                    if target not in node.sources:
                        node.sources.append(target)
        for rel, node in self.nodes.items():
            for edge in node.out:
                self.nodes[edge.other].inbound.append(Edge(rel, edge.context, edge.line))
        for node in self.nodes.values():
            node.inbound.sort(key=lambda e: e.other)

    def orphans(self) -> list[str]:
        """Compiled pages (library/, wiki/, projects/) with no incoming link."""
        return [rel for rel, n in self.nodes.items() if n.page.is_compiled and not n.inbound]

    def stats(self) -> dict:
        return {
            "pages": len(self.nodes),
            "edges": sum(len(n.out) for n in self.nodes.values()),
            "orphans": len(self.orphans()),
            "broken": sum(len(n.broken) for n in self.nodes.values()),
        }

    def to_json_dict(self, generated: str) -> dict:
        """The schema of 13.4. Local-only pages keep their title and structure only (D012, D025)."""
        pages = {}
        for rel in sorted(self.nodes):
            node = self.nodes[rel]
            secret = node.local_only
            pages[rel] = {
                "title": node.page.title,
                "type": node.page.type,
                "description": "" if secret else node.page.description,
                "sensitivity": node.sensitivity,
                "out": [{"to": e.other, "context": "" if secret else e.context} for e in node.out],
                "in": [
                    {"from": e.other, "context": "" if self.nodes[e.other].local_only else e.context}
                    for e in node.inbound
                ],
                "sources": [] if secret else list(node.sources),
                "broken": [] if secret else [link.raw for link in node.broken],
            }
        return {"generated": generated, "pages": pages, "stats": self.stats()}


def now_utc() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def render_json(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def graph_json_changes(vault: Path, graph: Graph) -> str | None:
    """New graph.json text if it differs from the file on disk, ignoring `generated`; else None."""
    path = vault / GRAPH_FILE
    fresh = graph.to_json_dict(now_utc())
    if path.is_file():
        try:
            old = json.loads(v.read_text(path))
        except (ValueError, OSError):
            old = None
        if isinstance(old, dict):
            comparable = dict(fresh, generated=old.get("generated"))
            if comparable == old:
                return None
    return render_json(fresh)


# ---------------------------------------------------------------------------- neighbors (13.5)

def current_profile() -> str:
    return os.environ.get("ANYBRAIN_PROFILE", "frontier").strip().lower() or "frontier"


def neighbor_entries(graph: Graph, rel: str, depth: int = 1, profile: str | None = None) -> list[dict]:
    """Linked pages out and in, then (depth 2) their neighbours, deduplicated, with distance."""
    profile = profile or current_profile()
    local = profile == "local"

    def entry(other: str, direction: str, context: str, distance: int, via: str | None) -> dict:
        node = graph.nodes[other]
        if node.local_only and not local:
            return {"distance": distance, "direction": direction, "path": None, "title": node.page.title,
                    "type": None, "description": None, "sensitivity": "confidential", "context": None,
                    "via": via, "masked": True, "_rel": other}
        return {"distance": distance, "direction": direction, "path": other, "title": node.page.title,
                "type": node.page.type, "description": node.page.description, "sensitivity": node.sensitivity,
                "context": context, "via": via, "masked": False, "_rel": other}

    def around(center: str, distance: int, via: str | None) -> list[dict]:
        node = graph.nodes[center]
        found = []
        center_secret = node.local_only and not local
        for edge in node.out:
            found.append(entry(edge.other, "out", edge.context, distance, via))
        for edge in node.inbound:
            other_secret = graph.nodes[edge.other].local_only and not local
            context = None if other_secret else edge.context
            item = entry(edge.other, "in", context, distance, via)
            found.append(item)
        if center_secret:
            for item in found:
                item["context"] = None
        return found

    first = around(rel, 1, None)
    if depth >= 2:
        seen = {rel} | {e["_rel"] for e in first}
        second = []
        for item in first:
            if item["masked"]:
                continue  # never expand through a page the profile cannot read
            for nxt in around(item["path"], 2, item["path"]):
                if nxt["_rel"] in seen:
                    continue
                seen.add(nxt["_rel"])
                second.append(nxt)
        first = first + second
    for item in first:
        item.pop("_rel", None)
    return first


def format_neighbors(graph: Graph, rel: str, entries: list[dict], depth: int) -> str:
    node = graph.nodes[rel]
    lines = [f"{node.page.title} ({node.page.type}, {node.sensitivity})"]
    if not entries:
        lines.append("  (no links in or out)")
        return "\n".join(lines)
    width = max((len(e["path"]) for e in entries if e["path"]), default=0)
    for e in entries:
        arrow = "→" if e["direction"] == "out" else "←"
        mark = f"[{e['distance']}] " if depth > 1 else ""
        if e["masked"]:
            lines.append(f"  {mark}{arrow} {e['title']} {CONFIDENTIAL_MARK}")
            continue
        via = f"   via {e['via']}" if e["via"] else ""
        lines.append(f"  {mark}{arrow} {e['path'].ljust(width)}   {e['description']}{via}".rstrip())
        if e["context"]:
            indent = " " * (6 + len(mark))
            lines.append(f'{indent}"{e["context"]}"')
    return "\n".join(lines)


def run_neighbors(vault: Path, page_arg: str, depth: int = 1, as_json: bool = False) -> int:
    graph = Graph(vault)
    profile = current_profile()
    rel = graph.resolver.resolve_page_arg(page_arg, local=profile == "local")
    if rel is None:
        raise v.VaultError(f"page not found: {page_arg}")
    node = graph.nodes[rel]
    if node.local_only and profile != "local":
        print(f"{rel} {CONFIDENTIAL_MARK}. Open a session with ANYBRAIN_PROFILE=local to see its links.",
              file=sys.stderr)
        return 1
    entries = neighbor_entries(graph, rel, depth, profile)
    if as_json:
        data = {"page": rel, "title": node.page.title, "type": node.page.type,
                "sensitivity": node.sensitivity, "depth": depth, "neighbors": entries}
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(format_neighbors(graph, rel, entries, depth))
    return 0
