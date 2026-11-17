"""Command-line entry point: one argparse dispatcher for every subcommand (PRD section 9.3).

Exit codes: 0 success, 1 findings (checks) or refusal, 2 usage or environment error.
"""
from __future__ import annotations

import argparse
import os
import sys

from . import __version__

# Commands listed in PRD 9.3 that belong to later build steps. They are registered so that
# `anybrain --help` shows the full surface, and they say plainly that they are not built yet.
PLANNED = {
    "content": ("Draft snapshots, edit ratio and content checks", "P1b, PRD section 24 step 15"),
    "migrate": ("Snapshot a legacy vault and check owner text survived", "P1, PRD section 24 step 11"),
    "transcribe": ("Transcribe new audio in raw/voice/ locally", "P3"),
}


class UsageError(Exception):
    """Raised for a bad invocation or a missing vault; reported with exit code 2."""


def vault_arg(value: str | None) -> str:
    """Return the vault path from the argument, else from ANYBRAIN_VAULT (PRD 9.2)."""
    if value:
        return value
    env = os.environ.get("ANYBRAIN_VAULT")
    if env:
        return env
    raise UsageError("no vault given: pass the vault path or set ANYBRAIN_VAULT")


def _cmd_index(args: argparse.Namespace) -> int:
    from . import index, vault

    return index.run(vault.resolve_vault(vault_arg(args.vault)), check=args.check, stats=args.stats)


def _cmd_neighbors(args: argparse.Namespace) -> int:
    from . import graph, vault

    if len(args.args) == 1:
        root, page = vault_arg(None), args.args[0]
    elif len(args.args) == 2:
        root, page = args.args
    else:
        raise UsageError("usage: anybrain neighbors [vault] <page>")
    return graph.run_neighbors(vault.resolve_vault(root), page, depth=args.depth, as_json=args.json)


def _cmd_init(args: argparse.Namespace) -> int:
    from . import install

    return install.init(vault_arg(args.vault), owner=args.owner, role=args.role, lang=args.lang)


def _cmd_update(args: argparse.Namespace) -> int:
    from . import install

    return install.update(vault_arg(args.vault), yes=args.yes)


def _cmd_doctor(args: argparse.Namespace) -> int:
    from . import install

    return install.doctor(vault_arg(args.vault))


LINT_CHECKS = ("schema", "links", "graph", "stale", "protected", "quotes", "secrets", "feedback", "icloud")


def _cmd_lint(args: argparse.Namespace) -> int:
    from . import lint, vault

    root = vault.resolve_vault(vault_arg(args.vault))
    selected = [name for name in LINT_CHECKS if getattr(args, name)]
    return lint.run(root, selected, report_only=args.report_only, report=args.report, accept=args.accept)


def _cmd_anonymize(args: argparse.Namespace) -> int:
    from . import anonymize

    return anonymize.run(args.file)


FEEDBACK_ACTIONS = ("add", "topics", "proposals")


def _cmd_feedback(args: argparse.Namespace) -> int:
    from . import feedback, vault

    if len(args.args) == 1 and args.args[0] in FEEDBACK_ACTIONS:
        root, action = vault_arg(None), args.args[0]
    elif len(args.args) == 2 and args.args[1] in FEEDBACK_ACTIONS:
        root, action = args.args
    else:
        raise UsageError("usage: anybrain feedback [vault] {add,topics,proposals}")
    root = vault.resolve_vault(root)
    if action == "add":
        missing = [f"--{name}" for name in ("topic", "skill", "note") if not getattr(args, name)]
        if missing:
            raise UsageError(f"feedback add needs {', '.join(missing)}")
        return feedback.run_add(root, args.topic, args.skill, args.note)
    if action == "topics":
        return feedback.run_topics(root)
    return feedback.run_proposals(root)


def _cmd_trace(args: argparse.Namespace) -> int:
    from . import trace, vault

    if len(args.args) == 1 and args.args[0] in ("show",):
        root, action = vault_arg(None), args.args[0]
    elif len(args.args) == 2 and args.args[1] in ("show",):
        root, action = args.args
    else:
        raise UsageError("usage: anybrain trace [vault] show [--last N] [--skill NAME]")
    return trace.show(vault.resolve_vault(root), last=args.last, skill=args.skill)


def _cmd_eval(args: argparse.Namespace) -> int:
    from . import evals, vault

    tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else None
    return evals.run(vault.resolve_vault(vault_arg(args.vault)), harness=args.harness, profile=args.profile,
                     tags=tags, max_cost=args.max_cost, freeze=args.freeze, golden=args.golden)


def _cmd_stats(args: argparse.Namespace) -> int:
    from . import stats, vault

    return stats.run(vault.resolve_vault(vault_arg(args.vault)), weeks=args.weeks)


def _not_built(step: str):
    def run(args: argparse.Namespace) -> int:
        print(f"anybrain {args.command}: not built yet ({step})", file=sys.stderr)
        return 2

    return run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anybrain",
        description="Your second brain in Markdown. Any model, any agent.",
        epilog="Every command takes the vault path first, or reads ANYBRAIN_VAULT.",
    )
    parser.add_argument("--version", action="version", version=f"anybrain {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p = sub.add_parser("init", help="Create a vault: folders, templates, config, AGENTS.md, skill links, index")
    p.add_argument("vault", nargs="?")
    p.add_argument("--owner", metavar="NAME", help="owner name written into AGENTS.md")
    p.add_argument("--role", metavar="ROLE", help="owner role written into AGENTS.md")
    p.add_argument("--lang", choices=("fr", "en"), help="default language of new pages")
    p.set_defaults(func=_cmd_init)

    p = sub.add_parser("update", help="Re-render framework files; never touches content folders")
    p.add_argument("vault", nargs="?")
    p.add_argument("--yes", action="store_true", help="do not ask; keep every file you modified")
    p.set_defaults(func=_cmd_update)

    p = sub.add_parser("doctor", help="Check the environment around a vault")
    p.add_argument("vault", nargs="?")
    p.set_defaults(func=_cmd_doctor)

    p = sub.add_parser("index", help="Build every index.md and .harness/graph.json")
    p.add_argument("vault", nargs="?")
    p.add_argument("--check", action="store_true", help="write nothing; exit 1 if anything would change")
    p.add_argument("--stats", action="store_true", help="print pages, edges, orphans and index token estimates")
    p.set_defaults(func=_cmd_index)

    p = sub.add_parser("neighbors", help="Print pages linked to and from a page")
    p.add_argument("args", nargs="+", metavar="[vault] page")
    p.add_argument("--depth", type=int, choices=(1, 2), default=1, help="2 adds neighbours of neighbours")
    p.add_argument("--json", action="store_true", help="print JSON instead of text")
    p.set_defaults(func=_cmd_neighbors)

    p = sub.add_parser("lint", help="Deterministic checks; no flag runs them all")
    p.add_argument("vault", nargs="?")
    for name in LINT_CHECKS:
        p.add_argument("--" + name, action="store_true")
    p.add_argument("--report-only", action="store_true", help="exit 0 even with findings")
    p.add_argument("--report", metavar="FILE", help="also write the findings to FILE (relative to the vault)")
    p.add_argument("--accept", metavar="PATH", action="append",
                   help="record the protected sections of PATH as the owner's (repeatable)")
    p.set_defaults(func=_cmd_lint)

    p = sub.add_parser("feedback", help="Log owner corrections and list rule proposals")
    p.add_argument("args", nargs="+", metavar="[vault] {add,topics,proposals}")
    p.add_argument("--topic", metavar="SLUG", help="kebab-case topic, reused when one fits (add)")
    p.add_argument("--skill", metavar="NAME", help="skill that produced the corrected output (add)")
    p.add_argument("--note", metavar="TEXT", help="the correction, in one line (add)")
    p.set_defaults(func=_cmd_feedback)

    p = sub.add_parser("anonymize", help="Print a PII-masked copy of a file to stdout")
    p.add_argument("file")
    p.set_defaults(func=_cmd_anonymize)

    p = sub.add_parser("stats", help="Usage counts from log.md per week and per skill")
    p.add_argument("vault", nargs="?")
    p.add_argument("--weeks", type=int, default=4, help="number of weeks to show (default 4)")
    p.set_defaults(func=_cmd_stats)

    p = sub.add_parser("eval", help="Run the golden set on a harness, with a run trace per case, and print the gates")
    p.add_argument("vault", nargs="?")
    p.add_argument("--harness", choices=("claude", "opencode"), default="claude")
    p.add_argument("--profile", choices=("frontier", "local"), default="frontier")
    p.add_argument("--tags", metavar="T,...", help="only cases with one of these tags")
    p.add_argument("--max-cost", type=float, metavar="AMOUNT",
                   help="stop starting cases once this cost is spent (in the currency the harness reports)")
    p.add_argument("--freeze", action="store_true", help="mark this run as the baseline")
    p.add_argument("--golden", metavar="FILE", help="golden set to use instead of .harness/golden.yaml")
    p.set_defaults(func=_cmd_eval)

    p = sub.add_parser("trace", help="Print recent run traces: files read and written, tokens, cost, local-only reads")
    p.add_argument("args", nargs="+", metavar="[vault] show")
    p.add_argument("--last", type=int, default=20, help="number of runs to show (default 20)")
    p.add_argument("--skill", metavar="NAME", help="only runs of this skill")
    p.set_defaults(func=_cmd_trace)

    for name, (summary, step) in PLANNED.items():
        p = sub.add_parser(name, help=f"{summary} (not built yet: {step})")
        p.add_argument("rest", nargs=argparse.REMAINDER)
        p.set_defaults(func=_not_built(step))

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    from .vault import VaultError

    try:
        return args.func(args)
    except (UsageError, VaultError) as exc:
        print(f"anybrain: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
