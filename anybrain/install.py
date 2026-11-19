"""init, update and doctor (PRD section 9.3, 9.5, 18.3, 18.4, 20).

Framework files are rendered from the repo into the vault: AGENTS.md (and the CLAUDE.md symlink), page
templates, harness configs and skill links. `.harness/render.json` keeps the hash of each file as last
rendered, so `update` can tell an untouched file (replaced silently) from one the owner edited (diff and
question; `--yes` keeps the owner's version). Content folders are never touched by `update`, and `init`
never overwrites an existing file.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import stat
import sys
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import vault as v

RENDER = ".harness/render.json"
MODELS = ".harness/models.toml"
ENV_FILE = ".harness/.env"
DEFAULT_OWNER = "Owner"
DEFAULT_ROLE = "knowledge worker"
DEFAULT_LANG = "en"
ICLOUD_ROOT = "~/Library/Mobile Documents"
ICLOUD_HINT = "Right-click the vault folder in Finder and choose Keep Downloaded."
DEFAULT_SKILL_DIRS = ["~/.agents/skills", "~/.claude/skills"]
HARNESS_CONFIGS = (".claude/settings.json", "opencode.json")

# PRD 10.2. content/ and .harness/drafts/ came with P1b (docs/decisions.md D019, D044).
LAYOUT_DIRS = [
    "me", "raw/inbox", "raw/highlights", "raw/articles", "raw/voice", "raw/docs", "raw/attachments",
    "raw/archive", "raw/legacy", "library", "wiki/concepts", "wiki/notes", "projects", "company", "finance",
    "journal", "content/channels", "content/drafts", "content/published", ".harness/templates", ".harness/drafts",
]
IDEAS_STARTER = ("# Content ideas\n\n<!-- Append-only. The weekly lint adds ideas here: one line each, with the angle "
                 "and its source paths. -->\n")

STARTER_ME = {
    "me/profile.md": ("Profile", "Who the owner is, in stable facts that stay true for a year. Owner-written.",
                      "<!-- Fully protected. Write stable facts in your own words: role, sectors, what is still true in a year. -->\n"),
    "me/aspirations.md": ("Aspirations", "Goals at 1, 3 and 5 years. Owner-written, fully protected.",
                          "<!-- Fully protected. The agent reads this page and never edits it. -->\n\n## 1 year\n\n## 3 years\n\n## 5 years\n"),
    "me/principles.md": ("Principles", "How the owner decides; rules the agent applies when weighing options.",
                         "<!-- Fully protected. Write the rules you want applied to you. -->\n"),
    "me/preferences.md": ("Preferences", "How the agent should work: owner rules first, then rules learned from corrections.",
                          "<!-- Read by every model, frontier models included (.harness/models.toml routing). "
                          "Write rules for the agent here, never private facts. -->\n\n"
                          "## Owner rules\n<!-- owner only. Protected. -->\n\n## Learned rules\n"
                          "<!-- append-only. The agent appends a rule here only after the owner approves it. -->\n"),
}

BACKUP_HINT = """Nightly backup outside iCloud (PRD 19.6), to add to launchd or cron:
  rsync -a --delete "{vault}/" "$HOME/brain-backup/" \\
    && cd "$HOME/brain-backup" && git add -A && git commit -qm "backup $(date +%F)" || true
~/brain-backup is a private git repository. Never push it to a public remote."""


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------- what gets rendered

@dataclass
class Rendered:
    rel: str  # path inside the vault
    content: str


def load_models(vault: Path) -> dict:
    path = vault / MODELS
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(v.read_text(path))
    except tomllib.TOMLDecodeError as exc:
        raise v.VaultError(f"{MODELS} is not valid TOML: {exc}") from exc


def skill_dirs(vault: Path) -> list[Path]:
    dirs = (load_models(vault).get("skills") or {}).get("dirs") or DEFAULT_SKILL_DIRS
    return [Path(os.path.expanduser(str(d))) for d in dirs]


def repo_skills() -> list[Path]:
    root = v.framework_root() / "skills"
    return sorted(p for p in root.iterdir() if (p / "SKILL.md").is_file())


def render_agents(params: dict) -> str:
    text = v.read_text(v.framework_root() / "templates" / "AGENTS.vault.md")
    return (text.replace("{{OWNER_NAME}}", params.get("owner") or DEFAULT_OWNER)
                .replace("{{OWNER_ROLE}}", params.get("role") or DEFAULT_ROLE)
                .replace("{{LANGUAGE}}", params.get("lang") or DEFAULT_LANG))


# ---------------------------------------------------------------------------- harness configs from routing

def routing_warnings(routing: v.Routing) -> list[str]:
    return [f"{MODELS}: routing entry {key!r} ignored: {reason}" for key, reason in routing.ignored]


def _glob(key: str) -> str:
    """A routing key as a glob: a folder covers everything below it."""
    return key + "**" if key.endswith("/") else key


def claude_read_rules(routing: v.Routing) -> list[str]:
    """Claude Code `permissions.deny` rules for everything routed local, with the frontier exceptions.

    An allow rule never wins over a deny in Claude Code, so an exception is a gitignore negation in the deny
    list. Measured with Claude Code 2.1.326 on 2026-11-17 (docs/decisions.md D027):
    - `Read(./me/**)` or `Read(./me/*)` followed by `Read(!./me/preferences.md)` still denies the exception;
    - `Read(me/*)` followed by `Read(!me/preferences.md)` (no `./`) opens it, and still denies files added
      later and subfolders; `Read(!me/voice/**)` opens a subfolder the same way.
    A folder without exceptions keeps the anchored form `Read(./folder/**)`.
    """
    exceptions = routing.exceptions()
    rules = []
    for folder in routing.local_folders():
        inner = [e for e in exceptions if e.startswith(folder)]
        if not inner:
            rules.append(f"Read(./{folder}**)")
            continue
        rules.append(f"Read({folder}*)")
        for exception in inner:
            rules.append(f"Read(!{_glob(exception)})")
    rules += [f"Read(./{path})" for path in routing.local_files()]
    return rules


def claude_settings(routing: v.Routing) -> str:
    data = json.loads(v.read_text(v.framework_root() / "templates" / "harness" / "claude-settings.json"))
    data.setdefault("permissions", {})["deny"] = claude_read_rules(routing)
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def opencode_config(routing: v.Routing) -> str:
    """OpenCode V2 rules: the last matching rule wins, and agent rules come after the global ones (D020)."""
    data = json.loads(v.read_text(v.framework_root() / "templates" / "harness" / "opencode.json"))
    folders = routing.local_folders()
    rules = [{"action": "read", "resource": _glob(f), "effect": "deny"} for f in folders + routing.local_files()]
    rules += [{"action": "read", "resource": _glob(e), "effect": "allow"} for e in routing.exceptions()]
    data["permissions"] = rules
    data["agents"]["local"]["permissions"] = [{"action": "read", "resource": _glob(f), "effect": "allow"}
                                              for f in folders + routing.local_files()]
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def framework_files(params: dict, routing: v.Routing | None = None) -> list[Rendered]:
    """Every file `update` manages, as rendered from the repo and the vault's routing."""
    routing = routing or v.DEFAULT_ROUTING_OBJ
    root = v.framework_root() / "templates"
    files = [Rendered("AGENTS.md", render_agents(params))]
    for page in sorted((root / "pages").glob("*.md")):
        files.append(Rendered(f".harness/templates/{page.name}", v.read_text(page)))
    files.append(Rendered(".claude/settings.json", claude_settings(routing)))
    files.append(Rendered("opencode.json", opencode_config(routing)))
    return files


def load_render(vault: Path) -> dict:
    path = vault / RENDER
    if not path.is_file():
        return {"version": 1, "params": {}, "files": {}}
    try:
        data = json.loads(v.read_text(path))
    except ValueError:
        return {"version": 1, "params": {}, "files": {}}
    data.setdefault("params", {})
    data.setdefault("files", {})
    return data


def save_render(vault: Path, data: dict, out: Callable[[str], None]) -> None:
    text = json.dumps({"version": 1, "params": data["params"], "files": dict(sorted(data["files"].items()))},
                      indent=1) + "\n"
    path = vault / RENDER
    if not path.is_file() or v.read_text(path) != text:
        v.write_text(path, text)
        out(f"wrote {RENDER}")


# ---------------------------------------------------------------------------- shared steps

def refuse_if_evicted(vault: Path) -> None:
    """An evicted file looks missing; writing a new one in its place would create an iCloud conflict."""
    placeholders = v.icloud_placeholders(vault)
    if placeholders:
        raise v.VaultError(f"{len(placeholders)} file(s) are evicted by iCloud (e.g. {placeholders[0]}), so they look "
                           f"missing and would be replaced. {ICLOUD_HINT} Then run the command again")


def merge_obsidian(vault: Path, out: Callable[[str], None]) -> None:
    """Add anybrain's Obsidian keys that are missing; never change a key the owner already set (PRD 20)."""
    wanted = json.loads(v.read_text(v.framework_root() / "templates" / "obsidian" / "app.json"))
    path = vault / ".obsidian" / "app.json"
    current: dict = {}
    if path.is_file():
        try:
            current = json.loads(v.read_text(path))
        except ValueError:
            out("kept .obsidian/app.json: not valid JSON, left as it is")
            return
    if not isinstance(current, dict):
        out("kept .obsidian/app.json: not a JSON object, left as it is")
        return
    missing = {k: val for k, val in wanted.items() if k not in current}
    if not missing:
        return
    current.update(missing)
    v.write_text(path, json.dumps(current, indent=2) + "\n")
    out(f"wrote .obsidian/app.json (added {', '.join(missing)})")


def link_claude_md(vault: Path, out: Callable[[str], None]) -> None:
    link = vault / "CLAUDE.md"
    if link.is_symlink() and os.readlink(link) == "AGENTS.md":
        return
    if link.exists() or link.is_symlink():
        out("kept CLAUDE.md: it exists and is not a symlink to AGENTS.md")
        return
    link.symlink_to("AGENTS.md")
    out("linked CLAUDE.md -> AGENTS.md")


def skill_link_state(link: Path, target: Path) -> str:
    """'ok', 'missing', 'other' (a link elsewhere) or 'blocked' (a real file or folder)."""
    if link.is_symlink():
        try:
            return "ok" if link.resolve() == target.resolve() else "other"
        except OSError:
            return "other"
    if link.exists():
        return "blocked"
    return "missing"


def link_skills(vault: Path, out: Callable[[str], None], ask: Callable[[str], bool] | None = None) -> None:
    """Symlink every repo skill into every configured skill directory (PRD 18.3).

    A link that points elsewhere is replaced only when `ask` says yes; a real folder is never touched.
    """
    for directory in skill_dirs(vault):
        for skill in repo_skills():
            link = directory / skill.name
            state = skill_link_state(link, skill)
            if state == "ok":
                continue
            if state == "missing":
                directory.mkdir(parents=True, exist_ok=True)
                link.symlink_to(skill, target_is_directory=True)
                out(f"linked {link} -> {skill}")
            elif state == "blocked":
                out(f"kept {link}: a real folder or file is there, not a link to anybrain")
            elif ask is not None and ask(f"{link} points elsewhere. Point it to {skill}?"):
                link.unlink()
                link.symlink_to(skill, target_is_directory=True)
                out(f"linked {link} -> {skill}")
            else:
                out(f"kept {link}: it links somewhere else")


# ---------------------------------------------------------------------------- init

COMMON_FIELDS = ("title", "type", "description", "tags", "language", "created", "updated", "sources", "sensitivity")


def earlier_vault(vault: Path) -> bool:
    """True when the folder shows an anybrain vault made before: its graph, or an index anybrain generated."""
    from .index import HEADER

    if (vault / ".harness" / "graph.json").is_file():
        return True
    for rel in ["index.md"] + [f"{d}/index.md" for d in v.DOMAINS]:
        path = vault / rel
        try:
            if path.is_file() and not path.is_symlink() and v.read_text(path).startswith(HEADER):
                return True
        except (OSError, UnicodeDecodeError):
            continue
    return False


def _anybrain_page(vault: Path, rel: str) -> bool:
    """A file of an earlier anybrain vault: a page in the layout with every common field (11.1) and a known
    type, a file under raw/, or numbers kept as CSV in company/ or finance/ (10.2). Only asked when the folder
    shows an earlier vault (`earlier_vault`)."""
    top = rel.split("/", 1)[0]
    if "/" not in rel or top not in {d.split("/")[0] for d in LAYOUT_DIRS} | {"content"}:
        return False
    if top == "raw" or (top in v.CONFIDENTIAL_FOLDERS and rel.endswith(".csv")):
        return True
    if not rel.endswith(".md"):
        return False
    from . import lint

    try:
        fm, *_ = v.parse_frontmatter(v.read_text(vault / rel))
    except (OSError, UnicodeDecodeError):
        return False
    return (isinstance(fm, dict) and fm.get("type") in lint.TYPES
            and all(field in fm for field in COMMON_FIELDS) and bool(fm.get("title")) and bool(fm.get("description")))


VAULT_ENTRIES = {d.split("/")[0] for d in LAYOUT_DIRS} | {
    "content", "index.md", "log.md", "AGENTS.md", "CLAUDE.md", "opencode.json"}


def refuse_foreign_notes(vault: Path) -> None:
    """Refuse to create a vault in a folder that already holds notes anybrain did not create.

    A legacy vault pasted into the target folder would be mixed with the new one: its folders next to
    `library/` and `wiki/`, and no way to snapshot it, because a snapshot of a folder inside the vault is
    refused (21.2). The notes go to a separate folder and come in through the migration (docs/decisions.md D043).
    """
    if not vault.is_dir() or (vault / RENDER).exists():
        return
    # In a folder anybrain never initialised, every visible file is someone else's: notes, canvases and
    # attachments, even inside folders named like the vault layout.
    found = []
    domains = {f"{d}/index.md" for d in v.DOMAINS}
    earlier = earlier_vault(vault)  # a copy of vault.example, a vault restored from backup (D047, D050)
    for directory, dirs, files in os.walk(vault):
        for name in sorted(d for d in dirs if not d.startswith(".")):
            if os.path.islink(os.path.join(directory, name)):  # a link to a folder elsewhere: someone's files
                found.append(v.rel_path(vault, Path(directory) / name))
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not os.path.islink(os.path.join(directory, d)))
        for name in sorted(files):
            rel = v.rel_path(vault, Path(directory) / name)
            if os.path.islink(os.path.join(directory, name)) and rel != "CLAUDE.md":
                if not name.startswith("."):
                    found.append(rel)  # a link to a file elsewhere: someone's note, and edits would go there
                continue
            if name.startswith(".") or rel in VAULT_ENTRIES or rel in v.APPEND_ONLY_FILES:
                continue
            if rel == "index.md" or rel in domains:
                continue  # a hand-written index, which init keeps as the owner wrote it (Q19)
            if v.is_generated(vault, rel):
                continue  # the dashboard `anybrain stats --dashboard` wrote
            if earlier and _anybrain_page(vault, rel):
                continue
            found.append(rel)
    if found == ["Welcome.md"]:
        raise v.VaultError(f"{vault} holds Obsidian's sample page Welcome.md: delete it, then run init again")
    if found:
        legacy = vault.parent / f"{vault.name}-legacy"
        raise v.VaultError(
            f"{vault} already holds {len(found)} file(s) that anybrain did not create (for example "
            f"{', '.join(found[:3])}). init does not mix them with a new vault. Move them to a separate folder, "
            f"for example {legacy}, run init again on the empty folder, then migrate them: "
            f"anybrain migrate \"{vault}\" inventory \"{legacy}\"")



def init(target: str, owner: str | None = None, role: str | None = None, lang: str | None = None,
         out: Callable[[str], None] = print) -> int:
    vault = Path(target).expanduser().resolve()
    if vault.exists() and not vault.is_dir():
        raise v.VaultError(f"not a folder: {vault}")
    v.check_eval_copy(vault)
    refuse_if_evicted(vault)
    refuse_foreign_notes(vault)
    written = 0

    def say(line: str) -> None:
        nonlocal written
        if line.startswith(("wrote", "created", "linked")):
            written += 1
        out(line)

    for rel in LAYOUT_DIRS:
        path = vault / rel
        if not path.is_dir():
            path.mkdir(parents=True, exist_ok=True)
            say(f"created {rel}/")

    render = load_render(vault)
    params = {"owner": owner or render["params"].get("owner") or DEFAULT_OWNER,
              "role": role or render["params"].get("role") or DEFAULT_ROLE,
              "lang": lang or render["params"].get("lang") or DEFAULT_LANG}
    if not render["params"]:
        render["params"] = params
    models = vault / MODELS
    if not models.exists():
        v.write_text(models, v.read_text(v.framework_root() / "templates" / "models.example.toml"))
        say(f"wrote {MODELS}")
    for item in framework_files(params, v.load_routing(vault)):
        path = vault / item.rel
        if path.exists():
            if v.read_text(path) == item.content and item.rel not in render["files"]:
                render["files"][item.rel] = sha(item.content)
            continue
        v.write_text(path, item.content)
        render["files"][item.rel] = sha(item.content)
        say(f"wrote {item.rel}")
    link_claude_md(vault, say)

    config = {
        ".harness/clients.txt": "# Client names, one per line. lint --secrets reports them outside mission pages.\n",
        ".harness/names.txt": "# Names to mask with anybrain anonymize, one per line.\n",
        "log.md": f"{v.today().isoformat()} · init · created vault from anybrain\n",
        ".gitignore": ".harness/.env\n.harness/baseline/\n",
    }
    for rel, text in config.items():
        if not (vault / rel).exists():
            v.write_text(vault / rel, text)
            say(f"wrote {rel}")
    if ".harness/.env" not in v.read_text(vault / ".gitignore").split():
        out("note: add `.harness/.env` to .gitignore so the backup repository never stores your keys")
    env = vault / ENV_FILE
    if not env.exists():
        fd = os.open(env, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("FRONTIER_API_KEY=\n")
        say(f"wrote {ENV_FILE} (chmod 600)")

    template = v.read_text(v.framework_root() / "templates" / "pages" / "profile.md")
    head = template.split("\n---\n", 1)[0] + "\n---\n"
    day = v.today().isoformat()
    for rel, (title, description, body) in STARTER_ME.items():
        if (vault / rel).exists():
            continue
        page = (head.replace("{{title}}", title).replace("{{description}}", description)
                    .replace("{{language}}", params["lang"]).replace("{{created}}", day).replace("{{updated}}", day))
        v.write_text(vault / rel, page + "\n" + body)
        say(f"wrote {rel}")

    voice = vault / "me" / "voice.md"
    if not voice.exists():
        text = v.read_text(v.framework_root() / "templates" / "pages" / "voice.md")
        v.write_text(voice, text.replace("{{language}}", params["lang"]).replace("{{created}}", day)
                     .replace("{{updated}}", day))
        say("wrote me/voice.md")
    ideas = vault / "content" / "ideas.md"
    if not ideas.exists():
        v.write_text(ideas, IDEAS_STARTER)
        say("wrote content/ideas.md")

    merge_obsidian(vault, say)
    link_skills(vault, say)
    save_render(vault, render, say)

    from . import index

    code = index.run(vault)
    if code != 0:
        return code
    if written:
        out("")
        out(BACKUP_HINT.format(vault=vault))
        out("")
        out("Next: write me/profile.md, me/aspirations.md and me/principles.md in your own words; put your API key "
            f"in {ENV_FILE}; turn on iCloud Advanced Data Protection.")
    out(f"init: {written} change(s)")
    return 0


# ---------------------------------------------------------------------------- update

def _ask_tty(question: str) -> bool:
    """Ask on the terminal; with no terminal, the answer is no (keep the owner's version)."""
    if not sys.stdin.isatty():
        return False
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def update(target: str | None, yes: bool = False, out: Callable[[str], None] = print,
           ask: Callable[[str], bool] | None = None) -> int:
    vault = v.resolve_vault(target)
    refuse_if_evicted(vault)
    if not (vault / RENDER).exists():
        refuse_foreign_notes(vault)  # update never makes a folder look initialised (D043)
    ask = ask or _ask_tty
    render = load_render(vault)
    params = render["params"] or {"owner": DEFAULT_OWNER, "role": DEFAULT_ROLE, "lang": DEFAULT_LANG}
    render["params"] = params
    routing = v.load_routing(vault)
    for message in routing_warnings(routing):
        out(f"note: {message}")
    for item in framework_files(params, routing):
        path = vault / item.rel
        new_hash = sha(item.content)
        if not path.exists():
            v.write_text(path, item.content)
            render["files"][item.rel] = new_hash
            out(f"wrote {item.rel}")
            continue
        current = v.read_text(path)
        if current == item.content:
            render["files"][item.rel] = new_hash
            continue
        if render["files"].get(item.rel) == sha(current):
            v.write_text(path, item.content)
            render["files"][item.rel] = new_hash
            out(f"updated {item.rel}")
            continue
        if yes:
            out(f"kept {item.rel}: you modified it (--yes keeps your version)")
            continue
        out(f"{item.rel} was modified since the last render:")
        for line in difflib.unified_diff(current.splitlines(), item.content.splitlines(),
                                         f"{item.rel} (yours)", f"{item.rel} (anybrain)", lineterm=""):
            out(line)
        if ask(f"Replace {item.rel} with the new version?"):
            v.write_text(path, item.content)
            render["files"][item.rel] = new_hash
            out(f"updated {item.rel}")
        else:
            out(f"kept {item.rel}")
    link_claude_md(vault, out)
    link_skills(vault, out, ask=None if yes else ask)
    merge_obsidian(vault, out)
    save_render(vault, render, out)
    if note := voice_route_note(vault):
        out(f"note: {note}")
    return 0


def voice_route_note(vault: Path) -> str | None:
    """AGENTS.md and the skills say every model reads me/voice.md (Q35). A vault made before 0.1.0 may still
    route it local: say so instead of rewriting the owner's routing (D050)."""
    try:
        local = v.load_routing(vault).route("me/voice.md") == "local"
    except v.VaultError:
        return None
    if not local:
        return None
    # a line for me/voice.md, kept or ignored for an unknown profile: change it, since a second one breaks the TOML
    table = (load_models(vault).get("routing") or {})
    explicit = any(str(key).strip().removeprefix("./") == "me/voice.md" for key in table)
    how = ('set the existing `"me/voice.md"` line under [routing] to "frontier"' if explicit
           else 'add the line `"me/voice.md" = "frontier"` under [routing]')
    return ('.harness/models.toml keeps me/voice.md local, so frontier sessions cannot read your voice profile, '
            f'while AGENTS.md and the skills say every model can. To let them, {how}, then run anybrain update')


# ---------------------------------------------------------------------------- doctor (9.5)

def _git_above(path: Path) -> Path | None:
    for parent in [path, *path.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def doctor(target: str | None, out: Callable[[str], None] = print, timeout: float = 2.0) -> int:
    vault = v.resolve_vault(target)
    failures = 0

    def report(level: str, message: str) -> None:
        nonlocal failures
        failures += level == "fail"
        out(f"{level:<5} {message}")

    if os.access(vault, os.W_OK):
        report("ok", f"vault exists and is writable: {vault}")
    else:
        report("fail", f"vault is not writable: {vault}")

    repo = _git_above(vault)
    icloud = Path(os.path.expanduser(ICLOUD_ROOT))
    in_icloud = vault == icloud or icloud in vault.parents or icloud.resolve() in vault.parents
    if repo:
        where = "contains a .git directory" if repo == vault else f"is inside the git repository {repo}"
        report("fail" if in_icloud else "warn",
               f"vault {where}; keep git out of iCloud and use a backup repository (PRD 19.6)")
    else:
        report("ok", "vault is not in a git repository")

    placeholders = sorted(v.rel_path(vault, p) for p in vault.rglob("*.icloud")
                          if p.is_file() and not v.rel_path(vault, p).startswith(".git/"))
    if placeholders:
        report("fail", f"{len(placeholders)} iCloud placeholder file(s), e.g. {placeholders[0]}. {ICLOUD_HINT}")
    else:
        report("ok", "no iCloud placeholder files")

    try:
        dirs = skill_dirs(vault)
        problems = [f"{d / s.name} ({state})" for d in dirs for s in repo_skills()
                    if (state := skill_link_state(d / s.name, s)) != "ok"]
        if problems:
            report("fail", f"skills not linked: {', '.join(problems)}. Run anybrain update")
        else:
            report("ok", f"skills linked in {', '.join(str(d) for d in dirs)}")
    except v.VaultError as exc:
        report("fail", str(exc))

    render = load_render(vault)
    agents = vault / "AGENTS.md"
    if not agents.is_file():
        report("fail", "AGENTS.md is missing. Run anybrain update")
    elif render["files"].get("AGENTS.md") != sha(v.read_text(agents)):
        report("warn", "AGENTS.md differs from the last render. Run anybrain update to see the difference")
    else:
        report("ok", "AGENTS.md matches the last render")
    claude = vault / "CLAUDE.md"
    if claude.is_symlink() and os.readlink(claude) == "AGENTS.md":
        report("ok", "CLAUDE.md is a symlink to AGENTS.md")
    else:
        report("fail", "CLAUDE.md is not a symlink to AGENTS.md. Run anybrain update")

    try:
        routing = v.load_routing(vault)
    except v.VaultError as exc:
        routing = None
        report("fail", str(exc))
    if routing is not None:
        for message in routing_warnings(routing):
            report("warn", message)
        params = render["params"] or {"owner": DEFAULT_OWNER, "role": DEFAULT_ROLE, "lang": DEFAULT_LANG}
        for item in framework_files(params, routing):
            if item.rel not in HARNESS_CONFIGS:
                continue
            path = vault / item.rel
            if not path.is_file():
                report("fail", f"{item.rel} is missing, so nothing denies local-only files. Run anybrain update")
            elif v.read_text(path) == item.content:
                report("ok", f"{item.rel} denies what the routing keeps local")
            elif render["files"].get(item.rel) == sha(v.read_text(path)):
                report("fail", f"{item.rel} does not match the routing of {MODELS}. Run anybrain update")
            else:
                report("warn", f"{item.rel} was modified by you; check that it still denies what the routing keeps local")

    try:
        local = (load_models(vault).get("profiles") or {}).get("local") or {}
    except v.VaultError as exc:
        local = {}
        report("fail", str(exc))
    base = str(local.get("base_url") or "").rstrip("/")
    if not base:
        report("warn", f"no local model endpoint in {MODELS}")
    else:
        try:
            with urllib.request.urlopen(f"{base}/models", timeout=timeout) as response:
                report("ok" if response.status == 200 else "warn", f"local model endpoint answers: {base}/models")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            report("warn", f"local model endpoint does not answer: {base}/models ({reason})")

    env = vault / ENV_FILE
    if not env.is_file():
        report("fail", f"{ENV_FILE} is missing. Run anybrain init")
    elif stat.S_IMODE(env.stat().st_mode) & 0o077:
        report("fail", f"{ENV_FILE} is readable by other users. Run: chmod 600 {env}")
    else:
        report("ok", f"{ENV_FILE} exists and is private (600)")

    if note := voice_route_note(vault):
        report("warn", note)
    report("note", "iCloud Advanced Data Protection should be ON (cannot be checked from here).")
    out(f"doctor: {failures} problem(s)")
    return 1 if failures else 0
