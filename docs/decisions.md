# Decisions

Choices made while building anybrain from `PRD.md`. One entry per decision: the context, what was decided, and why. When a decision answers a gap or an ambiguity in the PRD, it is also listed in PRD section 26 with the default that applies until the owner decides otherwise.

Format: `D<number> · <title>`, with the build step and the PRD sections involved.

---

## D001 · Scaffold scripts without a P0 target are removed, not kept as dead files

- **Step:** 1. **PRD:** 0.4, 9.1, 24.
- **Context.** The scaffold had `scripts/build_index.py`, `lint.py`, `anonymize.py`, `install.sh` and `run_evals.sh`. Section 9.1 has no `scripts/` folder.
- **Decision.** `build_index.py`, `lint.py` and `anonymize.py` are moved with `git mv` to `anybrain/index.py`, `anybrain/lint.py` and `anybrain/anonymize.py`, so their history follows them, and are called by the CLI unchanged until their own step rewrites them. `install.sh` is removed and replaced by `anybrain/install.py` in step 6. `run_evals.sh` is removed and replaced by `anybrain/evals.py` in step 10. Both remain readable in git history (`git show ade23f2:scripts/run_evals.sh`).
- **Why.** Keeping a shell script that calls paths which no longer exist would mislead a reader of a public repo.

## D002 · Framework files ship inside the wheel for non-editable installs

- **Step:** 1. **PRD:** 9.1, 9.2.
- **Context.** Section 9.2 installs with `uv tool install .` or `pipx install .`. Those copy the package into a separate environment, without `skills/` and `templates/`, which live at the repo root and which `init` and `update` need.
- **Decision.** The build backend is `hatchling`. `skills/` and `templates/` are force-included in the wheel under `anybrain/_framework/`. At run time, `anybrain` reads framework files from the repo root when it runs from a checkout (editable install), else from `anybrain/_framework/`. Build-time tools are not runtime dependencies: the only runtime dependency stays `pyyaml`.
- **Why.** It keeps the layout of 9.1 and makes both install modes work.

## D003 · Commands of later phases are registered and say they are not built

- **Step:** 1. **PRD:** 9.3.
- **Decision.** `content`, `migrate`, `eval` and `transcribe` appear in `anybrain --help` with their phase, and exit with code 2 and the message `not built yet (<phase and step>)`. P0 commands are wired as their step lands.
- **Why.** The help text then shows the whole surface of 9.3 without pretending a feature exists.

## D004 · No empty placeholders for files that belong to later steps

- **Step:** 1. **PRD:** 9.1, 24.
- **Decision.** Files of 9.1 that later steps create (`anybrain/migrate.py`, `evals.py`, `content.py`; skills `migrate`, `feedback`, `draft`, `publish`; `evals/rubric.md`; `docs/architecture.md`; page templates `draft`, `published`, `channel`, `voice`) are created by their own step.
- **Why.** An empty `SKILL.md` would be linked into the harness skill directories by `init` and loaded by agents. An empty module or doc reads as done when it is not.

## D005 · Scaffold skills only get path and punctuation fixes before step 8

- **Step:** 1. **PRD:** 15, 24 step 8.
- **Decision.** In the five scaffold skills, references to `scripts/*.py` now point to the matching `anybrain` command, and em dashes are replaced. Their method is rewritten in step 8.
- **Why.** The session scope excludes skill rewriting, and broken command paths would be worse than old wording.

## D006 · Repo hygiene checks run as tests

- **Step:** 1. **PRD:** 0.7, 23, 24 step 12.
- **Decision.** `tests/test_repo_hygiene.py` fails on any em dash (U+2014) in a tracked text file, and on any email or phone pattern outside `vault.example/` and `tests/fixtures/`. CI runs it through `pytest`. Test data that looks like a secret or PII is built at run time or kept in `tests/fixtures/`.
- **Why.** Section 23 asks CI for the PII check, and section 24 step 12 for the em dash check. Both are cheap enough to run from the first commit.
