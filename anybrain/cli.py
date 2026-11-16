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
    "eval": ("Run the golden set and print the gates", "P1, PRD section 24 step 10"),
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


LINT_CHECKS = ("schema", "links", "graph", "stale", "protected", "quotes", "secrets", "feedback", "icloud")


def _cmd_lint(args: argparse.Namespace) -> int:
    from . import lint, vault

    root = vault.resolve_vault(vault_arg(args.vault))
    selected = [name for name in LINT_CHECKS if getattr(args, name)]
    return lint.run(root, selected, report_only=args.report_only, report=args.report, accept=args.accept)


def _cmd_anonymize(args: argparse.Namespace) -> int:
    from . import anonymize

    return anonymize.main([args.file])


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
    p.set_defaults(func=_not_built("PRD section 24 step 6"))

    p = sub.add_parser("update", help="Re-render framework files; never touches content folders")
    p.add_argument("vault", nargs="?")
    p.set_defaults(func=_not_built("PRD section 24 step 6"))

    p = sub.add_parser("doctor", help="Check the environment around a vault")
    p.add_argument("vault", nargs="?")
    p.set_defaults(func=_not_built("PRD section 24 step 6"))

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
    p.set_defaults(func=_not_built("PRD section 24 step 7"))

    p = sub.add_parser("anonymize", help="Print a PII-masked copy of a file to stdout")
    p.add_argument("file")
    p.set_defaults(func=_cmd_anonymize)

    p = sub.add_parser("stats", help="Usage counts from log.md per week and per skill")
    p.add_argument("vault", nargs="?")
    p.set_defaults(func=_not_built("PRD section 24 step 7"))

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
