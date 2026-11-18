"""Run traces: what the agent did, from the harness's own machine output (PRD section 22.5).

One adapter per harness turns its machine-readable output into the same record: files read and written,
tool calls, denied calls, tokens, cost, and the local-only files read in a frontier run. A record never
holds file content, the answer text or the prompt. `anybrain trace show` prints recent records.
"""
from __future__ import annotations

import datetime as _dt
import fnmatch
import json
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from . import vault as v

TRACES_DIR = ".harness/traces"
BASELINE_DIR = ".harness/baseline"
FILES_OPENED_RE = re.compile(r"files opened:\s*(\d+)", re.I)
# Commands that do not show a file's content. Every other command is taken to read the files it names, in
# any word, even inside quoted code (`python3 -c "open('x.md')"`): conservative, so a read is not missed.
NO_READ_COMMANDS = {"ls", "stat", "find", "du", "test", "[", "[[", "echo", "printf", "mkdir", "touch", "rm",
                    "rmdir", "chmod", "chown", "ln", "cd", "pwd", "which", "type", "command", "true", "false",
                    "sleep", "date", "realpath", "readlink", "basename", "dirname", "anybrain"}
RECURSIVE_COMMANDS = {"rg", "ag", "ack"}  # search a folder tree; grep does with -r or -R
SHELL_PUNCTUATION = ";&|()<>\n"
PATHISH_RE = re.compile(r"[\w~./@+-]+")
# Texts of a refused tool call (Claude Code 2.1.326: "This command requires approval", "A variable in this
# command can't be checked before it runs", "File is in a directory that is denied by your permission settings").
DENIED_HINTS = ("denied", "not allowed", "permission", "approval", "can't be checked", "cannot be checked")


@dataclass
class ToolCall:
    tool: str
    target: str
    result: str = "ok"  # "ok", "denied" or "error"
    ran: bool = True  # False when the harness refused the call or the tool failed before doing anything
    raw: str = ""  # the target as the tool call gave it, before it was made relative to the vault

    def as_json(self) -> dict:
        return {"tool": self.tool, "target": self.target, "result": self.result}


@dataclass(frozen=True)
class Search:
    """A recursive search over a folder (`grep -r`, `rg`), with the filters that narrow the files it opens."""

    folder: str  # vault-relative, "." for the vault root
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    exclude_dirs: tuple[str, ...] = ()
    hidden: bool = True  # grep opens hidden files; rg and ag skip them unless told otherwise

    def files(self, root: Path) -> list[str]:
        base = root if self.folder in ("", ".") else root / self.folder
        if not base.is_dir():
            return []
        found = []
        for path in sorted(base.rglob("*")):
            if not path.is_file() or not path.is_relative_to(root):
                continue
            rel = v.rel_path(root, path)
            inner = path.relative_to(base).parts
            if not self.hidden and any(part.startswith(".") for part in inner):
                continue
            if any(fnmatch.fnmatch(part, g) for part in inner[:-1] for g in self.exclude_dirs):
                continue
            if self.include and not any(_glob_match(rel, path.name, g) for g in self.include):
                continue
            if any(_glob_match(rel, path.name, g) for g in self.exclude):
                continue
            found.append(rel)
        return found


def _glob_match(rel: str, name: str, glob: str) -> bool:
    return fnmatch.fnmatch(name, glob) or fnmatch.fnmatch(rel, glob) or fnmatch.fnmatch(rel, "*/" + glob)


@dataclass
class Run:
    """What one harness run did, before it becomes a trace record."""

    harness: str
    harness_version: str = ""
    model: str = ""
    answer: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    files_written: list[str] = field(default_factory=list)
    files_searched: list[str] = field(default_factory=list)  # folders a recursive search went through
    searches: list[Search] = field(default_factory=list)
    escaped_writes: list[str] = field(default_factory=list)  # writes into the owner's real vault during an eval
    tokens: dict = field(default_factory=lambda: {"input": 0, "output": 0, "cached": 0})
    cost_usd: float | None = None
    duration_ms: int | None = None
    ok: bool = False  # the harness produced a final answer
    error: str = ""
    parsed_tool_calls: bool = True  # False when the adapter cannot see tool calls


# ---------------------------------------------------------------------------- paths

def _inside(path: str, base: str) -> str | None:
    rel = os.path.relpath(path, base)
    if rel == ".." or rel.startswith("../") or os.path.isabs(rel):
        return None
    return "" if rel == "." else Path(rel).as_posix()


def _bases(root: Path, origin: Path | None) -> list[str]:
    bases = []
    for base in (root, origin):
        if base is None:
            continue
        for form in (os.path.normpath(os.path.abspath(base)), os.path.realpath(base)):
            if form not in bases:
                bases.append(form)
    return bases


def relative(target: str, root: Path, origin: Path | None = None, cwd: str | None = None) -> str:
    """A path from a tool call, relative to the vault when it points inside it.

    `..` is folded, `~` is expanded, and a path into the owner's real vault (`origin`, during an eval) maps to
    the same vault-relative path as one into the run's copy. Symlinks are followed only when the plain path
    lands outside the vault; `local_only_reads` checks both forms. A path outside the vault is kept as given.
    """
    raw = os.path.expanduser((target or "").strip())
    if not raw:
        return ""
    full = os.path.normpath(raw if os.path.isabs(raw) else os.path.join(cwd or str(root), raw))
    bases = _bases(root, origin)
    for base in bases:
        rel = _inside(full, base)
        if rel is not None:
            return rel
    real = os.path.realpath(full)
    for base in bases:
        rel = _inside(real, base)
        if rel is not None:
            return rel
    return full if os.path.isabs(raw) else os.path.normpath(raw)


def escapes(target: str, root: Path, origin: Path | None, cwd: str | None = None) -> bool:
    """True when a written path lands in the owner's real vault (`origin`) rather than in the run's copy."""
    raw = os.path.expanduser((target or "").strip())
    if not raw or origin is None:
        return False
    full = os.path.normpath(raw if os.path.isabs(raw) else os.path.join(cwd or str(root), raw))
    real = os.path.realpath(full)
    in_copy = any(_inside(form, base) is not None for form in (full, real) for base in _bases(root, None))
    in_origin = any(_inside(form, base) is not None for form in (full, real) for base in _bases(origin, None))
    return in_origin and not in_copy


def resolved_rel(rel: str, root: Path, origin: Path | None = None) -> str | None:
    """Where a vault-relative path really lands after symlinks, as a vault-relative path (None if outside)."""
    real = os.path.realpath(os.path.join(root, rel))
    for base in _bases(root, origin):
        found = _inside(real, base)
        if found is not None:
            return found
    return None


def _add(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _shell_words(command: str) -> list[str]:
    """Shell tokens with quotes honoured; operators and newlines come back as separate tokens."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=SHELL_PUNCTUATION)
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:  # unbalanced quotes: fall back to a plain split
        return re.findall(r"[;&|()<>\n]+|[^\s;&|()<>]+", command)


def _segments(command: str) -> list[tuple[list[str], list[str], list[str]]]:
    """(words, input redirections, output redirections) of each simple command."""
    segments, words, inputs, outputs = [], [], [], []
    tokens = _shell_words(command)
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token and all(c in SHELL_PUNCTUATION for c in token):
            if "<" in token or ">" in token:
                target = tokens[i + 1] if i + 1 < len(tokens) else ""
                if target and not all(c in SHELL_PUNCTUATION for c in target):
                    if words and words[-1].isdigit():
                        words.pop()  # the fd number of `2>`
                    (inputs if "<" in token else outputs).append(target)
                    i += 2
                    continue
            if words or inputs or outputs:
                segments.append((words, inputs, outputs))
            words, inputs, outputs = [], [], []
        else:
            words.append(token)
        i += 1
    if words or inputs or outputs:
        segments.append((words, inputs, outputs))
    return segments


def _is_file(rel: str, root: Path, origin: Path | None) -> bool:
    if not rel:
        return False
    if os.path.isabs(rel):
        return os.path.isfile(rel)
    return (root / rel).is_file() or bool(origin and (origin / rel).is_file())


def _is_dir(rel: str, root: Path) -> bool:
    return (root / rel).is_dir() if rel and not os.path.isabs(rel) else rel == ""


SEARCH_COMMANDS = {"grep", "egrep", "fgrep", "rg", "ag", "ack"}
OPTIONS_WITH_VALUE = {"-e", "--regexp", "-f", "--file", "-g", "--glob", "--include", "--exclude", "--exclude-dir",
                      "-m", "--max-count", "-A", "-B", "-C", "-t", "--type", "-T", "--type-not"}


def _search(name: str, words: list[str], root: Path, origin: Path | None, cwd: str) -> tuple[list[Search], set[int]]:
    """The recursive searches of a grep or rg command, and the positions of the words that are not paths."""
    recursive = name in RECURSIVE_COMMANDS
    include, exclude, exclude_dirs, positional, not_paths = [], [], [], [], set()
    hidden = name not in RECURSIVE_COMMANDS
    has_pattern_option = False
    i = 1
    while i < len(words):
        word = words[i]
        option, _, value = word.partition("=") if word.startswith("--") else (word, "", "")
        if word.startswith("-") and word != "-":
            if option in OPTIONS_WITH_VALUE and not value and i + 1 < len(words):
                value = words[i + 1]
                not_paths.add(i + 1)
                i += 1
            if option in ("-e", "--regexp", "-f", "--file"):
                has_pattern_option = True
            elif option == "--include":
                include.append(value)
            elif option == "--exclude":
                exclude.append(value)
            elif option == "--exclude-dir":
                exclude_dirs.append(value)
            elif option in ("-g", "--glob"):
                (exclude if value.startswith("!") else include).append(value.lstrip("!"))
            elif option in ("--hidden", "-uu", "-uuu"):
                hidden = True
            elif option in ("-r", "-R", "--recursive", "--dereference-recursive") or (
                    not word.startswith("--") and name not in RECURSIVE_COMMANDS and ("r" in word or "R" in word)):
                recursive = True
        else:
            positional.append(i)
        i += 1
    if not has_pattern_option and positional:
        not_paths.add(positional.pop(0))  # the pattern
    if not recursive:
        return [], not_paths
    folders = []
    for i in positional:
        rel = relative(words[i], root, origin, cwd)
        if _is_dir(rel, root):
            folders.append(rel or ".")
    if not positional:
        folders.append(relative(".", root, origin, cwd) or ".")
    searches = [Search(folder, tuple(include), tuple(exclude), tuple(exclude_dirs), hidden) for folder in folders]
    return searches, not_paths


def shell_escapes(command: str, root: Path, origin: Path | None) -> list[str]:
    """Write targets of a shell command (redirections, `tee`) that land in the owner's real vault."""
    found, cwd = [], str(root)
    for words, _, outputs in _segments(command):
        targets = list(outputs)
        if words and Path(words[0]).name == "cd":
            cwd = os.path.normpath(os.path.join(cwd, os.path.expanduser(words[1] if len(words) > 1 else str(root))))
            continue
        if words and Path(words[0]).name == "tee":
            targets += [w for w in words[1:] if not w.startswith("-")]
        for target in targets:
            if not target.isdigit() and not target.startswith(("&", "/dev/")) and escapes(target, root, origin, cwd):
                _add(found, os.path.normpath(os.path.join(cwd, target)))
    return found


def files_from_shell(command: str, root: Path, origin: Path | None = None) -> tuple[list[str], list[str], list[Search]]:
    """Files a shell command reads, files it writes, and the recursive searches it runs.

    Best effort, and conservative: any command outside NO_READ_COMMANDS reads every file it names, in any word
    or inside quoted code; `cd` is followed; a recursive search (`grep -r`, `rg`) opens the files under the
    folders it names, or the current folder, narrowed by its include and exclude globs. A script that builds a
    path at run time is not seen (docs/decisions.md D036).
    """
    read, written, searches = [], [], []
    cwd = str(root)
    for words, inputs, outputs in _segments(command):
        for target in outputs:
            if target.isdigit() or target.startswith(("&", "/dev/")):
                continue
            _add(written, relative(target, root, origin, cwd))
        for target in inputs:
            rel = relative(target, root, origin, cwd)
            if _is_file(rel, root, origin):
                _add(read, rel)
        if not words:
            continue
        name = Path(words[0]).name
        if name == "cd":
            dest = words[1] if len(words) > 1 else str(root)
            cwd = os.path.normpath(os.path.join(cwd, os.path.expanduser(dest)))
            continue
        if name == "tee":
            for word in words[1:]:
                if not word.startswith("-"):
                    _add(written, relative(word, root, origin, cwd))
            continue
        if name in NO_READ_COMMANDS:
            continue
        found, not_paths = _search(name, words, root, origin, cwd) if name in SEARCH_COMMANDS else ([], set())
        for search in found:
            if search not in searches:
                searches.append(search)
        for i, word in enumerate(words[1:], start=1):
            if i in not_paths:
                continue
            for candidate in [word] + [m for m in PATHISH_RE.findall(word) if m != word]:
                rel = relative(candidate, root, origin, cwd)
                if _is_file(rel, root, origin):
                    _add(read, rel)
    return read, written, searches


# ---------------------------------------------------------------------------- adapters

class Adapter:
    name = ""

    def output_args(self, profile: str, budget_usd: float | None) -> list[str]:
        return []

    def parse(self, output: str, root: Path, origin: Path | None = None) -> Run:  # pragma: no cover
        raise NotImplementedError


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
    return str(content or "")


class ClaudeAdapter(Adapter):
    """Claude Code, `claude -p ... --output-format stream-json --verbose` (verified on 2.1.326)."""

    name = "claude"

    def output_args(self, profile: str, budget_usd: float | None) -> list[str]:
        args = ["--output-format", "stream-json", "--verbose"]
        if budget_usd is not None:
            args += ["--max-budget-usd", f"{max(budget_usd, 0.01):.2f}"]
        return args

    def parse(self, output: str, root: Path, origin: Path | None = None) -> Run:
        run = Run("claude")
        calls: dict[str, ToolCall] = {}
        order: list[str] = []
        for line in output.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = event.get("type")
            if kind == "system" and event.get("subtype") == "init":
                run.model = event.get("model", "") or run.model
                run.harness_version = event.get("claude_code_version", "") or run.harness_version
            elif kind == "assistant":
                for item in (event.get("message") or {}).get("content") or []:
                    if item.get("type") != "tool_use":
                        continue
                    data = item.get("input") or {}
                    tool = item.get("name", "")
                    target = data.get("file_path") or data.get("command") or data.get("path") or \
                        data.get("pattern") or data.get("skill") or data.get("description") or ""
                    call = ToolCall(tool, relative(str(target), root, origin)
                                    if tool in ("Read", "Write", "Edit", "NotebookEdit") else str(target), raw=str(target))
                    calls[item.get("id", str(len(order)))] = call
                    order.append(item.get("id", str(len(order) - 1)))
            elif kind == "user":
                content = (event.get("message") or {}).get("content")
                for item in content if isinstance(content, list) else []:
                    if item.get("type") != "tool_result" or item.get("tool_use_id") not in calls:
                        continue
                    if item.get("is_error"):
                        call = calls[item["tool_use_id"]]
                        text = _result_text(item.get("content")).strip().lower()
                        if call.tool == "Bash" and text.startswith("exit code"):
                            call.result = "error"  # the command ran; its output reached the model
                        elif any(hint in text for hint in DENIED_HINTS):
                            call.result, call.ran = "denied", False
                        else:
                            # A failed shell command may still have printed a file: count its reads.
                            call.result, call.ran = "error", call.tool == "Bash"
            elif kind == "system" and event.get("subtype") == "permission_denied":
                call = calls.get(event.get("tool_use_id", ""))
                if call:
                    call.result, call.ran = "denied", False
            elif kind == "result":
                run.ok = event.get("subtype") == "success" and not event.get("is_error")
                run.answer = event.get("result") or ""
                run.cost_usd = event.get("total_cost_usd")
                run.duration_ms = event.get("duration_ms")
                usage = event.get("usage") or {}
                cached = int(usage.get("cache_read_input_tokens") or 0)
                run.tokens = {
                    "input": int(usage.get("input_tokens") or 0) + cached + int(usage.get("cache_creation_input_tokens") or 0),
                    "output": int(usage.get("output_tokens") or 0),
                    "cached": cached,
                }
                if not run.ok:
                    run.error = event.get("subtype", "error")
        run.tool_calls = [calls[i] for i in order if i in calls]
        for call in run.tool_calls:
            if call.tool == "Bash" and call.ran:
                read, written, searches = files_from_shell(call.target, root, origin)
                for item in read:
                    _add(run.files_read, item)
                for item in written:
                    _add(run.files_written, item)
                for search in searches:
                    _add(run.files_searched, search.folder)
                    if search not in run.searches:
                        run.searches.append(search)
                for item in shell_escapes(call.target, root, origin):
                    _add(run.escaped_writes, item)
            elif call.result != "ok":
                continue
            elif call.tool == "Read":
                _add(run.files_read, call.target)
            elif call.tool in ("Write", "Edit", "NotebookEdit"):
                _add(run.files_written, call.target)
                if call.raw and escapes(call.raw, root, origin):
                    _add(run.escaped_writes, call.raw)
        return run


class OpenCodeAdapter(Adapter):
    """OpenCode V2, `opencode run --format json` (newline-delimited events).

    Not verified against a real run: OpenCode is not available in the build container (step 9). It reads the
    final text and the tool calls it recognises, and says when it saw none (docs/decisions.md D033).
    """

    name = "opencode"

    def output_args(self, profile: str, budget_usd: float | None) -> list[str]:
        args = ["--format", "json"]
        if profile == "local":
            args += ["--agent", "local"]
        return args

    def parse(self, output: str, root: Path, origin: Path | None = None) -> Run:
        run = Run("opencode", parsed_tool_calls=False)
        texts = []
        for line in output.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            part = event.get("part") if isinstance(event.get("part"), dict) else event
            kind = str(part.get("type") or event.get("type") or "")
            if kind == "text" and part.get("text"):
                texts.append(str(part["text"]))
            if kind == "tool" or "tool" in part and isinstance(part.get("tool"), str):
                state = part.get("state") or {}
                data = state.get("input") or part.get("input") or {}
                target = data.get("filePath") or data.get("path") or data.get("command") or ""
                status = str(state.get("status") or "")
                call = ToolCall(str(part.get("tool")), relative(str(target), root, origin),
                                "error" if status == "error" else "ok")
                run.tool_calls.append(call)
                run.parsed_tool_calls = True
                if call.result == "ok" and call.tool == "read":
                    _add(run.files_read, call.target)
                elif call.result == "ok" and call.tool in ("write", "edit", "patch"):
                    _add(run.files_written, call.target)
            usage = part.get("tokens") if isinstance(part.get("tokens"), dict) else None
            if usage:
                run.tokens["input"] += int(usage.get("input") or 0)
                run.tokens["output"] += int(usage.get("output") or 0)
            if isinstance(part.get("cost"), (int, float)):
                run.cost_usd = (run.cost_usd or 0) + float(part["cost"])
        run.answer = texts[-1] if texts else ""
        run.ok = bool(run.answer)
        return run


ADAPTERS: dict[str, Adapter] = {"claude": ClaudeAdapter(), "opencode": OpenCodeAdapter()}


def adapter_for(harness: str) -> Adapter:
    if harness not in ADAPTERS:
        raise v.VaultError(f"no trace adapter for harness {harness!r}; known: {', '.join(ADAPTERS)}")
    return ADAPTERS[harness]


# ---------------------------------------------------------------------------- records (22.5)

def _is_local_only(root: Path, rel: str) -> bool:
    if not rel or os.path.isabs(rel) or rel.startswith(".."):
        return False
    if v.is_local_only_path(root, rel):
        return True
    return v.is_page_path(rel) and (root / rel).is_file() and v.load_page(root, root / rel).local_only


def local_only_reads(root: Path, files: list[str], origin: Path | None = None,
                     searched: list[Search | str] | None = None) -> list[str]:
    """Files read that the routing of the vault keeps local, or that are confidential pages.

    A path counts when its own form or the form it resolves to through symlinks is local-only. A folder that
    a recursive search went through counts for every local-only file under it.
    """
    found = []
    for rel in files:
        real = resolved_rel(rel, root, origin) if not os.path.isabs(rel) else None
        if _is_local_only(root, rel) or (real is not None and _is_local_only(root, real)):
            _add(found, rel)
    for search in searched or []:
        for rel in (search if isinstance(search, Search) else Search(search)).files(root):
            if _is_local_only(root, rel):
                _add(found, rel)
    return found


def self_reported_files(answer: str) -> int | None:
    matches = FILES_OPENED_RE.findall(answer or "")
    return int(matches[-1]) if matches else None


def build_record(run: Run, root: Path, profile: str, trigger: str, started: str,
                 case_id: str | None = None, skill: str | None = None, origin: Path | None = None) -> dict:
    denied = [c.as_json() for c in run.tool_calls if c.result == "denied"]
    local_reads = local_only_reads(root, run.files_read, origin, run.searches) if profile != "local" else []
    return {
        "run_id": f"{started}-{trigger}-{case_id or skill or 'session'}",
        "started": started,
        "duration_ms": run.duration_ms,
        "harness": run.harness,
        "harness_version": run.harness_version,
        "profile": profile,
        "model": run.model,
        "trigger": trigger,
        "case_id": case_id,
        "skill": skill,
        "tokens": run.tokens,
        "cost_usd": round(run.cost_usd, 4) if isinstance(run.cost_usd, (int, float)) else None,
        "tool_calls": [c.as_json() for c in run.tool_calls],
        "tool_calls_seen": run.parsed_tool_calls,
        "files_read": run.files_read,
        "files_written": run.files_written,
        "files_searched": run.files_searched,
        "escaped_writes": run.escaped_writes,
        "denied": denied,
        "local_only_read_on_frontier": local_reads,
        "self_reported_files_opened": self_reported_files(run.answer),
        "attempts": 1,
    }


def merge_records(records: list[dict]) -> dict:
    """One record for a case run several times (retries): every attempt's reads, writes, cost and tokens count.

    A local-only read in a failed attempt still fails the case, and the cost cap sees the full spend.
    """
    if len(records) == 1:
        return records[0]
    first, last = records[0], records[-1]
    merged = dict(last)
    merged["run_id"], merged["started"] = first["run_id"], first["started"]
    durations = [r.get("duration_ms") for r in records]
    merged["duration_ms"] = sum(durations) if all(isinstance(d, int) for d in durations) else None
    merged["tokens"] = {key: sum(int((r.get("tokens") or {}).get(key) or 0) for r in records)
                        for key in ("input", "output", "cached")}
    costs = [r.get("cost_usd") for r in records if isinstance(r.get("cost_usd"), (int, float))]
    merged["cost_usd"] = round(sum(costs), 4) if costs else None
    merged["tool_calls"] = [c for r in records for c in r.get("tool_calls") or []]
    merged["tool_calls_seen"] = all(r.get("tool_calls_seen", True) for r in records)
    merged["denied"] = [c for r in records for c in r.get("denied") or []]
    for key in ("files_read", "files_written", "files_searched", "local_only_read_on_frontier", "escaped_writes"):
        items: list[str] = []
        for record in records:
            for item in record.get(key) or []:
                _add(items, item)
        merged[key] = items
    merged["attempts"] = len(records)
    return merged


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------- anybrain trace show

def iter_records(vault: Path) -> list[dict]:
    """Trace records from session trace files and from eval run files, oldest first."""
    records = []
    for path in sorted((vault / TRACES_DIR).glob("*.jsonl")):
        for line in v.read_text(path).splitlines():
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
    for path in sorted((vault / BASELINE_DIR).glob("run-*.json")):
        try:
            data = json.loads(v.read_text(path))
        except ValueError:
            continue
        for result in data.get("results") or []:
            if isinstance(result.get("trace"), dict):
                records.append(result["trace"])
    return sorted(records, key=lambda r: str(r.get("started") or ""))


def format_record(record: dict) -> list[str]:
    tokens = record.get("tokens") or {}
    cost = record.get("cost_usd")
    label = record.get("case_id") or record.get("skill") or "session"
    head = (f"{str(record.get('started', ''))[:16].replace('T', ' ')}  {record.get('trigger', '?'):<7} {label:<10} "
            f"{record.get('harness', '?')} {record.get('profile', '?'):<8} "
            f"read {len(record.get('files_read') or [])}, wrote {len(record.get('files_written') or [])}, "
            f"denied {len(record.get('denied') or [])}  tokens {tokens.get('input', 0)}/{tokens.get('output', 0)}  "
            f"{f'{cost:.2f} USD' if isinstance(cost, (int, float)) else 'cost ?'}")
    lines = []
    if record.get("local_only_read_on_frontier"):
        lines.append(f"  LOCAL-ONLY READ ON FRONTIER: {', '.join(record['local_only_read_on_frontier'])}")
    lines.insert(0, head)
    claimed = record.get("self_reported_files_opened")
    if claimed is not None and claimed != len(record.get("files_read") or []):
        lines.append(f"  said files opened: {claimed}, measured: {len(record.get('files_read') or [])}")
    if record.get("tool_calls_seen") is False:
        lines.append("  tool calls not visible to this adapter")
    return lines


def show(vault: Path, last: int = 20, skill: str | None = None) -> int:
    records = iter_records(vault)
    if skill:
        records = [r for r in records if r.get("skill") == skill]
    records = records[-last:] if last > 0 else records
    if not records:
        print("no traces yet: run anybrain eval")
        return 0
    flagged = [r for r in records if r.get("local_only_read_on_frontier")]
    if flagged:
        print(f"{len(flagged)} run(s) read a local-only file on the frontier profile:")
    for record in sorted(records, key=lambda r: not r.get("local_only_read_on_frontier")):
        for line in format_record(record):
            print(line)
    return 0
