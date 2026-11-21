# Architecture

This page explains the diagram of PRD section 8: what each block does, why it is built that way, and where the rule comes from. Section numbers refer to [`PRD.md`](../PRD.md); `Dxxx` numbers refer to [`decisions.md`](decisions.md).

```
 CAPTURE                                 VAULT (private, synced by iCloud)
 ─────────────────────                   ──────────────────────────────────────────────────
 Readwise plugin ──────────┐             AGENTS.md            rules for any agent (generated)
 Obsidian Web Clipper ─────┼──────────▶  index.md             root index (generated)
 Manual drop ──────────────┘             raw/                 immutable inputs
                                         library/  wiki/      compiled by the agent
                                         projects/ me/        agent and owner (protected parts)
                                         company/ finance/    confidential, local model only
                                         journal/             briefs and reports
                                         .harness/            config, graph.json, evals, traces
                                                │
            ┌───────────────────────────────────┼────────────────────────────────────┐
            ▼                                   ▼                                    ▼
   SCRIPTS (deterministic)              SKILLS (Agent Skills)               HARNESS (swappable)
   anybrain index · lint · neighbors    ingest · ask · lint · recommend     Claude Code · OpenCode ·
   feedback · migrate · eval · trace    migrate · feedback · draft ·        any AGENTS.md reader
   content                              publish · daily-brief
                                                                                     │
                                                                          MODELS (by sensitivity)
                                                                          frontier ←→ local
```

## The split that shapes everything

**Scripts for facts, the model for judgment.** Anything that can be computed is computed by `anybrain`, in Python with `pyyaml` as the only dependency (9.2): indexes, the link graph, checks, counts, masking, migration checks, eval gates and traces. The model is used where judgment is needed: what a source says, which concept it supports, how two pages relate, how to answer. The split makes the agent cheaper, because it reads a generated index instead of scanning folders, and safer, because a rule held by code does not depend on the model remembering it.

**The repo holds the method, the machine holds the data** (8). Nothing personal is ever committed. `vault.example/` holds fake data, and a test fails on an email or phone pattern outside it.

## Capture

Inputs land in `raw/`, which the agent never edits (10.3): highlights from the Readwise plugin in `raw/highlights/`, web clips from the Obsidian Web Clipper in `raw/articles/`, anything else dropped by hand in `raw/inbox/`. `init` writes the Obsidian settings for new notes and attachments; the migration skill tells the owner which plugin folders to point there (21.2 step 7).

## The vault

| Folder | Who writes | Read by |
| --- | --- | --- |
| `raw/` | owner and capture tools | frontier, except `raw/docs/` |
| `library/`, `wiki/` | agent, except protected sections | frontier |
| `projects/` | agent and owner; missions are confidential | frontier, missions local |
| `me/` | owner; the agent appends approved rules to `preferences.md` | local, except `me/preferences.md` |
| `company/`, `finance/` | owner | local only, always |
| `journal/` | agent | local, except `journal/reports/` |
| `.harness/` | scripts | readable, except keys, name lists, traces and eval runs, which are always local |

Pages follow typed contracts (11): a book has `## My take` (the owner's), key ideas linked to concepts, and verbatim quotes; a concept needs two sources and keeps the owner's `## My position` apart from the agent's `## Draft position`; a project keeps a short `## Now` above an append-only `## Timeline`.

**Ownership is enforced, not hoped for** (12). Protected sections are hashed in `.harness/protected.json`; an edit the owner did not accept with `anybrain lint --accept` is an error. Append-only sections keep a hash per line, so a rewritten line is caught.

## Scripts

| Module | Role | PRD |
| --- | --- | --- |
| `vault.py` | Page loading, frontmatter, slugs, sensitivity, routing from `models.toml` | 10, 11, 18.2 |
| `graph.py` | Link extraction and resolution, `graph.json`, `neighbors`, orphans | 13 |
| `index.py` | Root and domain indexes, `index --check`, `--stats` | 14 |
| `lint.py`, `protect.py` | The checks of 15.4 and 13.6; protected and append-only hashing | 12, 13.6, 15.4 |
| `install.py` | `init`, `update`, `doctor`; renders `AGENTS.md` and the harness permission files | 9.3, 9.5, 18.4 |
| `anonymize.py` | Masks names, emails, phones, IBANs, cards and keys before a frontier model sees a file | 19.3 |
| `feedback.py`, `stats.py` | Correction log and rule proposals; usage counts per week and per skill, session figures, the dashboard | 16, 6, 22.6, 22.7 |
| `migrate.py` | Legacy snapshot, inventory, review dates, check that owner text survived | 21 |
| `evals.py`, `trace.py` | Golden-set runner, graders, judge, gates, baselines; run traces per harness; session import | 17.3, 22 |
| `content.py` | Draft snapshots, edit ratio and changed passages, content checks on one file | 11.10, 11.11, 15.10, 15.11 |
| `search.py` | QMD index configs from the routing, `anybrain search` by profile, link suggestions | 17.2, 17.4, 13.7 |

**Indexes and the graph** (13, 14). `anybrain index` writes one `index.md` per domain, one line per page built from its `description`, and a root index with one line per domain. It also writes `.harness/graph.json`, where each link keeps the sentence that carries it, so the agent can judge a relation without opening the page. `anybrain neighbors <page>` prints the links in and out of a page with those sentences.

**No orphan** (13.3 rule 6, D039). Every page in `library/`, `wiki/` and `projects/` must be linked from another page there. `lint` warns about an orphan for its first 7 days, then reports an error. Each orphan finding lists candidate relations that code found from facts: the same author, the pages it links to, a shared tag that is not generic. Code never writes the link. The agent writes only real relations, or asks the owner.

## Skills

Each skill is a `SKILL.md` folder following the Agent Skills standard (15.1), linked by `init` into every skill directory listed in `models.toml`. Skills never name a model, a harness, a slash command or a tool; they call `anybrain` commands and say "read the file", "write the file". The eight shared rules at the top of each skill (15.2) carry the safety rules into every procedure, so a harness that loads one skill without `AGENTS.md` still follows them.

The skills follow two habits taken from prior work (27). **The worker never grades itself**: the lint skill runs its judgment pass in a fresh context, and the eval judge sees only the question, the expected behaviour and the answer. **Corrections become rules only with approval**: the count is done by code, the rule by the owner.

## Harness

anybrain is not a harness (28). Any program that reads `AGENTS.md` can drive the vault; `CLAUDE.md` is a symlink to it. `init` and `update` render the harness permission files from the routing table: Claude Code's `permissions.deny` in `.claude/settings.json` and OpenCode's `permission` rules and `local` agent in `opencode.json` (18.4, D027, D031). The negation that keeps `me/preferences.md` readable while the rest of `me/` is denied was measured on a live session, not assumed (D027). Codex, Cursor and others were researched; each needs a measured probe before its config ships (Q24).

## Models and routing

`.harness/models.toml` holds two profiles, `frontier` and `local` (an OpenAI-compatible endpoint such as Ollama), and one `[routing]` table that is the only source of truth for what each profile may read (18.2). Some paths are local whatever the table says: `company/`, `finance/`, and the private `.harness` files (keys, name lists, traces, eval runs).

The rule holds at three layers, plus a test (18.4):
1. **Instructions.** `AGENTS.md` and every skill tell the agent to check `ANYBRAIN_PROFILE` before reading a local-only file, and to search only folders it may read.
2. **Harness.** The rendered deny rules stop the harness's own read tools.
3. **Scripts.** `neighbors`, `lint`, `graph.json`, the indexes and `anonymize` mask local-only pages outside the local profile. A lint report is masked according to where it is written (Q22).
4. **Tests.** Safety questions in the golden set ask for confidential content on the frontier profile, and a run that reads a local-only file fails.

## Evals and observability

`anybrain eval` (22) runs each golden question headless on a harness, in a throwaway copy of the vault, so a case that writes never touches the real one. A shell command that names the real vault fails the run (D038). Graders check cited files and required strings exactly. A judge, run in a fresh context with no tools, grades the expected behaviour. The run prints the pass rate per tag, the five gates of 17.3 that decide when search is needed, and the delta against a frozen baseline.

Every case gets a **run trace** (22.5), built from the harness's own machine output, never from the model's report. A trace records files read and written, tool calls, refusals, tokens, cost, and any local-only file read on the frontier profile, which fails the run. Traces stay in `.harness/`, routed local, with no service and no collector (D032).

If the owner edits the vault during a run, the runner waits until the vault has been quiet for 15 minutes and reruns only the cases that saw older content (D038).

**Every session, not only evals** (22.6, D049). `anybrain trace import` reads the transcripts Claude Code already keeps and stores one record per question asked in a session, with no prompt and no answer in it. No hook, no daemon: the weekly `lint` skill runs it. The cost is the harness's own figure; when several questions share one, it is split by tokens and the record says so. Records older than 30 days become weekly totals without paths. `anybrain stats` prints local-only reads on the frontier profile, cost per skill and per week, and the median files read per `ask`.

**The dashboard** (22.7). `anybrain stats --dashboard` writes `journal/dashboard.md`, a generated page with tables and text bars: local-only reads first, then costs, files read, the last eval runs, graph figures, usage and pending rule proposals. It sits in `journal/`, which is local, because it summarises traces. The figures Obsidian can compute itself, books by status or projects past their review date, are Dataview queries in `templates/obsidian/dashboard-vault.md`, which the owner may copy into the vault.

## Migration

`anybrain migrate inventory` copies a legacy vault read-only into `raw/legacy/<date>/` and lists every file with a suggested target (21.1). The `migrate` skill interviews the owner, migrates a 5-book pilot for review, then works in batches. `anybrain migrate check` verifies that every paragraph the owner wrote survives verbatim, that no file is still pending, and that each project's review date is the one code computed: the date already on the legacy note, or 30 days after the migration (D035, D037).

## Content

Drafts turn the vault into writing in the owner's voice (15.10). The `draft` skill builds a piece from the owner's own pages: `My position`, `My take` and decisions first. It puts a source comment after each claim, and a `[YOUR STORY: ...]` placeholder for anything the vault does not hold.

`anybrain content check` blocks `ready` on any of these:
- a banned pattern from `me/voice.md`;
- a placeholder left in;
- a confidential, mission, `company/` or `finance/` source;
- a client name;
- a missing source or channel guide.

When the owner publishes, the `publish` skill keeps the final text verbatim. `anybrain content diff` measures the share the owner changed against the agent's first version, saved by `content snapshot`. The owner's edits are logged as corrections, which become rules only once the owner approves them (16.5).

`anybrain stats` follows the edit ratio. The target is at most 30% by the 8th published piece, and lower over time (D044, D045).

## Testing

Unit tests cover every module, with one passing and one failing fixture per lint check (23). CI runs the tests on Python 3.11 and 3.12, lints `vault.example/`, checks that its generated indexes and `graph.json` are byte-identical to the committed ones, and fails on an em dash or on personal data patterns. Skills are checked by real runs, recorded with date, harness and cost in `decisions.md`.

## Search

Search is off until the owner turns it on in `.harness/models.toml` (17.2, D055). A QMD result carries a snippet of the file it found, so anybrain keeps two QMD indexes per vault, outside the vault, both built from the routing table. The frontier index holds only what frontier models may read; the local index holds everything. `anybrain search` picks the index of the session's profile and hides anything that profile may not read. A search uses meaning when QMD's models are on disk, and keywords otherwise. The weekly `lint` skill refreshes both indexes and lists similar pages that are not linked, for the owner to approve.
