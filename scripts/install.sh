#!/usr/bin/env bash
# Create (or refresh) a vault from this repo. Idempotent.
# Usage: scripts/install.sh <vault_path> [--owner "Name"] [--role "Role"] [--backup]
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
VAULT="${1:?usage: install.sh <vault_path>}"; shift || true
OWNER="Owner"; ROLE="knowledge worker"; BACKUP=0
while [[ $# -gt 0 ]]; do case "$1" in
  --owner) OWNER="$2"; shift 2;; --role) ROLE="$2"; shift 2;; --backup) BACKUP=1; shift;;
  *) echo "unknown flag $1"; exit 1;; esac; done

echo "→ vault: $VAULT"
mkdir -p "$VAULT"
# 1. folders and example pages (never overwrite existing files)
(cd "$REPO/vault.example" && find . -type d -exec mkdir -p "$VAULT/{}" \;)
(cd "$REPO/vault.example" && find . -type f | while read -r f; do
  [[ -e "$VAULT/$f" ]] || cp "$f" "$VAULT/$f"; done)
mkdir -p "$VAULT/.harness/baseline" "$VAULT/.harness/templates" "$VAULT/raw/"{articles,highlights,voice,docs}

# 2. instructions, with placeholders filled
sed -e "s/{{OWNER_NAME}}/$OWNER/g" -e "s/{{OWNER_ROLE}}/$ROLE/g" \
  "$REPO/templates/AGENTS.vault.md" > "$VAULT/AGENTS.md"
ln -sfn AGENTS.md "$VAULT/CLAUDE.md"
touch "$VAULT/log.md"

# 3. templates, config, evals (copy if absent)
cp -n "$REPO/templates/"{book,concept,project,daily-brief}.md "$VAULT/.harness/templates/" 2>/dev/null || true
cp -n "$REPO/templates/models.example.toml" "$VAULT/.harness/models.toml" 2>/dev/null || true
cp -n "$REPO/templates/opencode.example.json" "$VAULT/opencode.json" 2>/dev/null || true
cp -n "$REPO/evals/golden.example.yaml" "$VAULT/.harness/golden.yaml" 2>/dev/null || true
[[ -e "$VAULT/.harness/.env" ]] || printf 'FRONTIER_API_KEY=\n' > "$VAULT/.harness/.env"
echo "SCRIPTS=$REPO/scripts" > "$VAULT/.harness/paths"
grep -q '^\.harness/\.env' "$VAULT/.gitignore" 2>/dev/null || printf '.harness/.env\n.harness/baseline/\n' >> "$VAULT/.gitignore"

# 4. skills into the cross-harness location
mkdir -p "$HOME/.agents/skills"
for s in "$REPO"/skills/*/; do ln -sfn "$s" "$HOME/.agents/skills/$(basename "$s")"; done

# 5. first index + lint
python3 "$REPO/scripts/build_index.py" "$VAULT"
python3 "$REPO/scripts/lint.py" "$VAULT" --schema --links --report-only

if [[ $BACKUP -eq 1 ]]; then cat <<EOF

Nightly backup outside iCloud (add to crontab or launchd):
  rsync -a --delete "$VAULT/" "\$HOME/brain-backup/" && cd "\$HOME/brain-backup" && git add -A && git commit -qm "backup \$(date +%F)" || true
EOF
fi

cat <<EOF

✓ installed. Next:
  1. Enable iCloud Advanced Data Protection (Settings → iCloud → Advanced Data Protection).
  2. Put your API key in $VAULT/.harness/.env ; start Ollama for the local profile.
  3. Write me/profile.md, me/aspirations.md, me/principles.md in your own words.
  4. cd "$VAULT" && claude   (or opencode)   →  "ingest raw/highlights/<file>.md"
  5. Write 15 golden questions in .harness/golden.yaml, then scripts/run_evals.sh
EOF
