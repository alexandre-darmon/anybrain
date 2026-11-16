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

## D010 · What `lint --schema` requires where the PRD is silent

- **Step:** 3. **PRD:** 11.1 to 11.5, 13.6, 15.4. **Open question:** Q11.
- **Context.** Section 11 marks some fields as required and lists others as "extra fields" without saying. Section 11.3 says a concept's `sources` "MUST list at least 2 pages", while the graph check of 13.6 only warns about a concept with one source after 60 days.
- **Decision.**
  - Common fields of 11.1 marked "yes" are required; `tags` and `sources` must be present, as lists, and may be empty.
  - Books: `author` and `status` are required; `rating` is an integer from 1 to 5 and only allowed when `status` is `read` or `abandoned`; `date_read` is `YYYY-MM`.
  - Concepts: `confidence` is required. The two-source rule is checked by `lint --graph` (warning after 60 days), not by `--schema`, so an ingest can create a concept and add its second source later in the same week without an error.
  - Projects: `status`, `kind` and `next_review` are required, as in the scaffold's `lint.py`.
  - Tags must be kebab-case. Filenames of pages must be ASCII kebab-case.
  - Invalid dates (`2026-02-30`) never crash a command: the YAML loader keeps them as text and `--schema` reports them.
- **Why.** It keeps the scaffold behaviour where the PRD does not change it, and follows the more specific rule (the 13.6 table) where two sections disagree.

## D011 · Template conventions

- **Step:** 3. **PRD:** 11.9.
- **Decision.** Placeholders are quoted (`title: "{{title}}"`) so every template is valid YAML. Allowed values of an enum field are given in a YAML comment on the same line. Example lines from the contracts that contain a link (`- [[source-slug]]: ...`) sit inside the HTML guidance comment, so a page created from a template has no broken link. P1b templates (`draft`, `published`, `channel`, `voice`) arrive in step 15 (D004).
- **Why.** A template that fails to parse or that creates a broken link on day one would teach the agent a wrong pattern.

## D012 · graph.json keeps only the title and the structure of confidential pages

- **Step:** 4. **PRD:** 10.3, 13.4, 13.5, 18.4. **Open question:** Q12.
- **Context.** Agents read `.harness/graph.json` (10.3) in any session, including frontier sessions. Section 13.4 does not say what the file holds for confidential pages, while 13.5 lists confidential neighbours "with title only" unless the profile is local, and 14.2 shows confidential domains in the root index "with counts only".
- **Decision.** For a confidential page (effective sensitivity, D009), `graph.json` keeps `title`, `type`, `sensitivity` and the paths of its edges, and empties `description`, `sources`, `broken` and the `context` of every edge that starts on that page. `anybrain neighbors` does not read `graph.json`: it rebuilds the graph from the files each time (under 1.2 s on 2,000 pages) and applies the masking of 13.5 for the current `ANYBRAIN_PROFILE`. On a frontier profile, it also refuses to list the links of a confidential page itself, and never expands depth 2 through a masked page.
- **Why.** The harness deny rules of 18.4 protect `company/` and `finance/`, not `.harness/`. Without this, a confidential description would reach a remote model through the graph file.

## D013 · Index details the PRD leaves open

- **Step:** 4. **PRD:** 14.1, 14.2, 13.5.
- **Decision.**
  - Group order: books follow the root-line example of 14.2 (`read`, `reading`, `to-read`, then `abandoned`); projects follow the enum order of 11.5 (`idea`, `active`, `paused`, `shipped`, `dropped`). A page with an unknown status goes under `## other`. Inside a group, lines are sorted by `updated` descending, then by path.
  - Domains without a status (wiki, me, journal, company, finance) are a flat list. The root line says `N pages`, or the status counts when every page has a status.
  - The root index is titled `# brain` (the scaffold's title) and lists the domains in the order of 10.2, then `log.md`. A confidential domain shows its page count and "confidential, local profile only", without its last update date.
  - Concept lines say `1 source` and `1 in-link` in the singular.
  - `neighbors --depth 2` prefixes each line with its distance (`[1]`, `[2]`) and ends distance-2 lines with `via <page>`.
  - The `generated` timestamp of `graph.json` is ignored when comparing, so `--check` and idempotence hold.
  - Wikilink resolution also accepts a unique path suffix (`[[concepts/x]]`), as the scaffold's `lint.py` did, and falls back to a unique raw file with the same stem when no page has it.
- **Why.** Each point is either stated by an example in the PRD or kept from the scaffold.
