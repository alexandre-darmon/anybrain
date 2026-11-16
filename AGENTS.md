# AGENTS.md — working on the personal-harness repo

This repo is the **method**, not the data. It must stay free of any personal information.

- Spec: `docs/SPEC.md`. Build in the order of its section 11. Each section has acceptance criteria; a step is done when they pass.
- `templates/AGENTS.vault.md` is what gets installed into a user's vault; this file is only about the repo.
- Skills follow the Agent Skills standard (`SKILL.md` with `name` and `description` frontmatter). They must not reference a specific model, provider or harness feature.
- Scripts are Python 3.11+ standard library only (`pyyaml` is the single allowed dependency). Tests with `pytest` under `tests/`, run against `vault.example/`.
- `vault.example/` contains fake data only. `scripts/lint.py --secrets` must pass on it.
- Commit messages: `<area>: <what>` (`skills: add recommend`, `scripts: index grouping by status`).
- Do not add features absent from the spec; add them to `docs/SPEC.md` section 12 as an open decision instead.
