"""Run traces: what the agent did, from the harness's own machine output (PRD section 22.5).

One adapter per harness turns its machine-readable output into the same record: files read and written,
tool calls, denied calls, tokens, cost, and the local-only files read in a frontier run. A record never
holds file content, the answer text or the prompt. `anybrain trace show` prints recent records.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from . import vault as v

TRACES_DIR = ".harness/traces"
BASELINE_DIR = ".harness/baseline"
FILES_OPENED_RE = re.compile(r"files opened:\s*(\d+)", re.I)
READ_COMMANDS = {"cat", "head", "tail", "less", "more", "sed", "awk", "grep", "rg", "wc", "nl", "bat", "cut", "sort"}
REDIRECT_RE = re.compile(r"(?<![<>&\d])>{1,2}\s*([^\s;&|<>]+)")
DENIED_HINTS = ("denied", "not allowed", "permission")


@dataclass
class ToolCall:
    tool: str
    target: str
    result: str = "ok"  # "ok", "denied" or "error"

    def as_json(self) -> dict:
        return {"tool": self.tool, "target": self.target, "result": self.result}


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
    tokens: dict = field(default_factory=lambda: {"input": 0, "output": 0, "cached": 0})
    cost_usd: float | None = None
    duration_ms: int | None = None
    ok: bool = False  # the harness produced a final answer
    error: str = ""
    parsed_tool_calls: bool = True  # False when the adapter cannot see tool calls


# ---------------------------------------------------------------------------- paths

def relative(target: str, root: Path) -> str:
    """A path from a tool call, relative to the run's vault when it is inside it."""
    if not target:
        return ""
    path = Path(target)
    if path.is_absolute():
        try:
            return v.rel_path(root.resolve(), Path(path).resolve() if path.exists() else path)
        except ValueError:
            try:
                return v.rel_path(root, path)
            except ValueError:
                return target
    return target.strip().removeprefix("./")


def _add(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def files_from_shell(command: str, root: Path) -> tuple[list[str], list[str]]:
    """Files a shell command reads (with a common reading command) and writes (with a redirection).

    Best effort: commands that read files without naming them (`grep -r`, a script) are not seen here.
    """
    read, written = [], []
    for target in REDIRECT_RE.findall(command):
        _add(written, relative(target, root))
    for segment in re.split(r"&&|\|\||;|\||\n", command):
        segment = REDIRECT_RE.sub(" ", segment).strip()
        if not segment:
            continue
        try:
            words = shlex.split(segment)
        except ValueError:
            words = segment.split()
        if not words or Path(words[0]).name not in READ_COMMANDS:
            continue
        for word in words[1:]:
            if word.startswith("-"):
                continue
            candidate = relative(word, root)
            if candidate and (root / candidate).is_file():
                _add(read, candidate)
    return read, written


# ---------------------------------------------------------------------------- adapters

class Adapter:
    name = ""

    def output_args(self, profile: str, budget_usd: float | None) -> list[str]:
        return []

    def parse(self, output: str, root: Path) -> Run:  # pragma: no cover - interface
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

    def parse(self, output: str, root: Path) -> Run:
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
                    call = ToolCall(tool, relative(str(target), root) if tool in ("Read", "Write", "Edit") else str(target))
                    calls[item.get("id", str(len(order)))] = call
                    order.append(item.get("id", str(len(order) - 1)))
            elif kind == "user":
                content = (event.get("message") or {}).get("content")
                for item in content if isinstance(content, list) else []:
                    if item.get("type") != "tool_result" or item.get("tool_use_id") not in calls:
                        continue
                    if item.get("is_error"):
                        text = _result_text(item.get("content")).lower()
                        denied = any(hint in text for hint in DENIED_HINTS)
                        calls[item["tool_use_id"]].result = "denied" if denied else "error"
            elif kind == "system" and event.get("subtype") == "permission_denied":
                call = calls.get(event.get("tool_use_id", ""))
                if call:
                    call.result = "denied"
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
            if call.result != "ok":
                continue
            if call.tool == "Read":
                _add(run.files_read, call.target)
            elif call.tool in ("Write", "Edit", "NotebookEdit"):
                _add(run.files_written, call.target)
            elif call.tool == "Bash":
                read, written = files_from_shell(call.target, root)
                for item in read:
                    _add(run.files_read, item)
                for item in written:
                    _add(run.files_written, item)
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

    def parse(self, output: str, root: Path) -> Run:
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
                call = ToolCall(str(part.get("tool")), relative(str(target), root),
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

def local_only_reads(root: Path, files: list[str]) -> list[str]:
    """Files read that the routing of the vault keeps local, or that are confidential pages."""
    found = []
    for rel in files:
        if v.is_local_only_path(root, rel):
            found.append(rel)
        elif v.is_page_path(rel) and (root / rel).is_file() and v.load_page(root, root / rel).local_only:
            found.append(rel)
    return found


def self_reported_files(answer: str) -> int | None:
    matches = FILES_OPENED_RE.findall(answer or "")
    return int(matches[-1]) if matches else None


def build_record(run: Run, root: Path, profile: str, trigger: str, started: str,
                 case_id: str | None = None, skill: str | None = None) -> dict:
    denied = [c.as_json() for c in run.tool_calls if c.result == "denied"]
    local_reads = local_only_reads(root, run.files_read) if profile != "local" else []
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
        "denied": denied,
        "local_only_read_on_frontier": local_reads,
        "self_reported_files_opened": self_reported_files(run.answer),
    }


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
