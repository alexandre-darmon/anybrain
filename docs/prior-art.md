# Prior art

anybrain builds on work other people published. This page lists each source, what anybrain takes from it, and what it leaves out. Links checked on 2026-11-16.

## The pattern

| Source | What anybrain takes | What it leaves |
| --- | --- | --- |
| [Andrej Karpathy, "LLM Wiki"](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) | The core loop: immutable raw sources, a wiki the LLM writes and maintains, `index.md` and `log.md`, the ingest / query / lint operations, good answers filed back as pages | Nothing. anybrain is one implementation of this idea file |

## Second brains and agent memory

| Source | What anybrain takes | What it leaves |
| --- | --- | --- |
| [GBrain](https://github.com/garrytan/gbrain) (Garry Tan) | Pages with a compiled summary on top and a dated, append-only timeline below; citation audits | Postgres and pgvector; the OpenClaw and Hermes skill packs |
| [COG second brain](https://github.com/huytieu/COG-second-brain) | Framework files kept separate from personal data on update; verifiers that read the files produced, not the worker's summary; one vault for several harnesses through AGENTS.md | The 43 skills; Claude-first agent configuration |
| [Open Second Brain](https://github.com/itechmeat/open-second-brain) | Repeated corrections turn into rules the owner approves, counted by code, not by an LLM | Its scheduler runtime and CLI |
| [pm-brain](https://github.com/phuryn/pm-brain) (Paweł Huryn) | Evidence tags (documented, said, hunch); a migration mode with a short interview; an eval suite with a cost cap | The PM-only scope; commands that only run in Claude Code |
| [mini-harness](https://github.com/phuryn/mini-harness) (Paweł Huryn) | "The worker never grades itself"; a regular memory clean-up pass | Writing a runtime. That belongs to a separate project |
| [claude-second-brain](https://github.com/jessepinkman9900/claude-second-brain) | Confirms the Karpathy + QMD + Obsidian combination; setup in one command | QMD from day one; Claude-only commands |
| [Agent Second Brain](https://github.com/smixs/agent-second-brain) | Voice notes as an input | Telegram, a rented server and cloud transcription. anybrain transcribes on the laptop |
| [ai-memory](https://github.com/akitaonrails/ai-memory) | Nothing directly. It shares coding-session context between harnesses, a different problem | Hooks and a memory server |
| [Personal AI Infrastructure](https://github.com/danielmiessler/Personal_AI_Infrastructure) (Daniel Miessler) | Goals and principles as first-class context files | Claude Code hooks |

## Retrieval

| Source | What anybrain takes |
| --- | --- |
| [QMD](https://github.com/tobi/qmd) (Tobi Lütke) | Local hybrid search over Markdown: keyword (BM25), vectors, query expansion and reranking with small local models. Added in phase 2 only when measured gates fail |

## Method and philosophy

| Source | What anybrain takes |
| --- | --- |
| [Tiago Forte, PARA](https://fortelabs.com/blog/para/) | The split between projects, areas, resources and archive |
| [Steph Ango, "File over app"](https://stephango.com/file-over-app) | Plain files as the layer that outlives every tool |

## Open standards

| Standard | Used for |
| --- | --- |
| [AGENTS.md](https://agents.md) | Instructions any harness reads (`CLAUDE.md` is a symlink to it) |
| [Agent Skills](https://agentskills.io) | Procedures (`SKILL.md`) that work across harnesses |
| [Model Context Protocol](https://modelcontextprotocol.io) | Tools: calendar and email in phase 4, QMD in phase 2 |

AGENTS.md, MCP and goose are stewarded by the Agentic AI Foundation under the Linux Foundation.
