# personal-harness

**A local-first, model-agnostic second brain: Markdown you own, operated by whichever agent you choose.**

Most "AI second brains" die in one of two ways. You stop filing (the Obsidian graveyard), or you wake up locked into a vendor's memory feature you can't export. This repo is the harness I use to avoid both: plain files on disk, an agent that does the filing, and every layer on an open standard so the model and the tool are swappable.

It was built after I abandoned a hand-maintained Obsidian vault. The knowledge base was good; the maintenance cost killed it. The fix was not a better note app. It was handing the filing job to the agent.

---

## What it does

| You | The harness |
| --- | --- |
| Drop a Kindle export, a web clip, a voice memo into `raw/` | Compiles it into a wiki page, links it, updates the index |
| Finish a book and write three lines of opinion | Keeps your review untouched, extracts the ideas, feeds future recommendations |
| Ask "where do my side projects stand?" | Reads the index, opens the right files, answers with sources |
| Start the day | Writes a brief from your calendar, email and open projects, without copying any of it into the vault |
| Switch from Claude to a local model | Nothing to migrate: same files, same skills, same instructions |

## Design principles

1. **File over app.** Markdown for knowledge, CSV for numbers. If every tool here disappeared, the vault still works in a text editor.
2. **The agent writes, you judge.** The LLM maintains the wiki, the indexes and the links. You write only what needs your voice: reviews, aspirations, decisions. Those sections are protected.
3. **Indexes before retrieval.** A hierarchy of `index.md` files, regenerated from frontmatter by a script, is the navigation layer. Semantic search is added only when measured recall drops (see `docs/SPEC.md`, section 8).
4. **Open standards at every seam.** `AGENTS.md` for instructions, Agent Skills (`SKILL.md`) for procedures, MCP for tools, an OpenAI-compatible endpoint for models. No hooks, no vendor plugins.
5. **Live data is queried, never copied.** Email and calendar stay in their apps. The vault keeps a daily digest, not the raw messages.
6. **Sensitivity decides the model.** Finance, company and email run on a local model by default. Frontier models get books and public research.
7. **Tested like code.** A golden set of questions about the vault runs on every model or harness change. The brain is its own benchmark.

## Architecture

```
                 you                                    agent (Claude Code, OpenCode, …)
                  │                                              │
    drop files    │                                 reads AGENTS.md + skills
                  ▼                                              ▼
   ┌───────────────────────────  ~/brain  (iCloud, private)  ──────────────────────────┐
   │  AGENTS.md         who you are, where things live, rules                          │
   │  index.md          one line per domain  ──▶  <domain>/index.md  ──▶  pages        │
   │  raw/              immutable inputs (clips, highlights, transcripts)              │
   │  wiki/concepts/    cross-source ideas, LLM-written                                │
   │  library/          one file per book: your review + compiled ideas               │
   │  projects/         one status file per project                                   │
   │  company/ finance/ CSV + notes, local model only                                  │
   │  journal/          daily briefs (digests of live data)                            │
   └────────────────────────────────────────────────────────────────────────────────────┘
                  ▲                                              ▲
     live data    │ MCP (read-only)                 skills       │ ~/.agents/skills/
   email, calendar┘                        ingest · lint · ask · recommend · daily-brief
```

Two locations, one rule: **this repo holds the method, your machine holds the data.**

| In this repo (public) | On your machine (private) |
| --- | --- |
| `AGENTS.md` template, skills, scripts, page templates | The vault itself (`~/brain`, synced by iCloud) |
| `vault.example/` with fake data | Real reviews, projects, holdings, company files |
| Golden-question template | Your golden questions and their expected answers |
| Model-profile template | API keys, local model config, MCP credentials |

## Quick start

```bash
git clone https://github.com/alexandre-darmon/personal-harness
cd personal-harness
./scripts/install.sh ~/Library/Mobile\ Documents/com~apple~CloudDocs/brain
# → creates the vault from vault.example, links skills into ~/.agents/skills,
#   writes AGENTS.md, builds the first index

cd ~/Library/Mobile\ Documents/com~apple~CloudDocs/brain
claude          # or: opencode, codex, goose — anything that reads AGENTS.md
> ingest raw/highlights/snow-leopard.md
> what do I believe about category design?
```

Full implementation details, schemas and acceptance criteria: [`docs/SPEC.md`](docs/SPEC.md).

## The skills

| Skill | Trigger | What it produces |
| --- | --- | --- |
| `ingest` | a new file in `raw/`, "ingest this" | a wiki or library page, links, index update, log entry |
| `ask` | any question about the vault | an answer with file citations; optionally filed back as a page |
| `lint` | weekly, or "lint the vault" | broken links fixed, stale projects flagged, contradictions listed, indexes rebuilt |
| `recommend` | "what should I read next?" | 3 to 5 titles reasoned from your ratings, tags and abandoned books |
| `daily-brief` | each morning (scheduled) | one page in `journal/` from calendar, inbox and open projects, read-only |

## Why not just RAG?

At personal scale, a compiled wiki with good indexes beats chunk retrieval: the agent reads a few dense pages instead of thirty fragments, and you can open `index.md` and see exactly what it saw. The spec defines four measurable gates (recall on golden questions, index size, needle-type questions, files opened per answer) that trigger adding a local hybrid search engine. Until one trips, there is nothing to run.

## Inspirations

This harness composes ideas from people who published theirs:

- **Andrej Karpathy, "LLM Wiki"** (April 2026): the raw/ → wiki/ → index pattern, and the principle that the LLM, not the human, maintains the wiki.
- **Daniel Miessler, Personal AI Infrastructure**: goals and context files as first-class inputs, skills as the unit of capability. I kept the ideas and dropped the Claude Code-specific hooks to stay portable.
- **Tobi Lütke, QMD**: local hybrid search over Markdown. The retrieval layer this harness grows into.
- **Tiago Forte, PARA**: projects, areas, resources, archive, which shaped the folder split.
- **Steph Ango (Obsidian), "File over app"**: the reason everything here is a text file.
- **Agent Skills, AGENTS.md and MCP**: the open standards (stewarded by agentskills.io and the Agentic AI Foundation under the Linux Foundation) that make the harness vendor-neutral.

## Related projects

- [evalblink](https://pypi.org/project/evalblink/): runs the golden-question suite.
- [nanoagent](https://github.com/alexandre-darmon/nanoagent): a raw agent loop; the fallback operator when no commercial harness fits.
- [ai-pm-skills](https://github.com/alexandre-darmon/ai-pm-skills): the professional skills this harness loads for client work.

## License

MIT for scripts and templates. Your vault is yours.
