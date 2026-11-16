#!/usr/bin/env bash
# Run the golden-question suite and print the four scaling gates.
# Usage: scripts/run_evals.sh <vault_path> [--profile frontier|local] [--harness claude|opencode]
# Requires: evalblink (pip install evalblink), the chosen harness on PATH.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
VAULT="${1:?usage: run_evals.sh <vault_path>}"; shift || true
PROFILE=frontier; HARNESS=claude
while [[ $# -gt 0 ]]; do case "$1" in
  --profile) PROFILE="$2"; shift 2;; --harness) HARNESS="$2"; shift 2;; *) shift;; esac; done

GOLDEN="$VAULT/.harness/golden.yaml"; OUT="$VAULT/.harness/baseline/run-$HARNESS-$PROFILE-$(date +%F).json"
[[ -f "$GOLDEN" ]] || { echo "no golden set at $GOLDEN"; exit 1; }

# One evalblink case per question; the harness is driven in headless mode from the vault root.
# The adapter (evals/adapter.py, to implement in step 10) runs:  cd $VAULT && $HARNESS -p "<question>"
# and returns {answer, cited_files, files_opened} for the graders defined in evals/README.md.
evalblink run "$REPO/evals/suite.yaml" \
  --var vault="$VAULT" --var golden="$GOLDEN" --var harness="$HARNESS" --var profile="$PROFILE" \
  --out "$OUT"

python3 - "$OUT" "$VAULT" <<'PY'
import json, sys, subprocess, yaml
run, vault = json.load(open(sys.argv[1])), sys.argv[2]
cases = run["cases"]; n = len(cases)
recall = sum(c["graders"].get("cited_files", False) for c in cases) / n
needle = sum("needle" in c.get("tags", []) for c in cases) / n
opened = sorted(c.get("files_opened", 0) for c in cases); median = opened[n // 2] if n else 0
stats = subprocess.run(["python3", f"{__import__('os').path.dirname(sys.argv[0]) or '.'}/build_index.py", vault, "--stats"],
                       capture_output=True, text=True).stdout
tokens = int(next((l.split("~")[1].split()[0] for l in stats.splitlines() if "root index" in l), 0))
gates = [("recall ≥ 0.90", recall >= 0.90, f"{recall:.2f}"),
         ("root index ≤ 5000 tokens", tokens <= 5000, str(tokens)),
         ("needle questions ≤ 20%", needle <= 0.20, f"{needle:.0%}"),
         ("median files opened ≤ 8", median <= 8, str(median))]
print(f"\npass rate {run['pass_rate']:.2f}  ({n} cases)\n")
for name, ok, val in gates:
    print(f"  {'✓' if ok else '✗'} {name:28} {val}")
print("\nAll ✓ → indexes are enough. Any ✗ → add QMD (docs/SPEC.md §8).")
PY
