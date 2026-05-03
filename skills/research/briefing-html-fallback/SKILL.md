---
name: briefing-html-fallback
title: HTML Briefing Fallback
description: Fallback for rendered briefings when create_briefing is unavailable. Write a standalone HTML briefing and deliver it to webchat.
version: 0.1.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [Research, Briefing, HTML, Fallback, Sources, Executive Summary]
    category: research
    related_skills: [rendered-briefing, plan]
    fallback_for_tools: [create_briefing]
    requires_tools: [write_file, send_html_to_webchat]
---

# HTML Briefing Fallback

Use this skill only when the briefing renderer tool is unavailable or the user explicitly wants a standalone HTML deliverable instead of a renderer-backed preview job.

## Core Rule

Do not promise a `/briefings/<job_id>` preview path from `create_briefing` when that tool is unavailable. In this path, you are producing a self-contained HTML file and delivering it directly to webchat.

## Workflow

1. Research and synthesize the briefing first.
   Gather evidence with the normal research tools before writing HTML.
2. Build a tight narrative structure.
   Prefer 3-5 sections with concise spoken-language narration and clear evidence.
3. Write one standalone HTML file.
   Use `write_file` to save it under `/opt/data/cron/output/<name>.html` with inline CSS and no external assets.
4. Deliver it immediately.
   Call `send_html_to_webchat` with the file path and a short caption summarizing the briefing.
5. Reply with what you delivered.
   Summarize the briefing, note any caveats, and tell the user that the HTML file was sent to the chat.

## HTML Guidance

- Keep the file self-contained.
- Use clear section numbering for navigation.
- Surface key metrics near the top.
- Mark factual confidence clearly when evidence is mixed.
- End with source attribution and date.

## When to Use

- `create_briefing` is unavailable in the active tool list.
- The renderer is down or failing and the user still wants the deliverable now.
- The user explicitly asks for a standalone HTML file rather than a renderer preview.

## Quality Bar

- Prefer fewer, tighter sections over bloated coverage.
- Keep the HTML compact enough to deliver reliably.
- Make source attribution explicit.
- Never say you rendered the briefing with `create_briefing` on this path.