---
name: lint
description: Weekly health check of the vault: broken links, missing frontmatter, stale projects, contradictions between concepts and newer sources, index rebuild, report in journal/. Use on schedule or when the owner says "lint the vault".
---

# lint

Keep the vault consistent. Fix the mechanical, report the judgmental, delete nothing.

## Shared rules
- `AGENTS.md` first. Protected sections are never edited.
- Reading `projects/` on a frontier profile requires `anybrain anonymize` on each page first; `finance/`, `company/`, `me/` are read on the `local` profile only or skipped.

## Method
1. **Deterministic pass.** Run `anybrain lint <vault>`. It reports: broken `[[links]]`; missing required frontmatter; `description` missing or > 150 chars; projects past `next_review`; books `reading` > 90 days; protected-section hash changes since last run; secret patterns outside `finance/`.
2. **Mechanical fixes.** Repair links to renamed files; add missing `updated:`; shorten `description` only by trimming, never by rewriting meaning. Log each fix.
3. **Judgment pass (read-only).** For each page in `wiki/concepts/`: compare `My position` with the `Key ideas` of books added since the concept's `updated`; list contradictions. List concepts with one source for ≥ 60 days as merge candidates. List tags that look like synonyms (`ai`/`artificial-intelligence`).
4. **Rebuild.** `anybrain index <vault>`.
5. **Report.** Write `journal/lint-YYYY-MM-DD.md`: fixed (n, list), needs the owner (contradictions, merges, stale projects, deletions proposed), index stats (pages per domain, root index tokens). ≤ 40 lines. Append to `log.md`.

## Red lines
- Never delete a page or a section. Propose.
- Never change a protected section, even to fix a typo.
- Never resolve a contradiction by editing `My position`; list it.
