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

## D019 · What `init` creates in P0

- **Step:** 6. **PRD:** 7, 9.3, 10.2, 24 steps 6 and 15.
- **Decision.**
  - Folders: the layout of 10.2 without `content/` and `.harness/drafts/`, which step 15 adds with P1b, and without `.harness/baseline/`, which `eval` creates in step 10.
  - Framework files (re-rendered by `update`): `AGENTS.md`, the `CLAUDE.md -> AGENTS.md` symlink, `.harness/templates/*.md`, `.claude/settings.json`, `opencode.json`, the skill links.
  - Owner files (created once, never re-rendered): `.harness/models.toml`, `.harness/.env` (created with mode 600), `.harness/clients.txt` and `.harness/names.txt` (a comment line each), `log.md` (one `init` line), `.gitignore` (`.harness/.env`, `.harness/baseline/`), and four starter pages in `me/`: `profile.md`, `aspirations.md`, `principles.md`, `preferences.md` (with empty `## Owner rules` and `## Learned rules`). `me/cv.md` and `me/voice.md` belong to P1b.
  - `.obsidian/app.json`: only the missing keys are added (`newFileLocation: folder`, `newFileFolderPath: raw/inbox`, `attachmentFolderPath: raw/attachments`).
  - The fake pages of `vault.example/` are not copied: "created from `vault.example/` structure" is read as folders, templates and config, and the example's content would end up in the owner's indexes.
  - The `.gitignore` comes from the scaffold's `install.sh`. The vault is not a git repository, but the nightly backup of 19.6 copies it into one with `git add -A`; without this file, the API keys of `.harness/.env` would be committed there.
- **Why.** Section 7 wants the structure complete on day one, and section 24 puts `content/` in step 15. The owner's first `init` happens in step 14, so P1b only adds folders and files later, and nothing is restructured.

## D020 · Harness configuration, checked against the official docs on 2026-11-16 (Q6)

- **Step:** 6. **PRD:** 18.2, 18.3, 18.4, 26 Q6.
- **How.** Three research agents read the official documentation, and three other agents re-checked each claim against the cited source. Some sites (opencode.ai, agentskills.io, agents.md) could not be reached from the build container; for those, the agents read the source files the sites are built from, in the official GitHub repositories, at their latest commit of 2026-11-16.
- **Claude Code** (code.claude.com/docs):
  - Headless: `claude -p "<prompt>"`; `--output-format json` puts the answer in `result`. Since v2.1.302, `-p` runs start in manual permission mode, so tools that need approval are denied unless allowed (`--allowedTools`, `--permission-mode` or allow rules). To handle in step 10.
  - Project settings: `<vault>/.claude/settings.json`, `{"permissions": {"deny": [...]}}`. Deny always wins over allow; an allow rule cannot carve an exception out of a deny.
  - `Read(./company/**)` is a valid rule (the docs use `Read(./secrets/**)`); `./` is relative to the directory the session starts in, which is the vault root. A `Read` deny also blocks edits, Grep and Glob on the path, and the Bash commands Claude Code recognises (`cat`, `head`...), but **not** a script that opens files itself, such as `anybrain lint`. Hence D012 and D016, and the PRD's "gate, not a sandbox".
  - `env` in `settings.json` sets variables for the session and its Bash commands: the template sets `ANYBRAIN_PROFILE=frontier`. A value there overrides the shell's, so a Claude Code session in the vault is always the frontier profile.
  - Skills: `~/.claude/skills/<name>/SKILL.md`; a skill folder may be a symlink.
  - Claude Code reads `AGENTS.md` natively only from v2.1.277 and only when no `CLAUDE.md` exists; a `CLAUDE.md -> AGENTS.md` symlink is the documented pattern for every version.
- **OpenCode** (opencode.ai/docs, version 2.3.0 of 2026-11-13, "V2"):
  - Headless: `opencode run "<prompt>"`, with `--agent`, `--model provider/model` and `--format json`. `--standalone` starts a private server that inherits the caller's environment.
  - Config: `<vault>/opencode.json` with `"$schema": "https://opencode.ai/config.json"`. Native V2 keys are plural: `permissions` is an ordered list of `{action, resource, effect}` where the last match wins; `agents.<id>` takes `description`, `mode`, `model`, `permissions`. Agent rules are appended after the global ones, so the `local` agent's `allow` rules override the global `deny` rules. The V1 shapes (`permission`, `agent`) are still accepted; the template uses V2.
  - Ollama is detected at `127.0.0.1:11434` without a provider block; the model is `ollama/<id>`.
  - Skills are read from `~/.config/opencode/skills`, and also from `~/.claude/skills` and `~/.agents/skills`.
  - `AGENTS.md` is read natively; `CLAUDE.md` is not read in V2.
  - **The config cannot set environment variables** (no `env` key, globally or per agent). `ANYBRAIN_PROFILE=local` has to be set when OpenCode starts: `ANYBRAIN_PROFILE=local opencode` for a session, `ANYBRAIN_PROFILE=local opencode run --standalone --agent local "<prompt>"` for headless runs. See Q16.
- **Agent Skills** (agentskills.io specification): `name` (1 to 64 characters, lowercase letters, digits and hyphens, equal to the folder name) and `description` (1 to 1,024 characters) are required. `~/.agents/skills` is a documented cross-client convention, read by OpenCode, Codex, Gemini CLI and Goose. The default `[skills] dirs` of 18.2 (`~/.agents/skills`, `~/.claude/skills`) therefore covers both target harnesses.
- **Obsidian:** the help documents the settings but not the JSON keys. `attachmentFolderPath` is confirmed by Obsidian's own importer code; `newFileLocation` and `newFileFolderPath` come from community typings. Treated as reliable but not first-party.

## D021 · How `update` decides, file by file

- **Step:** 6. **PRD:** 9.3.
- **Decision.** For each framework file: missing, it is written; identical to the new render, nothing happens; equal to the hash of the last render (the owner did not touch it), it is replaced and reported as `updated`; otherwise the owner modified it, so `update` prints a unified diff and asks. With `--yes`, or when there is no terminal to ask on, the owner's version is kept. A file present before `init` that differs from the render is treated as the owner's. A skill link that points somewhere else is replaced only on a "yes"; a real folder in its place is never touched. Content folders, `log.md` and the owner files of D019 are never written by `update`. The values of `--owner`, `--role` and `--lang` are kept in `.harness/render.json`, so `update` renders `AGENTS.md` the same way.
- **Why.** 9.3 says `--yes` keeps owner versions; without a terminal the same answer is the conservative one.

## D022 · What `anonymize` masks, and how

- **Step:** 7. **PRD:** 19.3.
- **Decision.** Masks are applied in this order, each replaced by a placeholder: emails (`<email>`), IBANs (`<iban>`), card numbers (`<card>`), phone numbers (`<phone>`), amounts with a currency (`<amount>`), then client names (`<client>`) and names (`<name>`) from `.harness/clients.txt` and `.harness/names.txt`, whole words, case-insensitive, longest first. The vault is `ANYBRAIN_VAULT` when set, else the nearest parent folder with a `.harness/` folder. Patterns are shared with `lint --secrets` (D017), with one difference: `anonymize` masks any IBAN-shaped string without the checksum test, because masking too much is the safe side for text sent to a frontier model. For the same reason, a ten-digit number starting with 0 (for example an ISBN-10 that starts with 0) is masked as a French phone number. Dates such as `2026-01-10` are never masked (regression test of 19.3). Amounts are recognised with a currency sign or code before or after the number (`12 500 €`, `$3,000`, `40 k€`, `15 EUR`).
- **Why.** The scaffold masked emails, phones, IBANs, amounts and `names.txt`; 19.3 adds card numbers and `clients.txt`. `names.txt` is now read line by line, so a full name such as "Jane Doe" is one name, where the scaffold split it into words.

## D023 · Feedback details

- **Step:** 7. **PRD:** 16.
- **Decision.**
  - The 90-day window includes the day 90 days before today. Corrections dated in the future are ignored.
  - A rule is any line of `me/preferences.md` that carries `topic:<slug>` (exact slug, so `topic:no-em-dash` does not match `topic:no-em-dash-ever`). A rule stops proposals for its topic, wherever it sits in the file.
  - "Rule not followed" needs the rule's `learned YYYY-MM-DD` date: corrections dated after it, within 90 days, are reported, with a suggestion to reword the rule when two of them fall within 30 days. A rule written by the owner without a date stops proposals but is never reported as not followed, because its evidence cannot be told apart from a violation.
  - `feedback add` checks that the topic and the skill are kebab-case and joins a multi-line note into one line. `feedback topics` lists all-time counts and marks topics that already have a rule.
  - A malformed log line is skipped and reported as a warning by `lint --feedback`.
  - `vault.example/.harness/feedback.log` holds the three corrections behind its one learned rule, all dated before the rule, so the example shows the loop and stays clean.

## D024 · What `stats` prints in P0

- **Step:** 7. **PRD:** 6, 9.3, 16.5.
- **Decision.** One row per ISO week (the current week last, `--weeks 4` by default) with a count per skill found in `log.md`, plus `ingest` and `ask` always shown, and a total. A week that reaches the target of section 6 (at least 2 `ingest` and 3 `ask`) is marked `target met`. When the last 4 weeks all have fewer than 2 ingests, the kill criterion of section 6 is printed as a reminder. Malformed log lines are counted and skipped. Edit ratios per published piece (16.5) and owner metrics per channel (15.11) are P1b and come with `content.py` in step 15.
- **Why.** Section 6 names `anybrain stats` as the measure of the usage metric and of the kill criterion.

## D025 · Fixes from the adversarial review of steps 2 to 4, and the "local-only" rule

- **Step:** after 7. **PRD:** 10.3, 13.1, 13.2, 13.5, 14.2, 18.4, 19.4. **Open question:** Q18.
- **How.** Three reviewers read `vault.py`, `graph.py` and `index.py` against the PRD (conformance, correctness, privacy); each finding was handed to an independent verifier asked to refute it by reproducing it. 14 findings were confirmed, 3 rejected.
- **Fixed.**
  - A confidential page outside `company/` and `finance/` (a `kind: mission` project, a page that declares `sensitivity: confidential`) printed its full description in `projects/index.md` or `library/index.md`. It now shows its title and `(confidential: local profile only)` only, like `neighbors`.
  - `[[A/B testing]]` and other targets with a `/` now fall back to aliases and titles when no path matches (13.2).
  - `[[slug\|text]]`, the form Obsidian writes inside a table, resolves.
  - Markdown links with spaces (`[x](<my file.md>)`) or parentheses (`[x](notes(1).md)`) are parsed.
  - A single string in `aliases`, `sources` or `protected` is read as one item, not letter by letter.
  - A UTF-8 byte order mark at the start of a file no longer hides its frontmatter.
  - Several authors given as a YAML list are joined with commas in the index line.
  - Unknown statuses and pages without a status go under `## other`, as D013 says.
  - `neighbors --depth 2` no longer lists a masked page twice.
  - A page whose frontmatter cannot be read is treated as confidential until it is fixed (`lint --schema` reports it), because it may be a mission or declare `sensitivity: confidential`.
- **Rejected by the verifiers, kept as conservative choices.**
  - **Local-only pages.** The verifiers found that `graph.json` and `neighbors` showed text from `me/` and `journal/` pages, and judged it not a PRD defect, since 13.5 only masks confidential pages. It is kept fixed: the harness configs of 18.4 deny reading `me/` and `journal/` in frontier sessions, so a script must not show their content there either. Pages in `company/`, `finance/`, `me/`, `journal/`, and every confidential page, are "local-only": outside `ANYBRAIN_PROFILE=local`, `graph.json`, `neighbors` and `lint` show their title and path, never their description, link context or findings. Their own domain indexes sit inside those denied folders and stay complete.
  - **Guessing hidden names.** Outside the local profile, `neighbors` finds a local-only page by its exact path only, not by title or alias, and its refusal message gives the path, not the title. Otherwise a session could confirm a client name by guessing it.
  - The third rejected finding (a `sensitivity: Confidential` with a capital letter is ignored) stays as it is: the value is invalid and `lint --schema` reports it.

## D026 · Fixes from the adversarial review of steps 5 to 7

- **Step:** after 7. **PRD:** 9.2, 9.3, 12.3, 13.3, 15.4, 16.1, 19.2, 19.3. **Open question:** Q19.
- **How.** Four reviewers (PRD conformance, correctness, privacy, owner data) read `lint.py`, `protect.py`, `install.py`, `feedback.py`, `anonymize.py` and `stats.py`; each finding went to an independent verifier asked to refute it by reproducing it. 17 findings were confirmed (some found by two lenses), 7 rejected.
- **Owner data.**
  - `index` never overwrites an `index.md` that does not start with its generated header: it writes nothing, names the file and asks the owner to rename it. `init`, which builds the indexes last, therefore stops instead of replacing a hand-written home note (9.3 "never overwrites").
  - `init` and `update` refuse to run while an iCloud placeholder (`*.icloud`) exists: an evicted file looks missing, and writing a new one would create an iCloud conflict with the owner's version.
  - `feedback add` appends its line in one write and never rewrites the log, so two sessions adding at once cannot erase each other's corrections.
  - The protected store is keyed by file (`{"files": {path: {...}}}`, version 2). A `#` in a filename no longer splits a key, which had let an agent edit be re-recorded silently. Version 1 stores are converted on read.
  - `lint --accept` makes the path canonical (`library/../library//x.md` is `library/x.md`), refuses a path outside the vault, and refuses a file that neither exists nor has a record.
  - `lint --report` refuses to overwrite an existing file that is not a `type: report` page (kept from a rejected finding: the verifier judged overwriting a named output normal CLI behaviour, but a report pointed at `me/profile.md` would destroy owner text).
- **Correctness.**
  - The report lists findings inside fenced code blocks, so the links it quotes are not links of the report; a second run gives the same findings, and old reports never keep the vault from linting clean.
  - `lint --graph` checks every resolved link of a non-confidential page, files included: a link to `finance/holdings.csv` or to a `company/` page without frontmatter is a confidential leak (13.3 rule 5).
  - A scalar `tags` (`tags: 2026`) is a schema finding, never a crash; any unexpected value type in a page's frontmatter becomes a finding on that page.
- **Privacy.**
  - `anonymize` refuses, outside the local profile, a file in `company/`, `finance/`, `me/`, `journal/`, `raw/docs/` or `.harness/`, and a confidential page (mission, declared confidential, unreadable frontmatter). The harness deny rules do not cover a script that reads files itself (D020), so the command would otherwise print a confidential page, only lightly masked, into a frontier session. When no vault can be found, it judges by the folder names on the path and by the file's frontmatter.
  - `anonymize` sets dates aside before masking and restores them, so a date next to an amount stays intact. Amounts accept thousands in groups of three only (no more swallowing the day of a date, no more quadratic time on long lines of numbers), codes before the number (`EUR 1 500`), scales (`2,5 M€`, `3 Md€`, `15 kEUR`) and words (`800 euros`, `2 millions d'euros`). Phones match at the end of a sentence (`06 12 34 56 78.`), with a `00` country prefix, and with non-breaking spaces. IBANs match in any letter case, with hyphens or any grouping, when they pass mod-97 (the upper-case shape is still masked without it). Card numbers accept dots. Emails accept accented letters. API keys are masked as `<secret>`. Names and clients also match their kebab-case, snake_case, joined and line-wrapped forms.
  - `lint --quotes` does not compare a quote with a local-only source outside the local profile; it warns that the check needs the local profile. Otherwise a session could confirm a guess about a confidential file by trying values.
  - Masking of lint findings is decided from the path as well as from the graph, so a protected-section finding about a moved confidential file stays masked. Known limit: `.harness/protected.json` stores the headings of protected sections in clear; for the default headings (`My take`, `Why`...) this reveals nothing, but a custom `protected:` heading in a confidential page is visible to a session that reads `.harness/`.
- **Rejected and left as is.** `feedback add` run twice logs two corrections (each call is one correction); a correction logged on the day a rule was learned is evidence, not a violation (D023); `stats` counts the weeks before the vault existed when it checks the kill criterion; `update` after `render.json` is deleted renders with the default owner name until `init --owner` is not enough to change it (it only fills missing values); `init` keeps an existing harness config without deny rules (9.3: never overwrite; D021); a report written on the local profile is not masked; `update` writes through an `AGENTS.md` that the owner turned into a symlink.

## D027 · Routing decides what frontier sessions may read; `me/preferences.md` is open (Q15)

- **Step:** after 7. **PRD:** 10.3, 15.2 rule 3, 16.3, 18.2, 18.4, 26 Q15. **Decided by the owner on 2026-11-17.**
- **Context.** 18.4 denied `me/**` to frontier sessions, while 16.3 and the walkthrough of 8.1 have every agent read `me/preferences.md`. With the deny, approved rules never reached frontier sessions and the correction loop of section 16 could not work. The owner may open more files later.
- **Decision.**
  - `[routing]` in `.harness/models.toml` is the single source of truth. A key ending with `/` is a folder, any other key is a file; a file entry wins over its folder, and among folders the longest wins. The default adds `"me/preferences.md" = "frontier"` to the table of 18.2.
  - `company/` and `finance/` are always local: a `frontier` entry for them is ignored, and `init`, `update` and `doctor` say so (G6). Pages with `sensitivity: confidential` or `kind: mission` stay local-only whatever the routing says.
  - The harness configs are rendered from the routing by `init` and `update`: every folder routed local is denied, and every path routed frontier inside it stays readable. `raw/docs/`, routed local since 18.2 but missing from the deny list of 18.4, is now denied too. `doctor` fails when a config no longer matches the routing (for example after a routing line was added without `anybrain update`).
  - The scripts follow the same routing: `graph.json`, `neighbors`, `lint` and `anonymize` hide what is routed local, and show `me/preferences.md`. A page routed local inside a frontier domain shows its title only in that domain's index.
  - `me/preferences.md` says in a comment at its top that every model reads it, so the owner writes rules there, never private facts.
  - To open another file: add one line, such as `"me/voice.md" = "frontier"`, then run `anybrain update`.
- **OpenCode** (verified in D020): global `read` deny rules for the local folders, then `allow` rules for the exceptions; the last matching rule wins. The `local` agent allows every local folder.
- **Claude Code: measured, not only read.** An allow rule never wins over a deny (D020), so an exception is a gitignore negation inside the deny list. Probes with Claude Code 2.1.326 on 2026-11-17, in a vault built by `anybrain init`, each asking a headless session to read the files:

  | Deny rules | `me/preferences.md` | `me/profile.md` | file added later in `me/` | `me/sub/x.md` |
  | --- | --- | --- | --- | --- |
  | `Read(./me/*)`, `Read(!./me/preferences.md)` | denied | denied | | |
  | `Read(!./me/preferences.md)`, `Read(./me/*)` | denied | denied | | |
  | explicit list of the other files of `me/` | read | denied | **read** (not listed) | |
  | `Read(me/*)`, `Read(!me/preferences.md)` | **read** | denied | denied | denied |
  | same, plus `Read(!me/voice/**)` | read | denied | denied | denied; `me/voice/x.md` read |

  So a folder with exceptions is rendered as `Read(me/*)` plus one `Read(!<exception>)` per exception, without the `./` prefix (the prefixed negation does not reopen anything). A folder without exceptions keeps `Read(./folder/**)`, also measured as denied. Known side effect: per the docs, a rule without `./` and with one folder segment may also match a folder of that name deeper in the vault (`library/me/...`), which only denies more. The first version of this entry shipped the prefixed form, which denied `me/preferences.md` too; it was replaced the same day.
- **Instruction layer.** In the last probe, the session read `me/preferences.md` and did not even try `me/profile.md` or `company/admin.md`: rule 3 of `AGENTS.md` stopped it before the harness had to. The deny rules are the second layer, as 18.4 intends.

## D029 · Step 8: skills rewritten and run once in Claude Code

- **Step:** 8. **PRD:** 15, 16, 24 step 8.
- **Skills.** `ingest`, `ask`, `lint`, `recommend` were rewritten to sections 15.3 to 15.6; `feedback` is new (sections 15.9 and 16); `daily-brief` was aligned with 15.7 and the shared rules (shipped in P0, enabled in P4). Each repeats the eight shared rules of 15.2 at the top, with rule 3 pointing at the routing of `models.toml` (D027). `tests/test_skills.py` checks the Agent Skills frontmatter (`name` equal to the folder, `description` of 1 to 200 characters that says when to use it), the absence of any model, provider, harness or tool name, the shared rules, and that every `anybrain` command named in a skill exists. That test caught the `lint` skill's description, whose unquoted `: ` had made its frontmatter invalid YAML since step 1.
- **How the runs were made.** On 2026-11-17, Claude Code 2.1.326 in headless mode (`claude -p ... --output-format stream-json --verbose --permission-mode acceptEdits --max-budget-usd 1.50`, tools allowed: file read, edit, write, search and `Bash(anybrain *)`), from the root of a copy of `vault.example/` set up by `anybrain init`, so with the real `AGENTS.md`, harness configs (frontier profile) and the skills linked into the project's `.claude/skills/`. A fake highlights file for a book not yet in the library, `raw/highlights/obviously-awesome.md`, was added to `vault.example/` for the `ingest` acceptance of 15.3.

  | Skill | Prompt | Result | Cost |
  | --- | --- | --- | --- |
  | ingest | "ingest raw/highlights/obviously-awesome.md. The book is finished." | **Pass** (15.3 acceptance): `library/obviously-awesome.md` created; `lint --schema --quotes --graph` clean; 5 verbatim quotes; links with reasons to `category-design` and `languaging`; one line added to the concept's `sources`, `What the sources say` and the top of its `Timeline`; index updated; `My take` left empty; `log.md` line | 0.38 USD |
  | ask | "What do I believe about category design, and where do my sources disagree?" | Pass: owner's position first, a path after each claim, two real disagreements found (Cole and Dunford on when to create a category and how to name it), offer to file the synthesis, `files opened: 8` | 0.29 USD |
  | recommend | "What should I read next?" | Pass: 5 titles, none in the library, each tied to a rated book with a risk. Deviation: some titles are tied to the book rated 4, while 15.6 asks for a book rated 5 | 0.21 USD |
  | feedback | owner correction about the word "leverage" | Pass: `anybrain feedback . add --topic no-leverage ...` logged; no page used the word; no rule written before approval | 0.20 USD |
  | lint (first run) | "lint the vault" | Checks and a judgment pass in a **sub-agent with a fresh context** (principle 8). It found a real inconsistency in `vault.example` (a decision dated before the note it cited, now fixed). **Problem:** it could not write `journal/lint-*.md`, because `journal/` is local-only and a read deny also blocks writes in Claude Code, so it wrote the report outside the vault (Q22) | 0.69 USD |
  | lint (after the fix) | "lint the vault" | Pass: report given in the answer, nothing written outside the vault, with the command to file it from a local session | 0.51 USD |
  | daily-brief | not run | P4: its preconditions (local profile, read-only email and calendar tools) are not met in P0 | |

  Probes of the deny rules for D027 cost 1.01 USD; all runs together cost 3.29 USD.
- **Changes made from the runs.** `ingest` now keeps a concept's `Summary` and `Draft position` true when it gains a source (both concept pages still said "both sources" after a third one). `AGENTS.md` says that every section not protected, `Summary` included, is the agent's to rewrite (`ask` had treated `Summary` as the owner's). `lint` gives its report in its answer outside the local profile.
- **Friction seen in every run.** Shared rule 3 asks the agent to check `ANYBRAIN_PROFILE`, but reading an environment variable needs a shell command that the harness asks the owner to approve; in headless runs the agent could not check it and assumed a non-local profile, which is the safe side (Q23).
- **Not done.** Step 9 (the same skills in OpenCode with the local profile) needs OpenCode and a local model, which this build container does not have.

## D030 · How other coding agents deny file reads (research for Q24)

- **Step:** after 8. **PRD:** 3.1 (G5), 18.4, 26 Q24. **Date:** 2026-11-17.
- **How.** One research agent per harness family read the official docs or, when a docs host could not be reached from the build container, the vendor's official source repository or shipped code; an independent agent then tried to refute each claim. 82 claims were checked, 9 corrected. Claude Code facts were also measured in live sessions (D027).
- **Findings.**

  | Harness | Read deny by path | Exception inside a denied folder | Shell commands and scripts | Notes |
  | --- | --- | --- | --- | --- |
  | Claude Code | `permissions.deny` in `.claude/settings.json` | Yes: `Read(me/*)` then `Read(!me/preferences.md)`, the negation bare and after its rule, in the same file | Not covered: a command naming a denied file is refused, but `grep -r` over the folder and scripts read it (measured live). The optional sandbox covers the shell | Search tools may hide an exception from results even though it can be read by path |
  | OpenCode | ordered `read` rules in `opencode.json` | Yes: the last matching rule wins | Not covered | The config cannot set `ANYBRAIN_PROFILE` (D020) |
  | Codex | permission profiles (beta) in `.codex/config.toml` | Only with exact-path denies, not globs; on Linux the sandbox may not reopen a read-only file under a denied folder | **Covered** at OS level (Seatbelt, bubblewrap), the strongest of all | Project config loads only when the folder is marked trusted; `":workspace_roots"` entries apply to the launch folder, so Codex must start at the vault root |
  | Cursor | `.cursorignore` (gitignore syntax) | Yes: `me/*` then `!me/preferences.md`; `.cursor/cli.json` denies cannot have exceptions | **Not a security boundary** per its docs; its sandbox may apply the ignore list, unverified | Reads `AGENTS.md` |
  | Gemini CLI | `.geminiignore` | Yes: `me/**` then `!me/preferences.md` | Not covered; `grep_search` also leaks when ripgrep is missing | Reads `GEMINI.md` by default; a user setting can turn `.geminiignore` off unless the project pins it; project policies are disabled |
  | Goose | none since 2026 (`.gooseignore` removed) | No | Not covered | Only a project plugin hook could guard paths |
  | Windsurf, now Devin Desktop | `.devinignore`, and Claude-style `Read(...)` denies in `.devin/config.json` | gitignore negation | Only with its sandbox on | From search snippets only: its docs host could not be reached |
  | GitHub Copilot | content exclusion in organisation or enterprise settings, not a file in the vault | No allow rules | Not documented | Needs a Copilot Business or Enterprise plan |

- **Decision.** Keep Claude Code and OpenCode as the targets of G5 for now. A harness is added only with a measured probe like D027, because three of the eight mechanisms above behave differently from what their own docs suggest.

## D031 · No command pre-approved in the harness configs (Q23)

- **Decided by the owner on 2026-11-17.** The rendered harness configs pre-approve nothing. The owner approves `anybrain` commands and the profile check as they come, and the question is revisited after real use. When it is, `anybrain lint --accept` stays excluded: an agent that could run it unattended could accept its own edit of a protected section.

## D032 · Lint reports, trace source and trace retention (Q20, Q21, Q22)

- **Decided by the owner on 2026-11-17.**
- **Q22, lint reports.** Each session writes its report where its profile can read it. A frontier session writes `journal/reports/lint-YYYY-MM-DD.md`; the default routing gains `"journal/reports/" = "frontier"`, an exception inside the local `journal/` rendered like `me/preferences.md` (D027). A local session keeps `journal/lint-YYYY-MM-DD.md`. `anybrain lint --report` masks the details of local-only findings according to the **destination** of the report, whatever the profile, so a local session that writes into `journal/reports/` cannot put local-only details there. The report of a frontier session is safe by construction: that session only read what the frontier may read, plus the masked lint output. The agent-written sections of a report written on the local profile into `journal/reports/` rely on the skill's instruction not to quote local-only content; that is why the local profile writes to `journal/` instead.
- **Q20, trace source (P2).** `anybrain trace import` reads the transcripts the harnesses already keep, on demand or from the weekly `lint` skill. No daemon, no collector, nothing sent out.
- **Q21, trace retention.** Raw session traces: 30 days rolling, deleted by `trace import` or the weekly `lint`. Traces of an eval run: kept with their run file in `.harness/baseline/`, because a frozen baseline is the reference for a model change months later. Weekly aggregates without paths: kept, so trends longer than a month stay visible. `.harness/traces/` is routed local.
