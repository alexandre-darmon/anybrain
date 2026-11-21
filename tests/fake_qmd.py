"""A stand-in for QMD in tests: the commands anybrain uses, on files read from disk (no models, no index).

`qmd --index NAME update|embed|pull|search|vsearch|query TEXT -c brain -n N --format json`. The config is read from
$QMD_CONFIG_DIR/NAME.yml as anybrain writes it. FAKE_QMD_LOG appends each call; FAKE_QMD_IGNORE_EXCLUDES=1 plays an
index built before a page became confidential; FAKE_QMD_PULL_FAIL=1 makes the model download fail.
"""
from __future__ import annotations

import fnmatch
import json
import os
import sys
from pathlib import Path

import yaml


def main(argv: list[str]) -> int:
    if os.environ.get("FAKE_QMD_LOG"):
        with open(os.environ["FAKE_QMD_LOG"], "a", encoding="utf-8") as log:
            log.write(json.dumps({"args": argv, "cwd": os.getcwd()}) + "\n")
    index = argv[argv.index("--index") + 1] if "--index" in argv else "index"
    words = [w for i, w in enumerate(argv) if not w.startswith("-") and (i == 0 or argv[i - 1] not in ("--index", "-c", "-n", "--format"))]
    command, rest = words[0], words[1:]
    config = yaml.safe_load(Path(os.environ["QMD_CONFIG_DIR"], f"{index}.yml").read_text()) or {}
    collection = config["collections"]["brain"]
    models = Path(os.environ.get("XDG_CACHE_HOME") or "~/.cache").expanduser() / "qmd" / "models"
    if command in ("update", "embed"):
        print("ok")
        return 0
    if command == "pull":
        if os.environ.get("FAKE_QMD_PULL_FAIL"):
            print("download failed", file=sys.stderr)
            return 1
        models.mkdir(parents=True, exist_ok=True)
        for name in ("embeddinggemma-300M-Q8_0.gguf", "qwen3-reranker-0.6b-q8_0.gguf", "qmd-query-expansion-1.7B-q4_k_m.gguf"):
            (models / name).write_text("x")
        return 0
    if command not in ("search", "vsearch", "query"):
        return 2
    text = " ".join(rest).lower()
    root = Path(collection["path"])
    patterns = str(collection.get("pattern") or "**/*.md").split(",")
    ignore = [] if os.environ.get("FAKE_QMD_IGNORE_EXCLUDES") else collection.get("ignore") or []
    found = []
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(root).as_posix()
        if not any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(rel, p.replace("**/", "")) for p in patterns):
            continue
        if any(fnmatch.fnmatch(rel, p) for p in ignore):
            continue
        body = path.read_text(encoding="utf-8")
        hits = [line for line in body.splitlines() if any(w in line.lower() for w in text.split())]
        if hits:
            found.append({"docid": "#000000", "score": round(min(0.99, 0.3 + 0.1 * len(hits)), 2),
                          "file": f"qmd://brain/{rel}?index={index}", "line": 1, "title": path.stem,
                          "snippet": "@@ -1,3 @@ (0 before, 3 after)\n" + "\n".join(hits[:3])})
    limit = int(argv[argv.index("-n") + 1]) if "-n" in argv else 5
    print(json.dumps(sorted(found, key=lambda x: -x["score"])[:limit]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
