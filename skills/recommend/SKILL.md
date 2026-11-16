---
name: recommend
description: Recommend 3–5 books from the owner's ratings, reviews, tags and abandoned titles in library/. Use when asked "what should I read next" or "recommend a book on X".
---

# recommend

Reason from what the owner loved and dropped, not from titles.

## Shared rules
- `AGENTS.md` → `library/index.md` → the pages with `rating ≥ 4` and `status: abandoned`. Open at most 10.
- Never add a book to `library/` unless the owner says yes.

## Method
1. Build a taste profile from `My take` sections and tags of books rated 4–5: what the owner values (specificity, practicality, a contrarian angle, a domain). Quote one phrase per book as evidence.
2. Build the exclusion list: every title in `library/`, any status.
3. Weigh `abandoned` books: their tags and the owner's `My take` (if any) are negative signals; say which.
4. If the owner named a topic, keep to it; otherwise balance one book per dominant interest.
5. Return 3–5 titles. Each: author, year, one sentence linking it to a book the owner rated 5 (`library/<slug>.md`), one risk (why they might drop it).
6. Offer to add the chosen ones as `status: to-read` with `recommended_by: recommend skill`.

## Red lines
- No title already in the library, in any status.
- No recommendation justified by popularity or awards alone.
- Never set a rating or `date_read`.
