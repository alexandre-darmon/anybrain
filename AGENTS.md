# AGENTS.md: working on the anybrain repo

This repo is the **method**, not the data. It must stay free of any personal information.

- Spec: `PRD.md`. Build in the order of its section 24. Each step has acceptance criteria; a step is done when they pass.
- `templates/AGENTS.vault.md` is what gets rendered into a user's vault; this file is only about the repo.
- Skills follow the Agent Skills standard (`SKILL.md` with `name` and `description` frontmatter). They must not reference a specific model, provider, harness, slash command or tool name.
- Code is Python 3.11+, standard library plus `pyyaml` only. Tests use `pytest` under `tests/`; fixtures live in `tests/fixtures/` and `vault.example/`.
- `vault.example/` contains fake data only. `anybrain lint vault.example` must pass.
- No em dashes and no hype words in any file. `tests/test_repo_hygiene.py` checks the em dash.
- Commit messages: `<area>: <what>` (`graph: add backlinks and neighbors`).
- Do not add features absent from the PRD. When something is missing or ambiguous, take the most conservative option, record it in `docs/decisions.md`, and add it to PRD section 26.
