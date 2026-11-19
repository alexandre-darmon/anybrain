---
title: Vault figures
type: note
description: "Live figures on books, concepts, projects and recent pages, computed by Obsidian's Dataview plugin."
tags: [dashboard]
language: en
created: 2026-11-19
updated: 2026-11-19
sources: []
sensitivity: personal
---

# Vault figures

Copy this page into your vault if you want it, for example to `journal/vault-figures.md`, and install the community plugin Dataview. Obsidian computes every table below from the pages' frontmatter each time you open the page; no anybrain command is involved. The same queries can be written as an Obsidian Base.

The run traces, eval runs and costs are not here: they live in `.harness/`, which Obsidian does not index. `anybrain stats --dashboard` writes them to `journal/dashboard.md` (PRD 22.7).

## Books by status

```dataview
TABLE WITHOUT ID status AS Status, length(rows) AS Books, round(average(rows.rating), 1) AS "Average rating"
FROM "library"
WHERE type = "book"
GROUP BY status
```

## Books by rating

```dataview
TABLE WITHOUT ID rating AS Rating, length(rows) AS Books, rows.file.link AS Titles
FROM "library"
WHERE type = "book" AND rating
GROUP BY rating
SORT rating DESC
```

## Concepts by confidence

```dataview
TABLE WITHOUT ID confidence AS Confidence, length(rows) AS Concepts
FROM "wiki"
WHERE type = "concept"
GROUP BY confidence
```

## Concepts with fewer than 2 sources

A concept needs at least 2 sources (11.3).

```dataview
TABLE confidence AS Confidence, length(sources) AS Sources, updated AS Updated
FROM "wiki"
WHERE type = "concept" AND length(sources) < 2
SORT updated ASC
```

## Projects past their review date

```dataview
TABLE status AS Status, kind AS Kind, next_review AS "Next review"
FROM "projects"
WHERE type = "project" AND next_review AND date(next_review) <= date(today)
  AND status != "shipped" AND status != "dropped"
SORT next_review ASC
```

## Pages updated in the last 7 days

```dataview
TABLE type AS Type, updated AS Updated
FROM "library" OR "wiki" OR "projects" OR "me" OR "content"
WHERE updated AND date(updated) >= date(today) - dur(7 days)
SORT updated DESC
```
