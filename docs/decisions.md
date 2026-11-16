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

## D014 · How protected and append-only content is hashed

- **Step:** 5. **PRD:** 12.
- **Decision.**
  - A protected section is hashed without its heading line, after trimming, LF line endings and removal of trailing spaces. Dated agent notes (`> Agent note YYYY-MM-DD: ...`) at the end of the section are removed before hashing, so the agent can append them as 12.1 allows. A note inserted inside the owner's text still changes the hash.
  - A fully protected file is hashed as a whole, frontmatter included.
  - Folder defaults: `library/` protects `## My take`, `projects/` protects `## Why`, `wiki/concepts/` protects `## My position`, `me/preferences.md` protects `## Owner rules`. Headings in `protected:` frontmatter are matched with or without the `##`, ignoring case.
  - Append-only content keeps the hash of each non-blank line. A line that is only an HTML guidance comment (the template's `<!-- append-only ... -->`) is not tracked, so removing that comment is not reported. A changed line is reported as a removed line, as 12.3 says.
  - A missing section or a deleted file is reported as changed; `--accept` on that path clears it.
  - Files without a finding are re-recorded on every run, even when another file has a finding. A file with a finding keeps its old record until `--accept`. Section 12.3 says "on a clean run, update stored hashes"; updating per file is stricter, because a new page is protected from its first run instead of waiting for the whole vault to be clean.
  - `vault.example/.harness/protected.json` is not committed: it is local state, created by the first `lint`.

## D015 · The date of a project's `## Now`

- **Step:** 5. **PRD:** 11.5, 15.4 (`--stale`). **Open question:** Q13.
- **Context.** `--stale` reports "`Now` older than the newest Timeline entry by more than 14 days", but the contract of `## Now` has no date.
- **Decision.** The date of `## Now` is the latest `YYYY-MM-DD` written inside the section, else the page's `updated`. The newest Timeline entry is the latest date that starts a Timeline line.
- **Why.** It needs no new field and no change to the template. Since agents set `updated` on every edit, the check fires mostly when the owner adds Timeline lines by hand without touching `Now`, which is the case it exists for.

## D016 · Lint output never shows details of a confidential page outside the local profile

- **Step:** 5. **PRD:** 13.5, 15.4, 18.4, 19.2 (T1).
- **Decision.** When `ANYBRAIN_PROFILE` is not `local`, any finding about a confidential page is printed as `<path>: details hidden (confidential: local profile only)`. `lint --secrets` never prints the matched value, only its kind and line.
- **Why.** The weekly `lint` skill may run in a frontier session; link targets or quotes inside a confidential page would otherwise reach a remote model through the lint output.

## D017 · Scope of `lint --secrets`

- **Step:** 5. **PRD:** 15.4, 19.4. **Open question:** Q14.
- **Context.** 15.4 lists "API key patterns, IBAN, card numbers, emails and phone numbers outside `company/`, `finance/`, `raw/`". It is unclear whether "outside" applies to API keys too.
- **Decision.** API key patterns are reported in every text file of the vault, `raw/`, `company/` and `finance/` included, because 19.2 (T3) says keys live only in `.harness/.env`. IBANs, card numbers, emails and phone numbers are reported outside `company/`, `finance/` and `raw/` only. Dot folders (`.harness/`, `.obsidian/`, `.git/`) are not scanned: `.harness/.env` holds keys by design, and Obsidian plugins keep their own tokens. IBANs must pass the mod-97 check and card numbers the Luhn check and start with 2 to 6, which keeps ISBNs out; an `isbn:` frontmatter line is skipped. Client names match whole words, case-insensitively, in pages other than mission pages, and in the kebab-case form of `raw/` paths (`Acme Bank` matches `raw/inbox/acme-bank-brief.md`).
- **Why.** A key pasted into a clipped article or a finance note is still a leaked key.

## D018 · Performance measured, and what was optimised

- **Step:** 5. **PRD:** 9.2.
- **Measure.** On a synthetic vault of 2,000 pages (1,000 books, 500 concepts, 500 notes) in this build container: `anybrain index` about 0.8 s, `anybrain index --check` about 0.8 s, `anybrain lint` (all checks) about 2.4 s on a warm run, including 29,000 near-duplicate warnings caused by synthetic slugs (`concept-12`, `concept-123`).
- **Optimisations.** The vault is parsed once per command and shared by every check; `protect` indexes its store by file instead of scanning it for each file; the near-duplicate check reuses one `difflib.SequenceMatcher` per concept; vault-relative paths are computed by string slicing instead of `Path.relative_to`; frontmatter is parsed with PyYAML's C loader when available.
