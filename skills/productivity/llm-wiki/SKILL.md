---
name: llm-wiki
description: Build and maintain a persistent, compounding knowledge wiki from raw sources. Ingest, query, lint, and cross-reference.
---

# LLM Wiki — Persistent Knowledge Base

You are maintaining a **persistent, compounding knowledge wiki** — a structured, interlinked collection of markdown files that compiles knowledge from raw sources into a living reference. You write and maintain all of it. The user curates sources, directs analysis, and asks questions.

> Inspired by [Karpathy's LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f): the wiki is a persistent artifact that gets richer with every source ingested and every question asked.

## Architecture

Three layers:

1. **Raw Sources** (`raw/`) — User-curated source documents. Immutable — you read from them but **never modify them**. This is the ground truth.
2. **Wiki Pages** — LLM-generated markdown: summaries, entity pages, concept pages, comparisons, analyses. You own this layer entirely. You create, update, and cross-reference pages.
3. **This Skill** — The schema that tells you how the wiki is structured and what workflows to follow.

## Getting Started

Before any wiki operation, check the wiki status:

```
wiki(action="status")
```

If the wiki isn't initialized:

```
wiki(action="init")
```

The wiki root path is shown in the status output. Use that path with file tools for all page reads/writes.

## Directory Structure

```
<wiki_root>/
├── index.md          # Content catalog — every page with link + one-line summary
├── log.md            # Chronological record of operations (append-only)
├── sources/          # Source summaries (one per ingested source)
├── entities/         # Entity pages (people, places, organizations, projects)
├── concepts/         # Concept pages (ideas, systems, patterns, methods)
├── analyses/         # Comparisons, syntheses, investigation results
└── raw/              # User drops raw source files here (IMMUTABLE — never edit)
```

## Page Format

Every wiki page follows this template:

```markdown
---
title: Page Title
type: entity | concept | source | analysis
created: YYYY-MM-DD
updated: YYYY-MM-DD
sources: [list of source page names that inform this page]
tags: [relevant, tags]
---

# Page Title

Brief one-paragraph summary of what this page covers.

## Key Points

- Bullet points with the essential facts

## Details

Extended content, organized with headers as appropriate.

## Cross-References

- [[Related Page 1]] — how it relates
- [[Related Page 2]] — how it relates

## Open Questions

- Unanswered questions or areas needing more sources
```

Use `[[Page Name]]` wikilink syntax to cross-reference pages. These create the knowledge graph.

## Operations

### 1. Ingest

When the user provides a new source (file, URL content, pasted text):

1. **Read the source** — understand its key content
2. **Discuss takeaways** with the user — what matters, what to emphasize
3. **Create a source summary** page in `sources/`:
   ```
   sources/descriptive-name.md
   ```
4. **Update or create entity/concept pages** — each significant entity or concept mentioned should have its own page. Update existing pages with new information; create new ones as needed. A single source typically touches 5–15 wiki pages.
5. **Update cross-references** — add `[[wikilinks]]` between related pages
6. **Update `index.md`** — add new pages with one-line summaries under the right category
7. **Log the ingest**:
   ```
   wiki(action="log", operation="ingest", title="Source Title", details="Created X pages, updated Y pages")
   ```

**Important:** When new information contradicts existing wiki content, **flag the contradiction explicitly** on the affected pages. Don't silently overwrite — note both claims and their sources.

### 2. Query

When the user asks a question against the wiki:

1. **Search first**:
   ```
   wiki(action="search", query="relevant terms")
   ```
2. **Read relevant pages** using `read_file`
3. **Synthesize an answer** with citations to wiki pages
4. **File valuable answers back** — if the answer represents a useful synthesis, comparison, or analysis, save it as a new page in `analyses/`. This way explorations compound in the knowledge base.
5. **Log the query**:
   ```
   wiki(action="log", operation="query", title="Question summary", details="Referenced: page1, page2")
   ```

### 3. Lint

When the user asks you to health-check the wiki (or proactively when it feels right):

1. **Check status**:
   ```
   wiki(action="status")
   ```
2. **Review orphan pages** — pages with no inbound wikilinks. Either add cross-references or flag for the user.
3. **Look for:**
   - Contradictions between pages (newer sources superseding older claims)
   - Stale content that needs updating
   - Important concepts mentioned but lacking their own page
   - Missing cross-references between related pages
   - Sparse pages that could be enriched
4. **Suggest new questions** to investigate and new sources to seek
5. **Log the lint**:
   ```
   wiki(action="log", operation="lint", title="Health check", details="Found: X orphans, Y missing pages, Z contradictions")
   ```

### 4. Update

When updating existing wiki pages:

1. Use `read_file` to read the current page content
2. Use `write_file` or `patch` to update it
3. Always update the `updated:` frontmatter date
4. Update cross-references if relationships changed
5. Update `index.md` if the page summary changed
6. Log significant updates:
   ```
   wiki(action="log", operation="update", title="Page Name", details="What changed and why")
   ```

## Conventions

- **Naming**: Use lowercase-kebab-case for filenames: `machine-learning.md`, `john-smith.md`
- **One page per entity/concept**: Don't merge unrelated things onto the same page
- **Wikilinks everywhere**: Link liberally. The connections are as valuable as the content.
- **Sources are sacred**: Always cite which source(s) inform a claim. Use the `sources:` frontmatter field and inline references.
- **Flag uncertainty**: If a claim is based on a single source or seems questionable, note it explicitly.
- **Keep summaries current**: When you update a page, check if the one-line summary in `index.md` still fits.
- **Log everything**: The log is the wiki's memory of its own evolution. Every ingest, significant query, lint pass, and major update should be logged.

## Tips

- Start by ingesting one source at a time and staying involved — check the updates, guide emphasis
- For large ingests, batch process but still log each source individually
- The `index.md` file is your primary navigation tool — read it first when answering queries
- When the wiki grows beyond ~100 pages, rely more on `wiki(action="search")` than reading the full index
- Periodically lint the wiki to keep it healthy — aim for zero orphan pages
