---
name: ingest
description: Compile a new file from raw/ (book highlights, article clip, voice note, PDF) into the vault — a library or wiki page, links, index update, log line. Use when a file lands in raw/ or the owner says "ingest".
---

# ingest

Turn one raw source into vault pages. You compile; the owner judges.

## Shared rules
- Read `AGENTS.md`, then `index.md`, then the domain index. Never scan the whole vault.
- Never edit `raw/`. Never edit a protected section (`## My take`, `## Why`, `me/aspirations.md`, `me/principles.md`).
- Every write: set `updated:`, append one line to `log.md`, run `build_index.py`.
- Check the folder's model route in `.harness/models.toml` before reading `raw/docs/` or anything `confidential`.

## Inputs
Mandatory: the path of one file under `raw/`. Ask if missing: whether the owner has finished the book (`status`), and anything they want to say in `My take` (never write it for them).

## Method
1. **Classify.** Book highlights (Kindle/Readwise export, "Location" or "Highlight" markers) → `library/`. Web article → a bullet on an existing `wiki/` or `projects/` page, or a new `wiki/` note if ≥ 300 words of substance. Voice note → the project it names; else `wiki/`. PDF → summary note in `wiki/` pointing to `raw/docs/`.
2. **Find or create the page.** Search the domain index by title and author. Update if it exists; otherwise create from `.harness/templates/<type>.md`. Slug: kebab-case title, no articles.
3. **Compile.** For a book: fill `Key ideas` (5–12 bullets, each a claim, not a chapter title), `Quotes` (≤ 5, verbatim, with location), `Related`. Leave `My take` with its placeholder unless the owner gave text. For an article: 3–6 bullets with the source link.
4. **Link concepts.** For each key idea, scan `wiki/index.md`. Existing concept → add a `[[wiki/concepts/<slug>]]` link and add this book as a bullet under the concept's `What the sources say`. No concept yet and the idea now appears in ≥ 2 sources → create the concept page. One source only → no concept.
5. **Frontmatter.** `description` ≤ 150 chars, `sources`, `created`/`updated`, `sensitivity` from folder default, `tags` (reuse existing tags first; `lint` merges synonyms).
6. **Close.** `build_index.py`; `log.md` line; report in three lines: created/updated files, concepts touched, one suggestion.

## Red lines
- No page from a tweet-sized clip; it becomes a bullet on an existing page.
- No invented quote, rating, date read or opinion. Placeholders stay.
- No concept from a single source.
- Never rewrite an existing `Key ideas` section wholesale; merge, and keep the owner's edits.
