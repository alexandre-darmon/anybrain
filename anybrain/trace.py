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
    cwd: str = ""  # the shell's folder when the call was made, when the harness records it (transcripts)

    def as_json(self) -> dict:
        target = shown_command(self.target) if self.tool in SHELL_TOOLS else self.target
        return {"tool": self.tool, "target": target, "result": self.result}


SHELL_TOOLS = ("Bash", "bash", "shell")
SHOWN_LIMIT = 500  # characters of a stored shell command; reads and writes are found in the full command


def shown_command(command: str) -> str:
    """A shell command as a trace stores it: the body of every here-document replaced by its line count, and
    the rest cut at 500 characters. A body is the text a command writes or runs: file content, a report, code
    (22.5: a trace never holds file content; D052)."""
    lines, kept, i = str(command or "").split("\n"), [], 0
    while i < len(lines):
        line = lines[i]
        kept.append(line)
        delimiters = [m.group(2) for m in HEREDOC_RE.finditer(line)]
        i += 1
        for delimiter in delimiters:
            body_start = i
            while i < len(lines) and lines[i].strip() != delimiter:
                i += 1
            kept.append(f"[{i - body_start} line(s)]")
            if i < len(lines):
                kept.append(lines[i])
            i += 1
    text = "\n".join(kept)
    return text if len(text) <= SHOWN_LIMIT else text[:SHOWN_LIMIT] + f" [{len(text) - SHOWN_LIMIT} more characters]"


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
        if not os.path.isdir(base):
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
    """True when a path lands in the owner's real vault (`origin`) rather than in the run's copy.

    The resolved path decides first, so a link in the copy that points into the real vault is caught; the
    plain path decides for a path that does not exist yet. `cwd` None means the folder is unknown: only an
    absolute path can then be judged.
    """
    raw = os.path.expanduser(os.path.expandvars((target or "").strip()))
    if not raw or origin is None or "$" in raw or (cwd is None and not os.path.isabs(raw)):
        return False
    full = os.path.normpath(raw if os.path.isabs(raw) else os.path.join(cwd, raw))
    origin_bases, copy_bases = _bases(origin, None), _bases(root, None)

    def lands(path: str, bases: list[str]) -> bool:
        return any(_inside(path, base) is not None for base in bases)

    real = os.path.realpath(full)
    if lands(real, origin_bases) and not lands(real, copy_bases):
        return True
    return lands(full, origin_bases) and not lands(full, copy_bases) and not lands(real, copy_bases)


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


HEREDOC_RE = re.compile(r"(?<!<)<<(?!<)-?\s*(['\"]?)([A-Za-z_][\w.-]*)\1")


INTERPRETERS = re.compile(r"(?<![\w.-])(?:ba|z|da|k|fi)?sh\b|python[\d.]*|node|ruby|perl|php|osascript|R(?:script)?\b|"
                          r"deno|bun|lua|tclsh|awk|xargs|eval|source|\.\s")
DATA_SINKS = {"cat", "tee"}


def _strip_heredocs(command: str) -> str:
    """Drop the bodies of here-documents written to a file (`cat > f <<EOF`, `tee f <<EOF`): their lines are
    data, not commands. A body that an interpreter may run (`python3 - <<EOF`, `cat <<EOF | sh`) is kept, so
    the paths it names are still seen."""
    lines, kept, i = command.split("\n"), [], 0
    while i < len(lines):
        line = lines[i]
        kept.append(line)
        delimiters = [m.group(2) for m in HEREDOC_RE.finditer(line)]
        i += 1
        if not delimiters:
            continue
        head = line[:line.index("<<")]
        first = re.split(r"&&|\|\||;", head)[-1].split()
        sink = bool(first) and Path(first[0]).name in DATA_SINKS and not INTERPRETERS.search(line)
        for delimiter in delimiters:
            body_start = i
            while i < len(lines) and lines[i].strip() != delimiter:
                i += 1
            if not sink:
                kept.extend(lines[body_start:i])
            if i < len(lines):
                kept.append("")  # the delimiter line, as an empty command
            i += 1
    return "\n".join(kept)


def _shell_words(command: str) -> list[str]:
    """Shell tokens with quotes honoured; operators and newlines come back as separate tokens."""
    command = _strip_heredocs(command)
    lexer = shlex.shlex(command, posix=True, punctuation_chars=SHELL_PUNCTUATION)
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:  # unbalanced quotes: fall back to a plain split
        return re.findall(r"[;&|()<>\n]+|[^\s;&|()<>]+", command)


def _segments(command: str) -> list[tuple[list[str], list[str], list[str], int, int]]:
    """(words, input redirections, output redirections, subshells opened before, subshells closed after) of
    each simple command."""
    segments, words, inputs, outputs, opens = [], [], [], [], 0
    tokens = _shell_words(command)
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token and all(c in SHELL_PUNCTUATION for c in token):
            if ("<" in token or ">" in token) and not ("(" in token or ")" in token):
                target = tokens[i + 1] if i + 1 < len(tokens) else ""
                if target and not all(c in SHELL_PUNCTUATION for c in target):
                    if words and words[-1].isdigit():
                        words.pop()  # the fd number of `2>`
                    (inputs if "<" in token else outputs).append(target)
                    i += 2
                    continue
            closes = token.count(")")
            if words or inputs or outputs or closes:
                segments.append((words, inputs, outputs, opens, closes))
                opens = 0
            opens += token.count("(")
            words, inputs, outputs = [], [], []
        else:
            words.append(token)
        i += 1
    if words or inputs or outputs:
        segments.append((words, inputs, outputs, opens, 0))
    return segments


def _walk(command: str, root: Path, start: str | None = None, end: dict | None = None):
    """Each simple command with the folder it runs in: `cd` and `pushd` are followed, a subshell restores the
    folder when it closes, and a folder that cannot be known (`cd -`, `popd`, an unexpanded variable) is None.
    The command starts in `start` (the vault by default); `end`, when given, receives the folder it ends in."""
    cwd: str | None = start or str(root)
    stack: list[str | None] = []
    for words, inputs, outputs, opens, closes in _segments(command):
        stack.extend([cwd] * opens)
        here = cwd
        if words and Path(words[0]).name in ("cd", "pushd", "popd"):
            args = [w for w in words[1:] if w == "-" or not w.startswith("-")]
            dest = os.path.expandvars(os.path.expanduser(args[0])) if args else os.path.expanduser("~")
            if Path(words[0]).name == "popd" or dest == "-" or "$" in dest or (cwd is None and not os.path.isabs(dest)):
                cwd = None
            else:
                cwd = os.path.normpath(os.path.join(cwd or "/", dest))
        yield words, inputs, outputs, here
        for _ in range(closes):
            cwd = stack.pop() if stack else cwd
    if end is not None:
        end["cwd"] = cwd


def _is_file(rel: str, root: Path, origin: Path | None) -> bool:
    """os.path.isfile, not Path.is_file: a shell word longer than a file name may be (a sentence in a heredoc)
    is simply not a file, where pathlib raises (D052)."""
    if not rel:
        return False
    if os.path.isabs(rel):
        return os.path.isfile(rel)
    return os.path.isfile(os.path.join(root, rel)) or bool(origin and os.path.isfile(os.path.join(origin, rel)))


def _is_dir(rel: str, root: Path) -> bool:
    return os.path.isdir(os.path.join(root, rel)) if rel and not os.path.isabs(rel) else rel == ""


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
    """Paths in the owner's real vault that a shell command names, whatever the command does.

    Conservative on purpose: an eval case has no reason to name the real vault, and a write can take any form
    (`cp`, `mv`, `rm`, `sed -i`, a script), so naming it is enough (docs/decisions.md D038).
    """
    found: list[str] = []
    if origin is None:
        return found
    # The real vault's path as text, anywhere in the command: inside quotes, code, or with spaces in it
    # (every iCloud vault has "Mobile Documents" in its path), which word splitting would cut.
    home = os.path.expanduser("~")
    forms = {os.path.normpath(os.path.abspath(origin)), os.path.realpath(origin)}
    forms |= {"~/" + os.path.relpath(f, home) for f in list(forms) if f.startswith(home + os.sep)}
    text = _strip_heredocs(command)
    for words, inputs, outputs, cwd in _walk(command, root):
        for word in words + inputs + outputs:
            expanded = os.path.expandvars(os.path.expanduser(word))
            for candidate in [expanded] + [m for m in PATHISH_RE.findall(expanded) if m != expanded]:
                if escapes(candidate, root, origin, cwd):
                    _add(found, os.path.normpath(candidate if os.path.isabs(candidate) else os.path.join(cwd, candidate)))
    if not found:
        for form in sorted(forms, key=len, reverse=True):
            if re.search(re.escape(form) + r"(?![\w.-])", text):
                _add(found, form)
                break
    return found


def files_from_shell(command: str, root: Path, origin: Path | None = None, start: str | None = None,
                     end: dict | None = None) -> tuple[list[str], list[str], list[Search]]:
    """See `_files_from_shell`. A `cd` may fail, and `cd a || cd b` runs one or the other, so the folder a
    later word is read from is not certain: every path and every recursive search is also taken from each
    folder the command's `cd`s could leave the shell in, each one working or not. A read too many is better
    than one missed (D051, D052)."""
    read, written, searches = _files_from_shell(command, root, origin, start, end)
    begin = start or str(root)
    folders = _possible_folders(command, begin)
    if len(folders) > 1:
        bare = _strip_cd(command)
        for folder in folders:
            more, _, more_searches = _files_from_shell(bare, root, origin, folder, None)
            for item in more:
                _add(read, item)
            for search in more_searches:
                if search not in searches:
                    searches.append(search)
    return read, written, searches


FOLDERS_LIMIT = 32


def _possible_folders(command: str, begin: str) -> list[str]:
    """The folder the command starts in, and every folder its `cd`s could reach from it, each `cd` working or
    failing: `cd a || cd b` gives the start, a, b and a/b."""
    folders = [begin]
    for words, *_ in _segments(command):
        if not words or Path(words[0]).name not in ("cd", "pushd"):
            continue
        args = [w for w in words[1:] if w == "-" or not w.startswith("-")]
        dest = os.path.expandvars(os.path.expanduser(args[0])) if args else os.path.expanduser("~")
        if dest == "-" or "$" in dest:
            continue
        for folder in list(folders):
            reached = os.path.normpath(os.path.join(folder, dest))
            if reached not in folders and len(folders) < FOLDERS_LIMIT:
                folders.append(reached)
    return folders


def _strip_cd(command: str) -> str:
    """The command with every `cd`, `pushd` and `popd` turned into a no-op, so it is read from where it began."""
    return re.sub(r"(^|[;&|(\n]\s*)(?:cd|pushd|popd)\b[^;&|)\n]*", r"\1true", command)


def _files_from_shell(command: str, root: Path, origin: Path | None = None, start: str | None = None,
                      end: dict | None = None) -> tuple[list[str], list[str], list[Search]]:
    """Files a shell command reads, files it writes, and the recursive searches it runs.

    Best effort, and conservative: any command outside NO_READ_COMMANDS reads every file it names, in any word
    or inside quoted code; `cd` is followed; a recursive search (`grep -r`, `rg`) opens the files under the
    folders it names, or the current folder, narrowed by its include and exclude globs. A script that builds a
    path at run time is not seen (docs/decisions.md D036).
    """
    read, written, searches = [], [], []
    for words, inputs, outputs, here in _walk(command, root, start, end):
        cwd = here or str(root)  # an unknown folder: read paths as relative to the vault, best effort
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
        if name in ("cd", "pushd", "popd"):
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
        # the first word too, when it is not a command but a path: a line of code in a heredoc an interpreter runs
        for i, word in enumerate(words):
            if i in not_paths or (i == 0 and not _is_file(relative(word, root, origin, cwd), root, origin)):
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

    def parse(self, output: str, root: Path, origin: Path | None = None, cwd: str | None = None) -> Run:
        """`cwd` is the folder the session started in, the vault by default. The shell's folder carries from one
        command to the next, as in Claude Code, and goes back to that folder when a command leaves the vault
        (Claude Code 2.1.328 prints "Shell cwd was reset" then)."""
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
                    # only inputs that are a path, a command, a search pattern or a skill name: a free-text field
                    # (an agent's `description`, a task's text) can restate the owner's request (22.5, D051)
                    target = data.get("file_path") or data.get("notebook_path") or data.get("command") or data.get("path") or \
                        data.get("pattern") or data.get("skill") or ""
                    call = ToolCall(tool, relative(str(target), root, origin)
                                    if tool in ("Read", "Write", "Edit", "NotebookEdit") else str(target), raw=str(target),
                                    cwd=str(event.get("cwd") or ""))
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
        start = cwd or str(root)
        shell = start
        for call in run.tool_calls:
            if call.tool == "Bash" and call.ran:
                # A transcript records the shell's folder on each event: use it. A stream without it: carry the
                # folder a command ends in to the next one, only when the command succeeded (D051).
                here = call.cwd or shell
                end: dict = {}
                read, written, searches = files_from_shell(call.target, root, origin, here, end)
                if not call.cwd and here != start:  # a carried folder is a guess: read from the start too (D052)
                    more, _, more_searches = files_from_shell(call.target, root, origin, start)
                    read = read + [item for item in more if item not in read]
                    searches = searches + [x for x in more_searches if x not in searches]
                after = end.get("cwd")
                inside = after is not None and os.path.isdir(after) and \
                    any(_inside(after, base) is not None for base in _bases(root, origin))
                if call.result == "ok":
                    shell = after if inside else start
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
                _add(run.files_read, relative(call.raw, root, origin, call.cwd or start) if call.raw else call.target)
            elif call.tool in ("Write", "Edit", "NotebookEdit"):
                _add(run.files_written, call.target)
                if call.raw and escapes(call.raw, root, origin, str(root)):
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
                tool = str(part.get("tool"))
                is_shell = tool in ("bash", "shell")
                call = ToolCall(tool, str(target) if is_shell else relative(str(target), root, origin),
                                "error" if status == "error" else "ok", raw=str(target))
                run.tool_calls.append(call)
                run.parsed_tool_calls = True
                if is_shell:
                    read, written, searches = files_from_shell(call.raw, root, origin)
                    for item in read:
                        _add(run.files_read, item)
                    for item in written:
                        _add(run.files_written, item)
                    for search in searches:
                        _add(run.files_searched, search.folder)
                        if search not in run.searches:
                            run.searches.append(search)
                    for item in shell_escapes(call.raw, root, origin):
                        _add(run.escaped_writes, item)
                elif call.result == "ok" and tool == "read":
                    _add(run.files_read, call.target)
                elif call.result == "ok" and tool in ("write", "edit", "patch"):
                    _add(run.files_written, call.target)
                    if escapes(call.raw, root, origin, str(root)):
                        _add(run.escaped_writes, call.raw)
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
    return v.is_page_path(rel) and os.path.isfile(os.path.join(root, rel)) and v.load_page(root, root / rel).local_only


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
        for result in data.get("superseded") or []:  # replaced by a rerun, but what they did still counts
            if isinstance(result.get("trace"), dict):
                records.append({**result["trace"], "superseded": True})
    return sorted(records, key=lambda r: str(r.get("started") or ""))


def format_record(record: dict) -> list[str]:
    tokens = record.get("tokens") or {}
    cost = record.get("cost_usd")
    label = record.get("case_id") or record.get("skill") or "session"
    if record.get("superseded"):
        label += " (replaced)"
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


# ---------------------------------------------------------------------------- session import (22.6)
#
# `anybrain trace import` reads the transcripts Claude Code already keeps, once a session has ended, and stores
# one record per question asked in it. Claude Code documents where the transcripts live and that their format is
# internal and may change with any release (docs/decisions.md D049): a transcript this reader does not recognise
# is reported, never recorded as an empty run.

SESSION_RETENTION_DAYS = 30  # Q21: raw session traces are kept 30 days, then only weekly aggregates
WEEKLY_FILE = ".harness/traces/weekly.json"
# Relative weights of token kinds, used only to share a cost the harness measured between the questions of one
# session (base input = 1). Totals are never computed from them (D049).
TOKEN_SHARES = {"input_tokens": 1.0, "cache_read_input_tokens": 0.1, "output_tokens": 5.0}
CACHE_WRITE_SHARES = {"ephemeral_5m_input_tokens": 1.25, "ephemeral_1h_input_tokens": 2.0}
MODEL_USAGE_KEYS = {"inputTokens": "input_tokens", "cacheReadInputTokens": "cache_read_input_tokens",
                    "outputTokens": "output_tokens", "cacheCreationInputTokens": "cache_creation_input_tokens"}


def claude_projects() -> Path:
    """Where Claude Code keeps its transcripts: `~/.claude/projects`, or under `CLAUDE_CONFIG_DIR` when set."""
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser() / "projects"


def project_dir_name(path: str) -> str:
    """Claude Code's folder name for a working directory: every character that is not an ASCII letter or digit
    becomes `-`, counted in UTF-16 units as JavaScript does, so an emoji gives two; a name longer than 200
    characters is cut and gets a hash of the full path."""
    return "".join(c if c.isascii() and c.isalnum() else "-" * (2 if ord(c) > 0xFFFF else 1) for c in path)


def _vault_forms(vault: Path) -> list[str]:
    forms = []
    for form in (os.path.normpath(os.path.abspath(vault)), os.path.realpath(vault)):
        if form not in forms:
            forms.append(form)
    return forms


def session_files(vault: Path) -> list[Path]:
    """Transcripts that may belong to the vault: in a folder named after it or after a folder inside it, or in
    any folder whose sessions were opened inside it (a name Claude Code shortened with a hash, a path written in
    another Unicode form). Each session's first `cwd` then decides (`Session.origin`)."""
    projects = claude_projects()
    if not projects.is_dir():
        return []
    names = [project_dir_name(form) for form in _vault_forms(vault)]
    found = []
    for folder in sorted(projects.iterdir()):
        if not folder.is_dir():
            continue
        files = sorted(folder.glob("*.jsonl"))
        named = any(folder.name == n or folder.name.startswith(n + "-")
                    or (len(n) > 200 and folder.name.startswith(n[:200])) for n in names)
        if named or any(_in_vault(_first_cwd(path), vault) for path in files[-1:]):
            found.extend(files)
    return found


def _first_cwd(path: Path, limit: int = 65536) -> str:
    """The folder a session was opened in: the first `cwd` of its transcript, read from its beginning only."""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            head = handle.read(limit)
    except OSError:
        return ""
    for line in head.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("cwd"):
            return str(event["cwd"])
    return ""


def _in_vault(cwd: str, vault: Path) -> bool:
    import unicodedata

    if not cwd:
        return False
    forms = [os.path.normpath(cwd)]
    if os.path.exists(cwd):
        forms.append(os.path.realpath(cwd))
    bases = _vault_forms(vault)
    forms = [unicodedata.normalize("NFC", f) for f in forms]
    bases = [unicodedata.normalize("NFC", b) for b in bases]
    return any(_inside(form, base) is not None for form in forms for base in bases)


def _events(path: Path) -> list[dict]:
    events = []
    try:
        text = v.read_text(path)
    except (OSError, UnicodeDecodeError):
        return events
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _is_prompt(event: dict) -> bool:
    """A message from the owner (or the harness on their behalf) that starts a question, not a tool result."""
    if event.get("type") != "user" or event.get("isSidechain") or event.get("isMeta") or event.get("isCompactSummary"):
        return False
    content = (event.get("message") or {}).get("content")
    if isinstance(content, str):
        return bool(content.strip())
    kinds = {item.get("type") for item in content if isinstance(item, dict)} if isinstance(content, list) else set()
    return bool(kinds) and "tool_result" not in kinds


def _stamp(value) -> _dt.datetime | None:
    try:
        stamp = _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=_dt.timezone.utc)


def _weight(usage: dict) -> float:
    weight = sum(float(usage.get(key) or 0) * share for key, share in TOKEN_SHARES.items())
    split = usage.get("cache_creation") or {}
    written = 0.0
    for key, share in CACHE_WRITE_SHARES.items():
        weight += float(split.get(key) or 0) * share
        written += float(split.get(key) or 0)
    return weight + max(0.0, float(usage.get("cache_creation_input_tokens") or 0) - written) * 1.25


@dataclass
class Turn:
    """One question in a session: the owner's prompt and everything the agent did until the next one."""

    key: str
    first: int
    last: int = 0
    events: list[dict] = field(default_factory=list)
    cwd: str = ""
    version: str = ""
    started: _dt.datetime | None = None
    ended: _dt.datetime | None = None
    messages: dict = field(default_factory=dict)  # message id -> (event index, model, usage)
    subagents: list[list[dict]] = field(default_factory=list)
    cost: float = 0.0
    cost_shared: bool = False  # the session's measured cost was shared with other questions by tokens

    def note(self, index: int, event: dict) -> None:
        self.events.append(event)
        self.last = index
        stamp = _stamp(event.get("timestamp"))
        if stamp:
            self.started = self.started or stamp
            self.ended = stamp
        self.cwd = self.cwd or str(event.get("cwd") or "")
        self.version = self.version or str(event.get("version") or "")
        if event.get("type") == "assistant":
            message = event.get("message") or {}
            key = message.get("id") or event.get("requestId") or event.get("uuid") or f"{index}"
            if key not in self.messages and isinstance(message.get("usage"), dict):
                self.messages[key] = (index, str(message.get("model") or ""), message["usage"])


@dataclass
class Session:
    path: Path
    session_id: str
    turns: list[Turn]
    checkpoints: list[tuple[int, float, dict]]  # (event index, cumulative cost, model usage)
    recognised: bool  # the reader found the events it knows; False means the format changed
    origin: str = ""  # the folder the session was opened in: it decides whether the session is the vault's

    @property
    def closed_at(self) -> int:
        """Index of the last cost checkpoint: the questions before it are finished and their cost is written."""
        return self.checkpoints[-1][0] if self.checkpoints else -1


def read_session(path: Path) -> Session:
    events = _events(path)
    turns: list[Turn] = []
    checkpoints: list[tuple[int, float, dict]] = []
    current: Turn | None = None
    tool_turn: dict[str, tuple[int, int]] = {}  # Agent tool_use id -> (turn index, event index)
    session_id = path.stem
    known = 0
    origin = ""
    for index, event in enumerate(events):
        kind = event.get("type")
        # the folder the session was opened in: a branched session's copies hold the original's (D052)
        origin = origin or (str(event.get("cwd") or "") if kind in ("user", "assistant", "attachment") else "")
        if event.get("forkedFrom"):
            known += kind in ("user", "assistant")  # a known format, though only copies so far
            continue  # copied from the session it was branched from, which holds the original (D051)
        session_id = str(event.get("sessionId") or session_id)
        if kind == "cost-state":
            cost = event.get("totalCostUSD")
            if isinstance(cost, (int, float)):
                checkpoints.append((index, float(cost), event.get("modelUsage") or {}))
                known += 1
            continue
        if kind not in ("user", "assistant"):
            continue
        known += 1
        prompt_id = event.get("promptId")
        if _is_prompt(event) and (current is None or not prompt_id or prompt_id != current.key):
            current = Turn(str(prompt_id or f"turn-{len(turns) + 1}"), index)
            turns.append(current)
        if current is None:
            continue
        current.note(index, event)
        if kind == "assistant":
            for item in (event.get("message") or {}).get("content") or []:
                if isinstance(item, dict) and item.get("type") == "tool_use" and item.get("name") in ("Agent", "Task"):
                    tool_turn[str(item.get("id"))] = (len(turns) - 1, index)
    folder = path.with_suffix("") / "subagents"
    by_prompt = {turn.key: i for i, turn in enumerate(turns)}
    # agents started by a tool call, and agents of a workflow (`subagents/workflows/<run>/`), at any depth (D052)
    for sub in sorted(folder.rglob("agent-*.jsonl")) if folder.is_dir() else []:
        sub_events = [e for e in _events(sub) if e.get("type") in ("user", "assistant") and not e.get("forkedFrom")]
        if not sub_events or not turns:
            continue
        meta = {}
        meta_path = sub.with_name(sub.stem + ".meta.json")
        if meta_path.is_file():
            try:
                meta = json.loads(v.read_text(meta_path))
            except ValueError:
                meta = {}
        owner = tool_turn.get(str(meta.get("toolUseId") or ""))
        prompt_id = next((str(e.get("promptId")) for e in sub_events if e.get("promptId")), "")
        if owner is None and prompt_id in by_prompt:  # a workflow agent carries the prompt that launched it
            owner = (by_prompt[prompt_id], turns[by_prompt[prompt_id]].first)
        if owner is None:  # no link to the call that started it: the question that was running when it began
            begun = _stamp(sub_events[0].get("timestamp"))
            candidates = [i for i, t in enumerate(turns) if begun and t.started and t.started <= begun]
            owner = (candidates[-1] if candidates else len(turns) - 1, turns[candidates[-1] if candidates else -1].first)
        turn = turns[owner[0]]
        turn.subagents.append(sub_events)
        for event in sub_events:
            if event.get("type") == "assistant":
                message = event.get("message") or {}
                key = message.get("id") or event.get("requestId") or event.get("uuid")
                if key and key not in turn.messages and isinstance(message.get("usage"), dict):
                    turn.messages[key] = (owner[1], str(message.get("model") or ""), message["usage"])
    session = Session(path, session_id, turns, checkpoints, recognised=known > 0, origin=origin)
    _share_costs(session)
    return session


def _unit_prices(model_usage: dict) -> dict[str, float]:
    """Cost per unit of token weight for each model, from the harness's own totals."""
    prices = {}
    for model, totals in (model_usage or {}).items():
        if not isinstance(totals, dict) or not isinstance(totals.get("costUSD"), (int, float)):
            continue
        usage = {ours: totals.get(theirs) or 0 for theirs, ours in MODEL_USAGE_KEYS.items()}
        usage["cache_creation"] = {"ephemeral_5m_input_tokens": totals.get("cacheCreationEphemeral5mInputTokens") or 0,
                                   "ephemeral_1h_input_tokens": totals.get("cacheCreationEphemeral1hInputTokens") or 0}
        weight = _weight(usage)
        if weight > 0:
            prices[model] = float(totals["costUSD"]) / weight
    return prices


def _share_costs(session: Session) -> None:
    """Give each finished question its part of the cost Claude Code measured.

    Claude Code writes the session's running total at checkpoints (when a session ends, among others). The cost
    between two checkpoints is exact; when several questions fall between them it is shared by their token
    usage, priced per model from the harness's own totals (D049).
    """
    if not session.checkpoints:
        return
    positions = [index for index, _, _ in session.checkpoints]
    prices = _unit_prices(session.checkpoints[-1][2])
    fallback = sum(prices.values()) / len(prices) if prices else 1.0
    by_interval: dict[int, list[tuple[Turn, float]]] = {}
    for turn in session.turns:
        for index, model, usage in turn.messages.values():
            interval = sum(1 for p in positions if p < index)
            if interval < len(positions):
                by_interval.setdefault(interval, []).append((turn, prices.get(model, fallback) * _weight(usage)))
    previous = 0.0
    for interval, (_, total, _) in enumerate(session.checkpoints):
        spent = total - previous if total >= previous else total  # a lower total: the harness started a new count
        previous = total
        shares = by_interval.get(interval) or []
        weight = sum(share for _, share in shares)
        owners = {id(turn) for turn, _ in shares}
        for turn, share in shares:
            turn.cost += spent * (share / weight if weight > 0 else 1 / len(shares))
            if len(owners) > 1:
                turn.cost_shared = True


def _local_model(vault: Path) -> str:
    import tomllib

    path = vault / v.MODELS_FILE
    try:
        data = tomllib.loads(v.read_text(path)) if path.is_file() else {}
    except tomllib.TOMLDecodeError:
        return ""
    model = str(((data.get("profiles") or {}).get("local") or {}).get("model") or "")
    return "" if model.startswith("<") else model


def _turn_skill(turn: Turn) -> str | None:
    for event in turn.events:
        for item in (event.get("message") or {}).get("content") or [] if event.get("type") == "assistant" else []:
            if isinstance(item, dict) and item.get("type") == "tool_use" and item.get("name") == "Skill":
                skill = (item.get("input") or {}).get("skill")
                if skill:
                    return str(skill)
    counts: dict[str, int] = {}
    for event in turn.events:
        if event.get("type") == "assistant" and event.get("attributionSkill"):
            counts[str(event["attributionSkill"])] = counts.get(str(event["attributionSkill"]), 0) + 1
    return max(sorted(counts), key=counts.get) if counts else None


def _answer(turn: Turn) -> str:
    text = ""
    for event in turn.events:
        if event.get("type") == "assistant" and not event.get("isSidechain"):
            for item in (event.get("message") or {}).get("content") or []:
                if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
                    text = item["text"]
    return text


def _merge_run(run: Run, other: Run) -> None:
    run.tool_calls.extend(other.tool_calls)
    for key in ("files_read", "files_written", "files_searched", "escaped_writes"):
        for item in getattr(other, key):
            _add(getattr(run, key), item)
    for search in other.searches:
        if search not in run.searches:
            run.searches.append(search)


def turn_record(vault: Path, session: Session, turn: Turn, local_model: str) -> dict:
    """The trace record of one finished question (22.5): no prompt, no answer, no file content."""
    adapter = ClaudeAdapter()

    def parse(events: list[dict]) -> Run:
        return adapter.parse("\n".join(json.dumps(e) for e in events), vault, cwd=session.origin or turn.cwd or None)

    run = parse(turn.events)
    for sub_events in turn.subagents:
        _merge_run(run, parse(sub_events))
    models = [model for _, model, _ in turn.messages.values() if model and not model.startswith("<")]
    main = [str((e.get("message") or {}).get("model") or "") for e in turn.events if e.get("type") == "assistant"]
    main = [m for m in main if m and not m.startswith("<")] or models
    run.model = max(sorted(set(main)), key=main.count) if main else ""
    run.harness_version = turn.version
    run.answer = _answer(turn)
    cached = sum(int(u.get("cache_read_input_tokens") or 0) for _, _, u in turn.messages.values())
    run.tokens = {
        "input": sum(int(u.get("input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0)
                     for _, _, u in turn.messages.values()) + cached,
        "output": sum(int(u.get("output_tokens") or 0) for _, _, u in turn.messages.values()),
        "cached": cached,
    }
    pending = turn.last > session.closed_at
    run.cost_usd = None if pending else turn.cost
    if turn.started and turn.ended:
        run.duration_ms = int((turn.ended - turn.started).total_seconds() * 1000)
    profile = "local" if local_model and models and all(m == local_model for m in models) else "frontier"
    started = (turn.started or _dt.datetime.now(_dt.timezone.utc)).astimezone(_dt.timezone.utc)
    record = build_record(run, vault, profile, "session", started.strftime("%Y-%m-%dT%H:%M:%SZ"),
                          skill=_turn_skill(turn))
    record["run_id"] = f"claude:{session.session_id}:{turn.key}"
    record["cost_shared"] = turn.cost_shared and not pending
    record["cost_pending"] = pending  # Claude Code has not written this question's cost yet: a later import does
    del record["escaped_writes"]  # an eval measure: a session works in the real vault
    return record


def _trace_files(vault: Path) -> list[Path]:
    return sorted((vault / TRACES_DIR).glob("*.jsonl"))


def _read_jsonl(path: Path) -> list[dict]:
    records = []
    for line in v.read_text(path).splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _write_jsonl(path: Path, records: list[dict]) -> None:
    if records:
        v.write_text(path, "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records))
    elif path.exists():
        path.unlink()


def iso_week(day: _dt.date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def load_weekly(vault: Path) -> dict:
    path = vault / WEEKLY_FILE
    try:
        data = json.loads(v.read_text(path)) if path.is_file() else {}
    except ValueError as exc:
        raise v.VaultError(f"{WEEKLY_FILE} is not valid JSON: {exc}") from exc
    return data if isinstance(data, dict) and isinstance(data.get("weeks"), dict) else {"weeks": {}}


def _median(values: list[int | float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    return float(ordered[middle]) if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def aggregate(records: list[dict]) -> list[dict]:
    """Weekly figures per skill, with no path in them (Q21). The number of files each run read is kept, so that
    medians can be recomputed when rows are merged."""
    rows: dict[str, list[dict]] = {}
    for record in records:
        rows.setdefault(str(record.get("skill") or "none"), []).append(record)
    result = []
    for skill in sorted(rows):
        group = rows[skill]
        costs = [r["cost_usd"] for r in group if isinstance(r.get("cost_usd"), (int, float))]
        counts = sorted(len(r.get("files_read") or []) for r in group)
        result.append({
            "skill": skill,
            "runs": len(group),
            "cost_usd": round(sum(costs), 4) if costs else None,
            "runs_with_cost": len(costs),
            "tokens": {k: sum(int((r.get("tokens") or {}).get(k) or 0) for r in group) for k in ("input", "output", "cached")},
            "files_read": counts,
            "files_read_median": _median(counts),
            "local_only_reads": sum(len(r.get("local_only_read_on_frontier") or []) for r in group),
            "runs_with_local_only_reads": sum(1 for r in group if r.get("local_only_read_on_frontier")),
            "denied": sum(len(r.get("denied") or []) for r in group),
        })
    return result


def merge_rows(*groups: list[dict]) -> list[dict]:
    """Rows of the same week per skill, summed. A row without its counts keeps its median only when alone."""
    merged: dict[str, dict] = {}
    for rows in groups:
        for row in rows or []:
            skill = str(row.get("skill") or "none")
            if skill not in merged:
                merged[skill] = {**row, "skill": skill, "tokens": dict(row.get("tokens") or {}),
                                 "files_read": list(row.get("files_read") or [])}
                continue
            into = merged[skill]
            for key in ("runs", "runs_with_cost", "local_only_reads", "runs_with_local_only_reads", "denied"):
                into[key] = int(into.get(key) or 0) + int(row.get(key) or 0)
            costs = [c for c in (into.get("cost_usd"), row.get("cost_usd")) if isinstance(c, (int, float))]
            into["cost_usd"] = round(sum(costs), 4) if costs else None
            for key in ("input", "output", "cached"):
                into["tokens"][key] = int(into["tokens"].get(key) or 0) + int((row.get("tokens") or {}).get(key) or 0)
            into["files_read"] = sorted(into["files_read"] + list(row.get("files_read") or []))
            into["files_read_median"] = _median(into["files_read"])
    return [merged[k] for k in sorted(merged)]


def prune(vault: Path, today: _dt.date) -> int:
    """Drop session records older than the retention, adding each one to its week's totals first (Q21)."""
    cutoff = today - _dt.timedelta(days=SESSION_RETENTION_DAYS)
    files = {path: _read_jsonl(path) for path in _trace_files(vault)}

    def day(record: dict) -> _dt.date | None:
        return v.as_date(str(record.get("started") or "")[:10])

    expired = [r for records in files.values() for r in records
               if r.get("trigger") == "session" and (day(r) or today) < cutoff]
    if not expired:
        return 0
    weekly = load_weekly(vault)
    by_week: dict[str, list[dict]] = {}
    for record in expired:
        by_week.setdefault(iso_week(day(record) or today), []).append(record)
    for week, records in by_week.items():
        weekly["weeks"][week] = merge_rows(weekly["weeks"].get(week) or [], aggregate(records))
    weekly["weeks"] = dict(sorted(weekly["weeks"].items()))
    v.write_text(vault / WEEKLY_FILE, json.dumps(weekly, indent=1, sort_keys=True) + "\n")
    gone = {id(r) for r in expired}
    for path, records in files.items():
        kept = [r for r in records if id(r) not in gone]
        if len(kept) != len(records):
            _write_jsonl(path, kept)
    return len(expired)


STALE_HOURS = 24  # a last question with no cost after this long: a session killed, or left open


def import_sessions(vault: Path, since: _dt.date | None = None) -> int:
    """`anybrain trace import`: store the questions of Claude Code sessions opened in the vault.

    A question is stored once the session has moved past it, once its transcript has been still for a day, or
    at once when it read a local-only file on the frontier profile. Until Claude Code writes the session's cost,
    the record says `cost_pending` and a later import rebuilds it (D051).
    """
    from . import install

    harnesses = list((install.load_models(vault).get("harnesses") or {"claude": {}}).keys())
    for name in harnesses:
        if name != "claude":
            print(f"{name}: no transcript reader yet; only Claude Code sessions are imported (D041)")
    if "claude" not in harnesses:
        return 0
    today = v.today()
    cutoff = today - _dt.timedelta(days=SESSION_RETENTION_DAYS)
    files = {path: _read_jsonl(path) for path in _trace_files(vault)}
    where = {str(r.get("run_id")): (path, i) for path, records in files.items() for i, r in enumerate(records)}
    local_model = _local_model(vault)
    now = _dt.datetime.now(_dt.timezone.utc).timestamp()
    imported = updated = already = old = pending = running = 0
    sessions_seen: set[str] = set()
    stored: list[dict] = []
    unknown: list[str] = []
    exact = {project_dir_name(form) for form in _vault_forms(vault)}
    elsewhere = 0
    for path in session_files(vault):
        session = read_session(path)
        if not session.recognised:
            unknown.append(path.name)
            continue
        if not _in_vault(session.origin, vault):
            elsewhere += path.parent.name in exact  # in the vault's own folder, yet opened outside it
            continue
        try:
            still = (now - path.stat().st_mtime) / 3600 >= STALE_HOURS
        except OSError:
            still = False
        for number, turn in enumerate(session.turns):
            if not turn.messages:
                continue
            day = (turn.started or _dt.datetime.now(_dt.timezone.utc)).astimezone(_dt.timezone.utc).date()
            if since and day < since:
                continue
            if day < cutoff:
                old += 1
                continue
            run_id = f"claude:{session.session_id}:{turn.key}"
            found = where.get(run_id)
            if found and not files[found[0]][found[1]].get("cost_pending"):
                already += 1
                continue
            record = turn_record(vault, session, turn, local_model)
            moved_on = number + 1 < len(session.turns)
            if record["cost_pending"] and not (moved_on or still or record["local_only_read_on_frontier"]):
                running += 1
                continue
            pending += bool(record["cost_pending"])
            sessions_seen.add(session.session_id)
            stored.append(record)
            if found:
                files[found[0]][found[1]] = None  # replaced below, in the month it belongs to
                updated += 1
            else:
                imported += 1
            where[run_id] = (None, None)
    changed = {p for p, records in files.items() if any(r is None for r in records)}
    for record in stored:
        target = vault / TRACES_DIR / f"{record['started'][:7]}.jsonl"
        files.setdefault(target, _read_jsonl(target) if target.is_file() else []).append(record)
        changed.add(target)
    for target in sorted(changed):
        records = [r for r in files[target] if r is not None]
        _write_jsonl(target, sorted(records, key=lambda r: (str(r.get("started")), str(r.get("run_id")))))
    flagged = [r for r in stored if r.get("local_only_read_on_frontier")]
    if flagged:
        print(f"{len(flagged)} question(s) read a local-only file on the frontier profile:")
        for record in flagged:
            for line in format_record(record):
                print(line)
    print(f"imported {imported} question(s) from {len(sessions_seen)} Claude Code session(s)"
          + (f"; {updated} updated" if updated else "") + (f"; {already} already imported" if already else ""))
    if pending:
        print(f"{pending} question(s) stored without their cost: Claude Code writes it when the session ends, and "
              "the next import adds it")
    if running:
        print(f"{running} question(s) still running in an open session: stored once the session moves on")
    if old:
        print(f"{old} question(s) older than {SESSION_RETENTION_DAYS} days skipped (retention, section 22.5)")
    if elsewhere:
        print(f"{elsewhere} transcript(s) in the vault's own transcript folder record a session opened outside the "
              "vault: skipped")
    if unknown:
        print(f"{len(unknown)} transcript(s) in a format this reader does not know: {', '.join(unknown[:5])}; "
              "Claude Code may have changed it, check for an anybrain update")
    pruned = prune(vault, today)
    if pruned:
        print(f"removed {pruned} question(s) older than {SESSION_RETENTION_DAYS} days; their weekly figures stay in "
              f"{WEEKLY_FILE}")
    return 1 if unknown else 0
