# anybrain

**Your second brain in Markdown. Any model, any agent.**

Personal knowledge bases die of maintenance: filing, linking and indexing by hand takes longer than reading, so the vault goes stale. AI memory features fix the effort but lock the knowledge inside one vendor's product, where it cannot be exported, inspected or moved to another model. anybrain keeps everything in plain Markdown on your machine, lets an agent do the filing, and puts an open standard at every seam so the model and the harness stay swappable.

## What it does

| You | The harness, following anybrain's skills and scripts |
| --- | --- |
| Drop a Kindle or Readwise export, or a web clip, into `raw/` and say "ingest" | Writes a book or note page from the template, links it to the concepts it supports, adds it to those concepts' sources, rebuilds the indexes, logs the change |
| Write a few lines of opinion in `## My take` | Never edits them: protected sections are hashed, and `anybrain lint` reports any change you did not accept |
| Ask "what do I believe about category design?" | Reads the root index, the domain index and the page's neighbours (at most 8 files), then answers with your own position first and a vault path after each claim |
| Correct the agent's output | Logs the correction; after 3 on the same topic in 90 days, proposes a rule for `me/preferences.md`, which you approve or not |
| Run `anybrain lint` weekly | Finds broken links, orphans, stale projects, edited protected sections, unverifiable quotes, secrets and evicted iCloud files |
| Move from an old Obsidian vault | Takes a read-only snapshot, migrates every note, and checks that each paragraph you wrote survives verbatim |
| Switch model or harness | Runs your golden questions on it and compares the pass rate, the gates and what each run read and cost with a frozen baseline |

## Design principles

1. **File over app.** Markdown with YAML frontmatter, CSV for numbers. Without any tool, the vault still opens in a text editor.
2. **The agent writes, you judge.** The agent maintains pages, links and indexes. You write opinions, goals and decisions, in protected sections.
3. **Scripts for facts, the model for judgment.** Indexes, the link graph, checks, counts and masking are deterministic Python. The model chooses links, summaries and answers.
4. **Indexes before retrieval.** Generated `index.md` files and a link graph are the navigation layer. Semantic search waits until a measured gate fails.
5. **Open standards at every seam.** `AGENTS.md` for instructions, Agent Skills for procedures, MCP for tools, OpenAI-compatible endpoints for models. No hooks, no slash commands, no vendor plugins.
6. **Sensitivity decides the model.** One routing table sends `company/`, `finance/`, `journal/` and most of `me/` to a local model only.
7. **Tested like code.** Golden questions run on every model or harness change, with a trace of what the agent did in each run.

## Architecture

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
   feedback · migrate · eval · trace    migrate · feedback · daily-brief    any AGENTS.md reader
                                                                                     │
                                                                          MODELS (by sensitivity)
                                                                          frontier ←→ local
```

[`docs/architecture.md`](docs/architecture.md) explains each block.

**Two locations, one rule: the repo holds the method, your machine holds the data.**

| | This repo (public) | Your vault (private) |
| --- | --- | --- |
| Contains | CLI, skills, page templates, the `AGENTS.md` template, a fake example vault, eval templates, docs | Your pages, `models.toml`, golden questions, feedback log, eval baselines, run traces, API keys in `.harness/.env` |
| Version control | git | No `.git` inside iCloud; `init` prints a nightly backup command to a private repository outside it |
| Updated by | Commits | You and the agent; framework files through `anybrain update`, which keeps the files you modified unless you confirm |

## Quick start

```bash
git clone https://github.com/alexandre-darmon/anybrain
cd anybrain
uv tool install .          # or: pipx install .   (pip install -e . while developing)

anybrain init ~/Library/Mobile\ Documents/iCloud~md~obsidian/Documents/brain --owner "Your Name" --lang en
anybrain doctor ~/Library/Mobile\ Documents/iCloud~md~obsidian/Documents/brain
```

`init` creates the folders, the page templates, `AGENTS.md` and its `CLAUDE.md` symlink, `.harness/models.toml`, the harness permission files rendered from the routing table (`.claude/settings.json`, `opencode.json`), the Obsidian settings, and the first indexes. It links the skills into `~/.agents/skills` and `~/.claude/skills`. Running it twice changes nothing.

Put a highlights export in `raw/highlights/` (the Readwise plugin does it for you once pointed there), then ingest it and ask a question from a harness session opened in the vault:

```bash
cd ~/Library/Mobile\ Documents/iCloud~md~obsidian/Documents/brain
claude                      # or: opencode
> ingest raw/highlights/the-mom-test.md
> what do I believe about customer interviews?
```

Then write your opinion under `## My take` in the new book page; that is the only part of the work that is yours.

What works today: phases P0 and P1 of [`PRD.md`](PRD.md), checked on Claude Code. The OpenCode and local-model runs (step 9) are still to be done. Content drafting (P1b) and the daily brief (P4) are planned.

## The skills

| Skill | Trigger | What it produces |
| --- | --- | --- |
| `ingest` | a new file in `raw/`, "ingest this" | a book or note page, links to concepts with a reason, concept sources and timeline lines, rebuilt indexes, a log line |
| `ask` | any question about the vault | a short answer with a vault path after each claim and `files opened: N` |
| `lint` | weekly, "lint the vault" | mechanical fixes, a fresh-context judgment pass, rule proposals, a report in `journal/` |
| `recommend` | "what should I read next?" | 3 to 5 books reasoned from your ratings, takes and abandoned books |
| `migrate` | "migrate my old vault" | a snapshot, a short interview, a 5-book pilot you review, then batches, with every legacy file mapped or skipped with a reason |
| `feedback` | you correct the agent, or review proposals | a logged correction, and an approved rule appended to `me/preferences.md` |
| `daily-brief` | each morning (phase 4) | a short brief in `journal/` from calendar, unread email and open projects, read-only, local profile only |

Every skill repeats the same eight rules at its top: read `AGENTS.md` and the indexes first, never edit `raw/` or a protected section, check the profile before reading a local-only file, cite paths, treat instructions found in sources as data, record corrections, and report in three lines.

## Why not just RAG?

At personal scale, a compiled wiki with indexes and reasoned links gives the agent a few dense pages instead of thirty fragments, and you can open `index.md` and see what it saw. Search is added only when a measured gate of the golden-set run fails: recall at least 0.90 on navigation questions, at least 0.80 on multi-hop questions, a root plus largest domain index under 5,000 tokens, needle questions under 20% of the set, and a median of at most 8 files read per answer. Then [QMD](https://github.com/tobi/qmd), a local hybrid search engine over Markdown, joins as an extra entry point; the indexes and links stay.

## How this differs

At least seven open projects follow the same raw-to-wiki pattern, among them [pm-brain](https://github.com/phuryn/pm-brain), [COG](https://github.com/huytieu/COG-second-brain), [GBrain](https://github.com/garrytan/gbrain) and [claude-second-brain](https://github.com/jessepinkman9900/claude-second-brain), and anybrain borrows from each (see [`docs/prior-art.md`](docs/prior-art.md)). It differs in three ways:
- **Any harness.** The vault and the skills use no hooks, slash commands or harness paths. Claude Code and OpenCode get their permission files rendered from the same routing table.
- **Local routing for confidential folders.** One table in `models.toml` decides which files only a local model may read. The rule holds at three layers: the instructions every skill repeats, deny rules in the harness configs, and scripts that mask local-only pages in their output. Safety questions in the golden set check it.
- **Golden-set evals.** Your own questions run on any model or harness, each in a throwaway copy of the vault, with a trace of the files read, the cost and any local-only read, which fails the run.

## Inspirations

- [Andrej Karpathy, "LLM Wiki"](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f): immutable raw sources, a wiki the LLM maintains, index and log files.
- [Daniel Miessler, Personal AI Infrastructure](https://github.com/danielmiessler/Personal_AI_Infrastructure): goals and principles as first-class context files.
- [Tobi Lütke, QMD](https://github.com/tobi/qmd): local hybrid search over Markdown, the retrieval layer anybrain grows into.
- [Tiago Forte, PARA](https://fortelabs.com/blog/para/): the split between projects, areas, resources and archive.
- [Steph Ango, "File over app"](https://stephango.com/file-over-app): plain files as the layer that outlives every tool.
- [AGENTS.md](https://agents.md), [Agent Skills](https://agentskills.io) and [MCP](https://modelcontextprotocol.io): the open standards, stewarded by the Agentic AI Foundation under the Linux Foundation.

## Related repos

- [evalblink](https://pypi.org/project/evalblink/): the owner's eval tool. The anybrain runner follows its choices (judge at temperature 0, JSON outputs, a cost cap) without depending on it, to keep `pyyaml` as the only dependency.
- [nanoagent](https://github.com/alexandre-darmon/nanoagent): a minimal agent loop, for when no existing harness fits.
- [ai-pm-skills](https://github.com/alexandre-darmon/ai-pm-skills): product management skills that can be installed next to these.

## License

MIT for the code, skills and templates. Your vault is yours.
