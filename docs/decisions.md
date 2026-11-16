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

## D007 · Slugs drop a leading article only

- **Step:** 2. **PRD:** 13.2, 13.3, 21.1. **Open question:** Q9.
- **Context.** Section 13.2 says slugs have "no articles" (`snow-leopard.md`), but sections 13.3 and 21.1 use `the-art-and-business-of-online-writing` and `this-book-will-make-you-dangerous`, which keep words such as "and" and "of".
- **Decision.** `slugify` removes one leading article (`the`, `a`, `an`, `le`, `la`, `les`, `l'`, `un`, `une`), transliterates accents, turns `&` into `and`, and keeps every other word. `The Snow Leopard` gives `snow-leopard`; `création de niche` gives `creation-de-niche`.
- **Why.** Removing articles inside a title changes its meaning and makes slugs harder to guess; the leading article is the one that every example removes.

## D008 · `ANYBRAIN_TODAY` pins the date for tests and CI

- **Step:** 2. **PRD:** 13.6, 15.4 (`--stale`), 16.2.
- **Context.** Several checks depend on today's date (orphans younger than 7 days, projects past `next_review`, books `reading` for 90 days, the 90-day feedback window). Without a fixed date, the same vault passes one week and fails the next.
- **Decision.** `anybrain.vault.today()` reads `ANYBRAIN_TODAY=YYYY-MM-DD` when set, else the system date. Tests pin 2026-11-16. CI pins the same date for `anybrain lint vault.example`, because `vault.example/` is a snapshot taken on that date.
- **Why.** It is test infrastructure, not a product feature: an owner never needs to set it.

## D009 · A page can be more private than its folder, never less

- **Step:** 2. **PRD:** 10.3, 11.1, 13.3 rule 5, 18.4. **Open question:** Q10.
- **Context.** Section 11.1 says `sensitivity` "defaults from section 10.3" but does not say what happens when a page declares a weaker value than its folder (for example `sensitivity: public` in `company/`).
- **Decision.** The effective sensitivity is the stricter of the declared value and the folder default. Routing, the confidential leak check and `neighbors` use the effective value.
- **Why.** A typo in frontmatter must not be enough to send a confidential page to a remote model.
