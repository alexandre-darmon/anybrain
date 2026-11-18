"""Golden-set runner: graders, judge, cost cap, gates, baseline (PRD sections 17.3 and 22).

`anybrain eval <vault> [--harness claude|opencode] [--profile frontier|local] [--tags T,...] [--max-cost N]
[--freeze] [--golden FILE]` drives the harness headless, one question per invocation, each in a throwaway copy
of the vault so an eval never changes it. Every case gets a run trace (22.5). The run file goes to
`.harness/baseline/run-<harness>-<profile>-<date>.json`, which only the local profile may read.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import shlex
import shutil
import statistics
import subprocess
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import trace as tr
from . import vault as v

GOLDEN = ".harness/golden.yaml"
BASELINE_DIR = tr.BASELINE_DIR
TAGS = ("navigation", "concept", "multi-hop", "needle", "project", "recommend", "safety", "injection", "content")
HARD_TAGS = ("safety", "injection")  # a failure here makes the run fail (exit code 1)
COPY_IGNORE = (".git", "baseline", "traces")
EVAL_DEFAULTS = {"judge": "endpoint:frontier", "jobs": 2, "timeout_s": 600, "max_tokens": 1024, "retries": 2,
                 "quiet_minutes": 15, "relaunches": 2}
QUIET_POLL_S = 30
MAX_WAIT_FACTOR = 8  # give up waiting for a quiet vault after 8 quiet periods (2 hours at 15 minutes)
# What no case grades on: Obsidian's window state, its trash and system files change when the owner only browses.
FINGERPRINT_SKIP_DIRS = {".git", ".obsidian", ".trash"}
FINGERPRINT_SKIP_FILES = {".DS_Store"}
RETRY_DELAYS_S = (5, 15)
JUDGE_BUDGET_USD = 0.5  # per judge run when no --max-cost is given
NOT_RETRIED = ("error_max_budget_usd", "error_max_turns")  # limits: a retry would hit them again
PATH_RE = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.(?:md|csv|txt))\b")
SKIPPED_PHASE = {"content": "P1b: the draft skill and `anybrain content check` arrive in step 16"}


# ---------------------------------------------------------------------------- golden set (22.1)

@dataclass
class Case:
    id: str
    question: str
    tags: list[str]
    expected: dict
    profile: str | None = None


def load_golden(path: Path) -> list[Case]:
    if not path.is_file():
        raise v.VaultError(f"no golden set at {path}; copy evals/golden.example.yaml there and write your questions")
    try:
        data = yaml.safe_load(v.read_text(path)) or []
    except yaml.YAMLError as exc:
        raise v.VaultError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(data, list):
        raise v.VaultError(f"{path} must be a list of cases")
    cases, seen = [], set()
    for i, item in enumerate(data, start=1):
        if not isinstance(item, dict) or not item.get("id") or not item.get("question"):
            raise v.VaultError(f"{path}: case {i} needs an `id` and a `question`")
        if item["id"] in seen:
            raise v.VaultError(f"{path}: duplicate case id {item['id']}")
        seen.add(item["id"])
        tags = [str(t) for t in v.as_list(item.get("tags"))]
        unknown = [t for t in tags if t not in TAGS]
        if unknown:
            raise v.VaultError(f"{path}: case {item['id']} has unknown tags {unknown}; known: {', '.join(TAGS)}")
        expected = item.get("expected") or {}
        if not isinstance(expected, dict):
            raise v.VaultError(f"{path}: case {item['id']}: `expected` must be a mapping")
        profile = item.get("profile")
        if profile is not None and profile not in v.PROFILES:
            raise v.VaultError(f"{path}: case {item['id']}: profile must be frontier or local")
        cases.append(Case(str(item["id"]), str(item["question"]), tags, expected, profile))
    return cases


# ---------------------------------------------------------------------------- configuration

def load_models(vault: Path) -> dict:
    path = vault / v.MODELS_FILE
    if path.is_file():
        try:
            return tomllib.loads(v.read_text(path))
        except tomllib.TOMLDecodeError as exc:
            raise v.VaultError(f"{v.MODELS_FILE} is not valid TOML: {exc}") from exc
    return tomllib.loads(v.read_text(v.framework_root() / "templates" / "models.example.toml"))


def eval_settings(models: dict) -> dict:
    settings = dict(EVAL_DEFAULTS)
    settings.update(models.get("eval") or {})
    return settings


def harness_command(models: dict, harness: str, prompt: str, profile: str, budget: float | None) -> list[str]:
    """The `[harnesses.<name>] headless` template with the prompt, plus the adapter's machine-output flags."""
    spec = (models.get("harnesses") or {}).get(harness) or {}
    template = spec.get("headless")
    if not template:
        raise v.VaultError(f"no [harnesses.{harness}] headless command in {v.MODELS_FILE}")
    words = shlex.split(template)
    if not any("{prompt}" in w for w in words):
        raise v.VaultError(f"[harnesses.{harness}] headless must contain {{prompt}}")
    command = [prompt if w == "{prompt}" else w.replace("{prompt}", prompt) for w in words]
    return command + tr.adapter_for(harness).output_args(profile, budget)


def copy_vault(vault: Path, parent: Path) -> Path:
    """A throwaway copy of the vault, without eval runs, traces and git data."""
    target = parent / "vault"

    def ignore(directory: str, names: list[str]) -> set[str]:
        rel = Path(directory).resolve().relative_to(vault.resolve()).as_posix()
        skipped = {".git"} & set(names)
        if rel == ".harness":
            skipped |= {"baseline", "traces"} & set(names)
        return skipped

    shutil.copytree(vault, target, symlinks=True, ignore=ignore)
    return target


def run_command(command: list[str], cwd: Path, profile: str, timeout: float) -> tuple[str, str, int]:
    """Run a harness in `cwd`. `anybrain` commands it runs see `cwd` as the only vault they may use."""
    env = dict(os.environ, ANYBRAIN_PROFILE=profile, ANYBRAIN_VAULT=str(cwd))
    env[v.EVAL_COPY_ENV] = str(cwd)
    try:
        done = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise v.VaultError(f"harness command not found: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        return (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""), "timeout", -1
    return done.stdout, done.stderr, done.returncode


# ---------------------------------------------------------------------------- graders (22.2)

def cited_paths(answer: str) -> list[str]:
    found = []
    for match in PATH_RE.findall(answer or ""):
        path = match.removeprefix("./")
        if path not in found:
            found.append(path)
    return found


def grade_cited(answer: str, expected: list[str]) -> dict:
    cited = cited_paths(answer)
    missing = [p for p in expected if p not in cited and p not in (answer or "")]
    return {"pass": not missing, "detail": f"missing {', '.join(missing)}" if missing else "all cited"}


def grade_includes(answer: str, must: list[str], must_not: list[str]) -> dict:
    lower = (answer or "").lower()
    absent = [s for s in must if s.lower() not in lower]
    present = [s for s in must_not if s.lower() in lower]
    problems = [f"missing {s!r}" for s in absent] + [f"contains {s!r}" for s in present]
    return {"pass": not problems, "detail": "; ".join(problems) or "ok"}


def rubric() -> str:
    path = v.framework_root() / "evals" / "rubric.md"
    return v.read_text(path) if path.is_file() else "Grade the answer against the expected behaviour only."


def judge_payload(case: Case, answer: str) -> str:
    """What the judge sees: the question, the expected behaviour, the answer and the cited files. Never the
    worker's reasoning or tool calls (22.2: the judge runs in a fresh context)."""
    return json.dumps({
        "question": case.question,
        "expected_behaviour": case.expected.get("behaviour"),
        "answer": answer,
        "cited_files": cited_paths(answer),
    }, ensure_ascii=False, indent=2)


def parse_verdict(text: str) -> dict:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return {"pass": False, "detail": "judge gave no JSON verdict"}
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return {"pass": False, "detail": "judge verdict is not valid JSON"}
    return {"pass": bool(data.get("pass")), "detail": str(data.get("reason", ""))[:300]}


class EndpointJudge:
    """An OpenAI-compatible chat completion at temperature 0 (PRD 18.1 and 22.2)."""

    def __init__(self, models: dict, profile_name: str, max_tokens: int):
        profile = (models.get("profiles") or {}).get(profile_name) or {}
        self.base_url = str(profile.get("base_url") or "").rstrip("/")
        self.model = profile.get("model")
        key_env = profile.get("api_key_env") or ""
        self.api_key = os.environ.get(key_env, "") if key_env else ""
        self.max_tokens = max_tokens
        if not self.base_url or not self.model or "<" in str(self.model):
            raise v.VaultError(f"judge profile {profile_name!r} needs base_url and a real model in {v.MODELS_FILE}")

    def __call__(self, case: Case, answer: str, budget: float | None = None) -> dict:
        body = json.dumps({
            "model": self.model, "temperature": 0, "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": rubric()},
                         {"role": "user", "content": judge_payload(case, answer)}],
        }).encode()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(f"{self.base_url}/chat/completions", body, headers)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                data = json.loads(response.read())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return {"pass": False, "detail": f"judge call failed: {exc}"}
        text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        return parse_verdict(text)


class HarnessJudge:
    """A harness run headless in an empty folder, with no tools: it sees the payload only, never the vault.

    It uses the `[harnesses.<name>] judge` command, never the worker's: the worker's command allows tools, and
    the answer it grades may carry an injected instruction (docs/decisions.md D036).
    """

    def __init__(self, models: dict, harness: str, timeout: float, profile: str):
        spec = (models.get("harnesses") or {}).get(harness) or {}
        if not spec.get("judge"):
            raise v.VaultError(f"[eval] judge = harness:{harness} needs a [harnesses.{harness}] judge command that "
                               f"runs with no tools (see templates/models.example.toml)")
        self.models, self.harness, self.timeout, self.profile = models, harness, timeout, profile

    def command(self, prompt: str, budget: float | None) -> list[str]:
        words = shlex.split(self.models["harnesses"][self.harness]["judge"])
        if not any("{prompt}" in w for w in words):
            raise v.VaultError(f"[harnesses.{self.harness}] judge must contain {{prompt}}")
        command = [prompt if w == "{prompt}" else w.replace("{prompt}", prompt) for w in words]
        return command + tr.adapter_for(self.harness).output_args(self.profile, budget)

    def __call__(self, case: Case, answer: str, budget: float | None = None) -> dict:
        prompt = (rubric() + "\n\nGrade this. Reply with one JSON object only: "
                  '{"pass": true or false, "reason": "one sentence"}.\n\n' + judge_payload(case, answer))
        with tempfile.TemporaryDirectory(prefix="anybrain-judge-") as empty:
            out, err, code = run_command(self.command(prompt, JUDGE_BUDGET_USD if budget is None else budget),
                                         Path(empty), self.profile, self.timeout)
            run = tr.adapter_for(self.harness).parse(out, Path(empty))
        cost = run.cost_usd or 0.0
        if not run.ok:
            return {"pass": False, "detail": f"judge run failed: {run.error or err.strip()[:200]}", "cost_usd": cost}
        return {**parse_verdict(run.answer), "cost_usd": cost}


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def make_judge(models: dict, settings: dict, profile: str = "frontier"):
    """The judge of `[eval] judge`. On the local profile the answers can quote confidential files, so the judge
    must be local too: `endpoint:local` on a localhost URL, or a harness other than Claude Code (18.4 layer 3)."""
    spec = str(settings.get("judge", "endpoint:frontier"))
    kind, _, name = spec.partition(":")
    if kind == "endpoint":
        name = name or "frontier"
        if profile == "local":
            base_url = str(((models.get("profiles") or {}).get(name) or {}).get("base_url") or "")
            host = (urllib.parse.urlsplit(base_url).hostname or "").lower()
            if name != "local" or host not in LOCAL_HOSTS:
                raise v.VaultError(f"[eval] judge = {spec!r} would send local-profile answers off this machine; "
                                   "use endpoint:local with a localhost base_url")
        return EndpointJudge(models, name, int(settings.get("max_tokens", 1024)))
    if kind == "harness":
        name = name or "claude"
        if profile == "local" and name == "claude":
            raise v.VaultError("[eval] judge = harness:claude runs the frontier profile; a local-profile eval needs "
                               "endpoint:local or a harness that runs the local profile")
        return HarnessJudge(models, name, float(settings.get("timeout_s", 600)), profile)
    raise v.VaultError(f"[eval] judge must be endpoint:<profile> or harness:<name>, not {spec!r}")


# ---------------------------------------------------------------------------- one case

@dataclass
class Result:
    case: Case
    status: str  # pass, fail, error, skipped, not-run
    graders: dict = field(default_factory=dict)
    answer: str = ""
    trace: dict | None = None
    note: str = ""
    judge_cost: float = 0.0
    seen: str | None = None  # digest of the vault as this result's attempt copied it

    @property
    def cost(self) -> float:
        return ((self.trace or {}).get("cost_usd") or 0.0) + self.judge_cost

    def as_json(self) -> dict:
        return {"id": self.case.id, "tags": self.case.tags, "profile": self.case.profile, "status": self.status,
                "graders": self.graders, "note": self.note, "answer": self.answer, "trace": self.trace,
                "judge_cost_usd": round(self.judge_cost, 4)}


def run_case(case: Case, vault: Path, models: dict, harness: str, profile: str, settings: dict,
             judge, budget: float | None) -> Result:
    """Run one case. Each attempt gets a fresh copy of the vault and the budget left; the trace keeps every
    attempt, so a local-only read or a cost in a failed attempt is never lost."""
    for tag, reason in SKIPPED_PHASE.items():
        if tag in case.tags:
            return Result(case, "skipped", note=reason)
    if case.profile and case.profile != profile:
        return Result(case, "skipped", note=f"case runs on the {case.profile} profile only")
    attempts = int(settings.get("retries", 2)) + 1
    adapter = tr.adapter_for(harness)
    records, run, spent = [], None, 0.0
    for attempt in range(attempts):
        left = None if budget is None else budget - spent
        if left is not None and left < 0.01:
            break
        started = tr.now_iso()
        seen = digest(fingerprint(vault))
        with tempfile.TemporaryDirectory(prefix="anybrain-eval-") as tmp:
            try:
                root = copy_vault(vault, Path(tmp))
            except (shutil.Error, OSError) as exc:  # a file moved while it was copied: the owner is editing
                if attempt + 1 < attempts:
                    continue
                return Result(case, "error", note=f"the vault changed while it was copied ({str(exc)[:120]})", seen="")
            command = harness_command(models, harness, case.question, profile, left)
            out, err, code = run_command(command, root, profile, float(settings.get("timeout_s", 600)))
            run = adapter.parse(out, root, origin=vault)
            records.append(tr.build_record(run, root, profile, "eval", started, case_id=case.id, origin=vault))
        spent += run.cost_usd or 0.0
        if run.ok:
            break
        run.error = run.error or (err.strip()[:300] or f"exit code {code}")
        if run.error in NOT_RETRIED:
            break
        if attempt + 1 < attempts:
            time.sleep(RETRY_DELAYS_S[min(attempt, len(RETRY_DELAYS_S) - 1)])
    if run is None:
        return Result(case, "not-run", note="cost cap reached")
    record = tr.merge_records(records)
    if not run.ok:
        return Result(case, "error", answer=run.answer, trace=record, note=run.error, seen=seen)
    graders, expected, judge_cost = {}, case.expected, 0.0
    if expected.get("cited_files"):
        graders["cited_files"] = grade_cited(run.answer, [str(p) for p in v.as_list(expected["cited_files"])])
    if expected.get("must_include") or expected.get("must_not_include"):
        graders["includes"] = grade_includes(run.answer, [str(s) for s in v.as_list(expected.get("must_include"))],
                                             [str(s) for s in v.as_list(expected.get("must_not_include"))])
    note = ""
    if expected.get("behaviour"):
        left = None if budget is None else budget - spent
        if judge is None:
            graders["behaviour"] = {"pass": False, "detail": "no judge"}
        elif left is not None and left < 0.01:
            graders["behaviour"] = {"pass": False, "detail": "not judged: cost cap reached"}
            note = "cost cap reached before the judge ran"
        else:
            verdict = judge(case, run.answer, left)
            judge_cost = float(verdict.pop("cost_usd", 0.0) or 0.0)
            graders["behaviour"] = verdict
    measured = len(record["files_read"])
    claimed = record["self_reported_files_opened"]
    graders["files_opened"] = {"pass": True, "detail": f"measured {measured}, said {claimed}", "informational": True}
    if record["local_only_read_on_frontier"]:
        graders["local_only_reads"] = {"pass": False,
                                       "detail": "read on frontier: " + ", ".join(record["local_only_read_on_frontier"])}
    passed = all(g["pass"] for g in graders.values())
    status = "error" if note and not record["local_only_read_on_frontier"] else ("pass" if passed else "fail")
    return Result(case, status, graders, run.answer, record, note=note, judge_cost=judge_cost, seen=seen)


# ---------------------------------------------------------------------------- run, gates, baseline (17.3, 22.3)

def rate(results: list[Result]) -> float | None:
    scored = [r for r in results if r.status in ("pass", "fail", "error")]
    return sum(r.status == "pass" for r in scored) / len(scored) if scored else None


def index_weight(vault: Path) -> int | None:
    from . import index

    try:
        files, _ = index.build(vault)
    except Exception:  # an unbuildable vault has no weight to report; lint says why
        return None
    tokens = {k: len(c.split()) * 4 // 3 for k, c in files.items()}
    domains = [n for k, n in tokens.items() if k != "index.md"]
    return tokens.get("index.md", 0) + (max(domains) if domains else 0)


def gates(results: list[Result], vault: Path) -> list[dict]:
    scored = [r for r in results if r.status in ("pass", "fail", "error")]
    # A navigation case that errored counts as a miss, as it does in every other rate.
    nav = [r for r in scored if "navigation" in r.case.tags and r.case.expected.get("cited_files")]
    recall = (sum(r.status != "error" and r.graders.get("cited_files", {}).get("pass", False) for r in nav) / len(nav)
              if nav else None)
    multi = rate([r for r in results if "multi-hop" in r.case.tags])
    needle = [r for r in scored if "needle" in r.case.tags]
    needle_share = len(needle) / len(scored) if scored else None
    needle_pass = rate(needle)
    reads = [len(r.trace["files_read"]) for r in scored if r.trace and r.trace.get("tool_calls_seen", True)]
    median = statistics.median(reads) if reads else None
    weight = index_weight(vault)

    def gate(name, value, ok, shown):
        return {"name": name, "value": value, "pass": ok, "shown": shown}

    return [
        gate("recall ≥ 0.90", recall, None if recall is None else recall >= 0.90,
             "n/a" if recall is None else f"{recall:.2f}"),
        gate("multi-hop ≥ 0.80", multi, None if multi is None else multi >= 0.80,
             "n/a" if multi is None else f"{multi:.2f}"),
        gate("index weight ≤ 5000 tok", weight, None if weight is None else weight <= 5000,
             "n/a" if weight is None else str(weight)),
        gate("needle share ≤ 20%", needle_share,
             None if needle_share is None else (needle_share <= 0.20 and (needle_pass is None or needle_pass >= 0.70)),
             "n/a" if needle_share is None else f"{needle_share:.0%}"),
        gate("median files read ≤ 8", median, None if median is None else median <= 8,
             "n/a" if median is None else f"{median:g}"),
    ]


def run_path(vault: Path, harness: str, profile: str) -> Path:
    base = vault / BASELINE_DIR
    day = v.today().isoformat()
    path = base / f"run-{harness}-{profile}-{day}.json"
    n = 2
    while path.exists():
        path = base / f"run-{harness}-{profile}-{day}-{n}.json"
        n += 1
    return path


def frozen_baseline(vault: Path, harness: str, profile: str) -> dict | None:
    pointer = vault / BASELINE_DIR / f"baseline-{harness}-{profile}.json"
    if not pointer.is_file():
        return None
    try:
        name = json.loads(v.read_text(pointer)).get("run")
        return json.loads(v.read_text(vault / BASELINE_DIR / name))
    except (ValueError, OSError, TypeError):
        return None


def trace_metrics(results: list[Result]) -> dict | None:
    """Per-tag trace figures of 22.5: median files read, tokens, cost, local-only reads."""
    traces = [r.trace for r in results if r.trace]
    if not traces:
        return None
    reads = [len(t.get("files_read") or []) for t in traces if t.get("tool_calls_seen", True)]
    return {
        "median_files_read": statistics.median(reads) if reads else None,
        "tokens_input": sum(int((t.get("tokens") or {}).get("input") or 0) for t in traces),
        "tokens_output": sum(int((t.get("tokens") or {}).get("output") or 0) for t in traces),
        "cost_usd": round(sum(r.cost for r in results), 4),
        "local_only_reads": sum(len(t.get("local_only_read_on_frontier") or []) for t in traces),
    }


def summarise(results: list[Result], vault: Path, meta: dict) -> dict:
    by_tag, by_tag_trace = {}, {}
    for tag in TAGS:
        tagged = [r for r in results if tag in r.case.tags]
        value = rate(tagged)
        if value is not None:
            by_tag[tag] = value
        metrics = trace_metrics(tagged)
        if metrics:
            by_tag_trace[tag] = metrics
    traces = [r.trace for r in results if r.trace]
    return {
        **meta,
        "pass_rate": rate(results),
        "passed": sum(r.status == "pass" for r in results),
        "scored": sum(r.status in ("pass", "fail", "error") for r in results),
        "by_tag": by_tag,
        "by_tag_trace": by_tag_trace,
        "gates": gates(results, vault),
        "cost_usd": round(sum(r.cost for r in results), 4),
        "local_only_reads": sorted({p for t in traces for p in t.get("local_only_read_on_frontier") or []}),
        "results": [r.as_json() for r in results],
    }


def print_summary(summary: dict, baseline: dict | None) -> None:
    if summary["local_only_reads"]:
        print("LOCAL-ONLY FILES READ ON FRONTIER: " + ", ".join(summary["local_only_reads"]))
    rate_now = summary["pass_rate"]
    head = f"pass rate {rate_now:.2f} ({summary['passed']}/{summary['scored']})" if rate_now is not None else "pass rate n/a"
    if baseline and baseline.get("pass_rate") is not None and rate_now is not None:
        delta = rate_now - baseline["pass_rate"]
        head += f"   baseline {baseline['pass_rate']:.2f}   Δ {delta:+.2f}"
    print(head)
    tags = "  ".join(f"{tag} {value:.2f}" for tag, value in summary["by_tag"].items())
    if tags:
        print("  " + tags)
    if baseline:
        deltas = [f"{tag} {value - baseline['by_tag'][tag]:+.2f}" for tag, value in summary["by_tag"].items()
                  if tag in (baseline.get("by_tag") or {})]
        if deltas:
            print("  Δ per tag: " + "  ".join(deltas))
    print("gates")
    failed = []
    for gate in summary["gates"]:
        mark = "✓" if gate["pass"] else ("-" if gate["pass"] is None else "✗")
        print(f"  {mark} {gate['name']:<28} {gate['shown']}")
        if gate["pass"] is False:
            failed.append(gate["name"])
    if failed:
        print(f"→ {len(failed)} gate(s) failed: see PRD §17.3 (install QMD, then raise neighbors depth)")
    if summary.get("by_tag_trace"):
        print("traces per tag: median files read, tokens in/out, cost, local-only reads")
        for tag, m in summary["by_tag_trace"].items():
            median = "n/a" if m["median_files_read"] is None else f"{m['median_files_read']:g}"
            print(f"  {tag:<11} {median:>4}  {m['tokens_input']}/{m['tokens_output']}  "
                  f"{m['cost_usd']:.2f} USD  {m['local_only_reads']}")
    print(f"cost {summary['cost_usd']:.2f} USD")


def fingerprint(vault: Path) -> dict[str, tuple[int, int, int]]:
    """Size, modification time and change time of every file a case can read.

    Without git data, eval runs, traces, Obsidian's window state, its trash and system files. The change time
    is set by the system on every write and cannot be set back, so a synced edit with an old date still counts.
    A file that disappears during the walk is skipped.
    """
    found = {}
    for directory, dirs, files in os.walk(vault, onerror=lambda error: None):
        rel_dir = os.path.relpath(directory, vault)
        rel_dir = "" if rel_dir == "." else Path(rel_dir).as_posix() + "/"
        dirs[:] = [d for d in dirs if not (rel_dir == "" and d in FINGERPRINT_SKIP_DIRS)
                   and f"{rel_dir}{d}" not in (BASELINE_DIR, tr.TRACES_DIR)]
        for name in files:
            if name in FINGERPRINT_SKIP_FILES:
                continue
            path = os.path.join(directory, name)
            try:
                if os.path.islink(path):
                    continue
                stat = os.stat(path)
            except OSError:
                continue
            found[rel_dir + name] = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    return found


def digest(found: dict) -> str:
    return hashlib.sha256(json.dumps(sorted(found.items())).encode()).hexdigest()


def changed_files(before: dict, after: dict) -> list[str]:
    """Files added, removed or changed in the owner's vault between two fingerprints (docs/decisions.md D038)."""
    return sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))


def wait_for_quiet(vault: Path, minutes: float, current: dict) -> tuple[dict, bool]:
    """Wait until the vault has had no change for `minutes`, counted from now (when the runner saw the vault
    as `current`), never from file dates. Return its fingerprint then, and False if it never went quiet within
    MAX_WAIT_FACTOR quiet periods."""
    start = last = time.time()
    while time.time() - last < minutes * 60:
        if time.time() - start > MAX_WAIT_FACTOR * minutes * 60:
            return current, False
        time.sleep(max(1.0, min(QUIET_POLL_S, minutes * 60 - (time.time() - last))))
        newer = fingerprint(vault)
        if newer != current:
            current, last = newer, time.time()
    return current, True


def run(vault: Path, harness: str = "claude", profile: str = "frontier", tags: list[str] | None = None,
        max_cost: float | None = None, freeze: bool = False, golden: str | None = None) -> int:
    if harness == "claude" and profile == "local":
        raise v.VaultError("Claude Code sessions in the vault run the frontier profile (its settings set "
                           "ANYBRAIN_PROFILE=frontier); use --harness opencode for the local profile")
    models = load_models(vault)
    settings = eval_settings(models)
    cases = load_golden(Path(golden) if golden else vault / GOLDEN)
    if tags:
        cases = [c for c in cases if set(c.tags) & set(tags)]
    if not cases:
        raise v.VaultError("no case to run")
    judge = make_judge(models, settings, profile) if any(c.expected.get("behaviour") for c in cases) else None
    spent = {"usd": 0.0}
    results: dict[str, Result] = {}

    def task(case: Case) -> Result:
        if max_cost is not None and spent["usd"] >= max_cost:
            return Result(case, "not-run", note=f"cost cap of {max_cost} reached")
        remaining = None if max_cost is None else max_cost - spent["usd"]
        result = run_case(case, vault, models, harness, profile, settings, judge, remaining)
        spent["usd"] += result.cost
        return result

    # A cap holds only if each case knows what the others spent: with --max-cost, cases run one at a time.
    jobs = 1 if max_cost is not None else max(1, int(settings.get("jobs", 2)))

    superseded: list[Result] = []

    def run_cases(batch: list[Case], label: str = "") -> None:
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = {pool.submit(task, case): case for case in batch}
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                earlier = results.get(result.case.id)
                if earlier and result.status == "not-run":
                    earlier.note = (earlier.note + "; " if earlier.note else "") + "stale: not run again (cost cap)"
                    print(f"{result.case.id:<6} not run again: {result.note}")
                    continue
                if earlier:
                    superseded.append(earlier)  # what it did still counts: reads, writes, a failed safety case
                results[result.case.id] = result
                trace = result.trace or {}
                leak = " LOCAL-ONLY READ" if trace.get("local_only_read_on_frontier") else ""
                extra = (f" ({result.cost:.2f} USD, {len(trace.get('files_read') or [])} file(s) read)"
                         if isinstance(trace.get("cost_usd"), (int, float)) else "")
                note = f": {result.note}" if result.note else ""
                print(f"{result.case.id:<6} {result.status}{leak}{extra}{note}{label}")

    def every_result() -> list[Result]:
        return list(results.values()) + superseded

    def escaped_writes() -> list[str]:
        return sorted({p for r in every_result() for p in (r.trace or {}).get("escaped_writes") or []})

    def stale(current: str) -> list[Case]:
        """Cases whose copy of the vault is not the vault as it is now."""
        return [c for c in cases if c.id in results and results[c.id].seen is not None and results[c.id].seen != current]

    reference = fingerprint(vault)
    run_cases(cases)
    relaunches, quiet = [], float(settings.get("quiet_minutes", 15))
    # An edit by the owner during the run leaves some cases on older content: once the vault has been quiet
    # for `quiet_minutes`, those cases run again. A case that named the real vault fails the run at once.
    while True:
        now = fingerprint(vault)
        if not stale(digest(now)) or escaped_writes() or len(relaunches) >= int(settings.get("relaunches", 2)):
            break
        if max_cost is not None and spent["usd"] >= max_cost:
            print(f"the vault changed during the eval, but the cost cap of {max_cost} is reached: not running again")
            break
        print(f"the vault changed during the eval ({', '.join(changed_files(reference, now)[:5])}); waiting for "
              f"{quiet:g} min without edits, then running again the cases that saw older content")
        waited_from = time.time()
        settled, quiet_reached = wait_for_quiet(vault, quiet, now)
        if not quiet_reached:
            print(f"the vault did not stay {quiet:g} min without edits within {MAX_WAIT_FACTOR * quiet:g} min; "
                  "not running again")
            break
        waited_s = round(time.time() - waited_from)
        rerun = stale(digest(settled))
        spent_before = spent["usd"]
        run_cases(rerun, " (run again)")
        relaunches.append({"changed": changed_files(reference, settled), "rerun": [c.id for c in rerun],
                           "waited_s": waited_s, "cost_usd": round(spent["usd"] - spent_before, 4)})
        reference = settled
    final = fingerprint(vault)
    left = stale(digest(final))
    changed = (changed_files(reference, final) or ["(a file changed while a case copied the vault)"]) if left else []
    escaped = escaped_writes()
    ordered = [results[c.id] for c in cases]
    first = next((r.trace for r in ordered if r.trace), {}) or {}
    meta = {"date": v.today().isoformat(), "harness": harness, "harness_version": first.get("harness_version"),
            "model": first.get("model"), "profile": profile, "golden": str(golden or GOLDEN),
            "judge": settings.get("judge"), "tags": tags or []}
    summary = summarise(ordered, vault, meta)
    summary["cost_usd"] = round(spent["usd"], 4)  # every run, including the ones a relaunch replaced
    summary["local_only_reads"] = sorted({p for r in every_result() if r.trace
                                          for p in r.trace.get("local_only_read_on_frontier") or []})
    summary["vault_changed"] = changed
    summary["stale_cases"] = [c.id for c in left]
    summary["relaunches"] = relaunches
    summary["escaped_writes"] = escaped
    summary["superseded"] = [r.as_json() for r in superseded]
    path = run_path(vault, harness, profile)
    v.write_text(path, json.dumps(summary, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {v.rel_path(vault, path)}")
    baseline = frozen_baseline(vault, harness, profile)
    print_summary(summary, baseline)
    if freeze:
        pointer = vault / BASELINE_DIR / f"baseline-{harness}-{profile}.json"
        v.write_text(pointer, json.dumps({"run": path.name, "frozen": v.today().isoformat()}, indent=1) + "\n")
        print(f"wrote {v.rel_path(vault, pointer)} (frozen baseline)")
    if escaped:
        print("A CASE WROTE INTO THE VAULT ITSELF, NOT INTO ITS COPY: " + ", ".join(escaped[:10]))
    elif left:
        print(f"THE VAULT CHANGED DURING THE EVAL ({len(changed)} file(s)), still after {len(relaunches)} "
              f"relaunch(es): {', '.join(changed[:10])}; cases on older content: {', '.join(c.id for c in left)}")
    hard_fail = summary["local_only_reads"] or left or escaped or any(
        r.status in ("fail", "error") and set(r.case.tags) & set(HARD_TAGS) for r in ordered + superseded)
    return 1 if hard_fail else 0
