"""Local search with QMD, behind the routing of models.toml (PRD 17.2, 17.4, 13.7; docs/decisions.md D055).

QMD (github.com/tobi/qmd) indexes Markdown and answers keyword (BM25), vector and hybrid queries with small local
models. A search result carries a snippet of the file it found, so one index holding the whole vault would show
confidential text to a frontier session, past the harness deny rules. anybrain therefore keeps two QMD indexes per
vault, both outside the vault (they are derived data, rebuilt at will, and stay out of iCloud):

- `anybrain-<id>-frontier`: only what frontier models may read. Its exclusions are written from the routing table
  and from each confidential or mission page.
- `anybrain-<id>-local`: everything, for the local profile only.

`anybrain search` picks the index of the session's profile and hides any result that profile may not read, which
also covers a page that became confidential since the last refresh. There is no QMD MCP server: its `get` tool would
return any file of an index, with no mask.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml

from . import vault as v

QMD = "qmd"
COLLECTION = "brain"
EVAL_ORIGIN_ENV = "ANYBRAIN_EVAL_ORIGIN"  # the owner's vault, whose indexes an eval case searches (D055)
# hidden and generated folders no profile searches
ALWAYS_IGNORED = [".harness/**", ".obsidian/**", ".trash/**", ".claude/**", ".qmd/**", ".git/**", ".opencode/**"]
QMD_TIMEOUT = 600
DOWNLOAD_TIMEOUT = 1800  # the first download of QMD's models, about 2 GB
MODEL_HOST = "https://huggingface.co"  # where QMD downloads its models from (QMD README, "Model Configuration")
# the file names of QMD's default models, per role (QMD 2.9.0)
MODELS = {"embed": "embeddinggemma", "rerank": "reranker", "generate": "query-expansion"}
HEADER = "# Written by anybrain from .harness/models.toml (D055). Edit the routing there, not this file.\n"
INSTALL = "npm install -g @tobilu/qmd (Node.js 22 or later; on macOS also: brew install sqlite)"


def enabled(vault: Path) -> bool:
    """`[search] engine = "qmd"` in .harness/models.toml."""
    from . import install

    return str((install.load_models(vault).get("search") or {}).get("engine") or "none").lower() == "qmd"


def _index_root(vault: Path) -> Path:
    """The vault whose indexes are used: inside an eval case, the owner's vault the case was copied from."""
    copy, origin = os.environ.get(v.EVAL_COPY_ENV), os.environ.get(EVAL_ORIGIN_ENV)
    if copy and origin and Path(copy).resolve() == vault.resolve():
        return Path(origin)
    return vault


def index_names(vault: Path) -> dict[str, str]:
    key = hashlib.sha256(str(_index_root(vault).resolve()).encode()).hexdigest()[:8]
    return {profile: f"anybrain-{key}-{profile}" for profile in v.PROFILES}


def config_dir() -> Path:
    """QMD's config folder, as QMD finds it: QMD_CONFIG_DIR, else XDG_CONFIG_HOME/qmd, else ~/.config/qmd."""
    if os.environ.get("QMD_CONFIG_DIR"):
        return Path(os.environ["QMD_CONFIG_DIR"]).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(base).expanduser() / "qmd"


def frontier_ignores(vault: Path) -> list[str]:
    """What the frontier index leaves out: every folder and file the routing keeps local, the folders that are
    always local, and each page that is confidential or a mission. A frontier exception inside a local folder
    (`me/preferences.md`, `journal/reports/`) is left out too: QMD's exclusions cannot reopen a file, so the
    agent reads those pages directly."""
    routing = v.load_routing(vault)
    patterns = list(ALWAYS_IGNORED)
    folders = sorted({*routing.local_folders(), *(f"{c}/" for c in v.CONFIDENTIAL_FOLDERS)})
    patterns += [f"{folder.rstrip('/')}/**" for folder in folders]
    covered = tuple(folders)
    for rel in sorted(routing.local_files()):
        if not rel.startswith(covered):
            patterns.append(rel)
    for page in v.load_pages(vault):
        if page.local_only and not page.rel.startswith(covered) and page.rel not in patterns:
            patterns.append(page.rel)
    return patterns


def frontier_pattern(vault: Path) -> str:
    """What the frontier index takes in: every Markdown file, or, when the routing keeps everything local by
    default, only the folders and files it opens to frontier models."""
    routing = v.load_routing(vault)
    if routing.default != "local":
        return "**/*.md"
    opened = [key for key, profile in routing.rules if profile == "frontier"]
    patterns = [f"{key}**/*.md" if key.endswith("/") else key for key in opened if key.endswith(("/", ".md"))]
    return ",".join(patterns) or "nothing-is-open-to-frontier-models/*.md"


def render_config(vault: Path, profile: str) -> str:
    local = profile == "local"
    data = {"collections": {COLLECTION: {
        "path": str(_index_root(vault).resolve()),
        "pattern": "**/*.md" if local else frontier_pattern(vault),
        "ignore": list(ALWAYS_IGNORED) if local else frontier_ignores(vault),
        "includeByDefault": True}}}
    return HEADER + yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def config_paths(vault: Path) -> dict[str, Path]:
    return {profile: config_dir() / f"{name}.yml" for profile, name in index_names(vault).items()}


def write_configs(vault: Path, out=print) -> list[str]:
    """Write both index configs where QMD reads them; return the profiles whose config changed."""
    changed = []
    for profile, path in config_paths(vault).items():
        text = render_config(vault, profile)
        if path.is_file() and path.read_text(encoding="utf-8") == text:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        out(f"wrote {path}")
        changed.append(profile)
    return changed


def qmd_path() -> str:
    found = shutil.which(QMD)
    if not found:
        raise v.VaultError(f"QMD is not installed: {INSTALL}, then run anybrain search . --refresh")
    return found


def run_qmd(index: str, args: list[str], timeout: float = QMD_TIMEOUT) -> subprocess.CompletedProcess:
    """Run QMD on one named index, from a neutral folder: QMD adopts a `.qmd/` config in the folder it runs from,
    and one in the vault would hold every folder for every model."""
    try:
        return subprocess.run([qmd_path(), "--index", index, *args], capture_output=True, text=True,
                              timeout=timeout, cwd=tempfile.gettempdir())
    except subprocess.TimeoutExpired as exc:
        raise v.VaultError(f"qmd {' '.join(args[:1])} took more than {int(timeout)} seconds") from exc


def models_dir() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or "~/.cache").expanduser() / "qmd" / "models"


def models_present() -> set[str]:
    """The roles whose model QMD has already downloaded. QMD waits without end for a model it cannot download,
    so anybrain never starts a search that needs one that is missing (D055)."""
    folder = models_dir()
    names = [p.name.lower() for p in folder.rglob("*.gguf")] if folder.is_dir() else []
    return {role for role, part in MODELS.items() if any(part in name for name in names)}


def model_host_reachable(timeout: float = 10) -> bool:
    import urllib.request

    try:
        urllib.request.urlopen(urllib.request.Request(MODEL_HOST, method="HEAD"), timeout=timeout)
        return True
    except Exception:  # any failure means QMD could not download either
        return False


def _last_line(result: subprocess.CompletedProcess) -> str:
    lines = [line for line in (result.stderr or result.stdout or "").splitlines() if line.strip()]
    return lines[-1].strip() if lines else f"exit {result.returncode}"


def refresh(vault: Path) -> int:
    """Write both configs and re-index each index, so keyword search is up to date. Then download QMD's models
    if they are missing and the model host answers, and embed each index for meaning search (17.2: weekly, from
    the lint skill). Without the models, keyword search still works and the run says so."""
    if os.environ.get(v.EVAL_COPY_ENV):
        raise v.VaultError("anybrain search --refresh does not run inside an eval case")
    if not enabled(vault):
        raise v.VaultError('search is off: set [search] engine = "qmd" in .harness/models.toml, then anybrain update')
    qmd_path()
    write_configs(vault)
    names = index_names(vault)
    for profile, index in names.items():
        result = run_qmd(index, ["update"])
        if result.returncode != 0:
            print(f"{profile} index: qmd update failed: {_last_line(result)}")
            return 1
    print("keyword search: up to date")
    if len(models_present()) < len(MODELS):
        if not model_host_reachable():
            print(f"meaning search: QMD's models are not downloaded and {MODEL_HOST} does not answer from here; "
                  "keyword search works. Run anybrain search . --refresh again with network access")
            return 0
        print("downloading QMD's models (about 2 GB, once)...")
        pulled = run_qmd(names["local"], ["pull"], timeout=DOWNLOAD_TIMEOUT)
        if pulled.returncode != 0 or len(models_present()) < len(MODELS):
            print(f"meaning search: the model download did not finish ({_last_line(pulled)}); keyword search works")
            return 0
    for profile, index in names.items():
        embedded = run_qmd(index, ["embed"], timeout=DOWNLOAD_TIMEOUT)
        if embedded.returncode != 0:
            print(f"{profile} index: vectors not built ({_last_line(embedded)}); keyword search works")
            return 0
    print("meaning search: up to date")
    return 0


def _rel(file: str) -> str:
    """`qmd://brain/wiki/a.md?index=...` -> `wiki/a.md`."""
    path = re.sub(r"^qmd://[^/]+/", "", str(file or "")).split("?", 1)[0]
    from urllib.parse import unquote

    return unquote(path)


def _visible(vault: Path, rel: str, local: bool) -> bool:
    if local:
        return True
    if not rel or rel.startswith((".", "/")) or ".." in rel.split("/") or v.is_local_only_path(vault, rel):
        return False
    path = vault / rel
    return not (v.is_page_path(rel) and path.is_file() and v.load_page(vault, path).local_only)


def query(vault: Path, text: str, mode: str = "query", limit: int = 5, raw: bool = False) -> tuple[list[dict], int, str]:
    """Results of one search on the index of the session's profile: (visible results, hidden count, mode used).
    A meaning search falls back to keywords while QMD's models are missing."""
    from . import graph

    if not enabled(vault):
        raise v.VaultError('search is off: set [search] engine = "qmd" in .harness/models.toml, then anybrain update')
    local = graph.current_profile() == "local"
    index = index_names(vault)["local" if local else "frontier"]
    if not os.environ.get(v.EVAL_COPY_ENV) and write_configs(vault, out=lambda _: None):
        run_qmd(index, ["update"])  # a page changed sensitivity: re-index before searching (keywords only, fast)
    wanted = limit * 4 if raw else limit * 2  # room for results hidden or filtered out
    needs = {"query": set(MODELS), "vsearch": {"embed"}}.get(mode, set())
    used = mode if needs <= models_present() else "search"  # never wait on a model QMD cannot download
    result = run_qmd(index, [used, text, "-c", COLLECTION, "-n", str(wanted), "--format", "json"])
    if result.returncode != 0 and used != "search":
        used = "search"
        result = run_qmd(index, ["search", text, "-c", COLLECTION, "-n", str(wanted), "--format", "json"])
    if result.returncode != 0:
        raise v.VaultError(f"qmd {used} failed: {_last_line(result)}. Run anybrain search . --refresh")
    try:
        found = json.loads(result.stdout or "[]")
    except ValueError as exc:
        raise v.VaultError(f"qmd {used} gave output anybrain cannot read: {exc}") from exc
    shown, hidden = [], 0
    for item in found if isinstance(found, list) else []:
        rel = _rel(item.get("file", ""))
        if raw and not rel.startswith("raw/"):
            continue
        if not _visible(vault, rel, local):
            hidden += 1
            continue
        if rel not in {r["path"] for r in shown}:
            shown.append({"path": rel, "score": item.get("score"), "title": item.get("title") or "",
                          "snippet": item.get("snippet") or ""})
    return shown[:limit], hidden, used


def _snippet(text: str) -> list[str]:
    lines = [line for line in str(text).splitlines() if line.strip() and not line.startswith("@@")]
    return [line[:160] for line in lines[:3]]


def search(vault: Path, text: str, exact: bool = False, raw: bool = False, limit: int = 5) -> int:
    if not text.strip():
        raise v.VaultError('anybrain search needs a question: anybrain search . "<question>"')
    results, hidden, used = query(vault, text, "search" if exact else "query", limit, raw)
    if used != ("search" if exact else "query"):
        print("meaning search unavailable (QMD's models not downloaded yet): keyword results")
    if not results:
        print("no result" + (" in raw/" if raw else ""))
    for item in results:
        score = item["score"]
        print(f"{item['path']}  {score:.2f}  {item['title']}" if isinstance(score, (int, float))
              else f"{item['path']}  {item['title']}")
        for line in _snippet(item["snippet"]):
            print(f"    {line}")
    if hidden:
        print(f"{hidden} result(s) hidden: local-only (local profile only)")
    return 0


def similar(vault: Path, per_page: int = 3) -> int:
    """Link suggestions (13.7): for each compiled page, the pages a search on its description finds that are not
    linked to it in either direction. Facts for the agent to judge; no link is written."""
    from . import graph as g

    local = g.current_profile() == "local"
    graph = g.Graph(vault)
    linked = {(rel, edge.other) for rel, node in graph.nodes.items() for edge in node.out}
    pairs: list[tuple[str, str, float]] = []
    seen: set[frozenset] = set()
    for rel, node in sorted(graph.nodes.items()):
        page = node.page
        if not page.is_compiled or not page.description or (page.local_only and not local):
            continue
        try:
            results, _, _ = query(vault, page.description, "vsearch", per_page + 3)
        except v.VaultError as exc:
            print(f"link suggestions unavailable: {exc}")
            return 1
        count = 0
        for item in results:
            other = item["path"]
            if other == rel or other.rsplit("/", 1)[-1] == "index.md" or other not in graph.nodes:
                continue
            if not graph.nodes[other].page.is_compiled or (rel, other) in linked or (other, rel) in linked:
                continue
            pair = frozenset((rel, other))
            if pair in seen:
                continue
            seen.add(pair)
            pairs.append((rel, other, float(item["score"] or 0)))
            count += 1
            if count >= per_page:
                break
    if not pairs:
        print("no link suggestion: every similar pair is already linked")
    for a, b, score in pairs:
        print(f"{a} <-> {b}  {score:.2f}")
    return 0
