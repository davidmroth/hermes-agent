---
name: rendered-briefing
title: Rendered Research Briefings
description: Research a topic, assemble a structured narrative, and render it into synchronized audio plus HTML assets with the create_briefing tool.
version: 0.1.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [Research, Briefing, Audio, HTML, Sources, Executive Summary]
    category: research
    related_skills: [arxiv, blogwatcher, research-paper-writing, plan]
      requires_tools: [create_briefing]
---

# Rendered Research Briefings

Use this skill when the user wants a polished briefing rather than a plain-text answer: a structured narrative with sections, sources, citations, and renderer output that can be previewed in WebUI.

## Core Rule

Do not say you will render the briefing later. When the briefing structure is ready, call `create_briefing` in the same turn.

## Workflow

1. Lock the brief only where ambiguity changes the output.
   Clarify audience, decision/use case, timeframe, and desired emphasis when those are missing or conflicting.
2. Research first.
   Use `web_search`, `web_extract`, or related research skills to gather evidence before rendering unless the user explicitly wants a mock or synthetic demo.
3. Challenge weak assumptions.
   If the user asks for a broad briefing with no audience or freshness window, narrow it. If the evidence is thin or contradictory, say so explicitly instead of faking certainty.
4. Draft a concrete outline.
   Prefer 3-5 sections. Every section needs a stable `id`, a short title, and a narration paragraph written as spoken-language sentences.
5. Map evidence cleanly.
   Put important numeric facts into `metrics`. Add `citations` that point to real `source_id` entries from the top-level `sources` list.
6. Render with `create_briefing`.
   Surface the returned `job_id`, validation warnings, and the WebUI preview path `/briefings/<job_id>` when the conversation is happening in WebUI.

## Payload Guidance

- `title`: short and specific. Good: `EU AI Regulation Risk Briefing`.
- `topic`: the analytical question or scope.
- `summary`: executive frame, one or two sentences.
- `sections[].narration`: concise spoken prose, not bullet fragments.
- `sections[].body`: supporting context, bullets, or short paragraphs.
- `metrics`: reserve for facts a user should spot quickly.
- `illustrations`: use for maps, charts, or conceptual visuals only when they materially help.
- `sources`: deduplicated, credible, and recent when recency matters.

## Minimal Rendering Pattern

1. Research and synthesize the outline.
2. Build the structured payload.
3. Call `create_briefing`.
4. Reply with:
   - what the briefing covers,
   - any caveats or validation warnings,
   - the preview path if available.

## Quality Bar

- Prefer fewer, tighter sections over bloated coverage.
- Narration should sound like a spoken explainer for an informed listener.
- If the user wants recommendations, separate facts from recommendations clearly.
- If you cannot verify a claim from sources, remove it or label it as uncertainty.