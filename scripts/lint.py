#!/usr/bin/env python3
"""Deterministic vault checks. Exit 1 on any finding unless --report-only.

Usage: lint.py <vault_path> [--all | --schema | --links | --stale | --protected | --secrets] [--report-only]

Checks:
  schema     required frontmatter per type; description ≤ 150 chars
  links      every [[wikilink]] and relative (path.md) resolves
  stale      projects past next_review; books `reading` > 90 days
  protected  hash of protected sections vs .harness/protected.json (written on first run)
  secrets    API-key / IBAN / email patterns outside finance/ and company/
"""
import sys, re, json, hashlib, datetime
from pathlib import Path
import yaml

REQ = {"*": ["title", "type", "description", "updated"],
       "book": ["author", "status"], "project": ["status", "kind", "next_review"],
       "concept": ["confidence", "sources"]}
FOLDER_PROTECT = {"library": ["## My take"], "projects": ["## Why"]}
FULL_PROTECT = {"me/aspirations.md", "me/principles.md"}
SECRET_PATTERNS = [r"sk-[A-Za-z0-9]{20,}", r"AKIA[0-9A-Z]{16}", r"\bFR\d{2}(?: ?\d{4}){5} ?\d{3}\b",
                   r"[\w.+-]+@[\w-]+\.[\w.]+"]
FM_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)
LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]|\]\(([^)#\s]+\.md)")
SKIP = {"raw", ".harness", ".git", ".obsidian"}


def pages(vault: Path):
    for p in vault.rglob("*.md"):
        if any(part in SKIP for part in p.relative_to(vault).parts) or p.name in ("index.md", "log.md", "AGENTS.md", "CLAUDE.md"):
            continue
        m = FM_RE.match(p.read_text(encoding="utf-8"))
        yield p, (yaml.safe_load(m.group(1)) or {}) if m else None, (m.group(2) if m else "")


def check_schema(vault):
    out = []
    for p, fm, _ in pages(vault):
        rel = p.relative_to(vault)
        if fm is None:
            out.append(f"schema: no frontmatter — {rel}"); continue
        for f in REQ["*"] + REQ.get(fm.get("type", ""), []):
            if not fm.get(f):
                out.append(f"schema: missing `{f}` — {rel}")
        if len(str(fm.get("description", ""))) > 150:
            out.append(f"schema: description > 150 chars — {rel}")
    return out


def check_links(vault):
    out, known = [], {p.relative_to(vault).with_suffix("").as_posix() for p in vault.rglob("*.md")}
    for p, _, body in pages(vault):
        for wiki, rel in LINK_RE.findall(body):
            if wiki:
                target = wiki.strip()
                ok = target in known or any(k.endswith("/" + target) for k in known)
            else:
                ok = (p.parent / rel).resolve().exists()
            if not ok:
                out.append(f"links: broken `{wiki or rel}` — {p.relative_to(vault)}")
    return out


def check_stale(vault):
    out, today = [], datetime.date.today()
    for p, fm, _ in pages(vault):
        if not fm:
            continue
        rel = p.relative_to(vault)
        if fm.get("type") == "project" and fm.get("status") == "active" and fm.get("next_review"):
            if datetime.date.fromisoformat(str(fm["next_review"])) < today:
                out.append(f"stale: project past next_review ({fm['next_review']}) — {rel}")
        if fm.get("type") == "book" and fm.get("status") == "reading" and fm.get("updated"):
            if (today - datetime.date.fromisoformat(str(fm["updated"]))).days > 90:
                out.append(f"stale: book `reading` for > 90 days — {rel}")
    return out


def protected_hashes(vault):
    h = {}
    for p, fm, body in pages(vault):
        rel = p.relative_to(vault).as_posix()
        if rel in FULL_PROTECT:
            h[rel] = hashlib.sha256(body.encode()).hexdigest(); continue
        heads = (fm or {}).get("protected") or FOLDER_PROTECT.get(rel.split("/")[0], [])
        for head in heads:
            m = re.search(re.escape(head) + r"\n(.*?)(?=\n## |\Z)", body, re.S)
            if m:
                h[f"{rel}#{head}"] = hashlib.sha256(m.group(1).strip().encode()).hexdigest()
    return h


def check_protected(vault):
    store = vault / ".harness" / "protected.json"
    now = protected_hashes(vault)
    if not store.exists():
        store.parent.mkdir(exist_ok=True)
        store.write_text(json.dumps(now, indent=1)); return []
    old = json.loads(store.read_text())
    out = [f"protected: section changed — {k}" for k in now if k in old and old[k] != now[k]]
    if not out:
        store.write_text(json.dumps(now, indent=1))
    return out


def check_secrets(vault):
    out = []
    for p in vault.rglob("*"):
        if not p.is_file() or any(part in SKIP for part in p.relative_to(vault).parts):
            continue
        top = p.relative_to(vault).parts[0]
        if top in ("finance", "company"):
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for pat in SECRET_PATTERNS:
            if re.search(pat, text):
                out.append(f"secrets: pattern `{pat[:12]}…` — {p.relative_to(vault)}"); break
    return out


def main():
    args = sys.argv[1:]
    if not args:
        raise SystemExit(__doc__)
    vault = Path(args[0]).expanduser().resolve()
    want = {a.lstrip("-") for a in args[1:]} or {"all"}
    runs = {"schema": check_schema, "links": check_links, "stale": check_stale,
            "protected": check_protected, "secrets": check_secrets}
    findings = []
    for name, fn in runs.items():
        if "all" in want or name in want:
            findings += fn(vault)
    for f in findings:
        print(f)
    print(f"[lint] {len(findings)} finding(s)")
    sys.exit(1 if findings and "report-only" not in want else 0)


if __name__ == "__main__":
    main()
