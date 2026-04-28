#!/usr/bin/env python3
"""
Wiki Tool Module — Persistent LLM-Maintained Knowledge Base

Provides structured operations for the LLM Wiki pattern: a persistent,
compounding knowledge base of interlinked markdown files maintained by the
LLM.  Three layers:
  - raw/  : user-curated source documents (immutable — LLM reads, never modifies)
  - wiki/ : LLM-generated pages (summaries, entities, concepts, cross-references)
  - The llm-wiki skill provides the schema (conventions, workflows, page formats)

This tool handles the operations where code is better than LLM inference:
  - search : fast full-text search across all wiki pages
  - status : wiki stats (page count, categories, recent log, orphans)
  - log    : append timestamped entries to the chronological log

Everything else (page creation, updates, cross-referencing, ingest, lint) uses
the existing file tools — the llm-wiki skill instructs the LLM how to use them.

Storage: configurable via WIKI_PATH env var or config.yaml wiki.path setting.
Default: ~/.hermes/wiki/ (profile-scoped via get_hermes_home()).
"""

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Wiki directory resolution
# ---------------------------------------------------------------------------

def _get_wiki_root() -> Path:
    """Resolve the wiki root directory.

    Priority:
      1. WIKI_PATH environment variable (absolute or ~/relative)
      2. config.yaml wiki.path setting
      3. Default: <HERMES_HOME>/wiki/
    """
    env_path = os.environ.get("WIKI_PATH")
    if env_path:
        return Path(env_path).expanduser().resolve()

    # Try config.yaml
    try:
        from hermes_cli.config import load_config
        config = load_config()
        config_path = (config.get("wiki") or {}).get("path", "")
        if config_path:
            return Path(config_path).expanduser().resolve()
    except Exception:
        pass

    return get_hermes_home() / "wiki"


def _ensure_wiki_structure(wiki_root: Path) -> None:
    """Create the wiki directory structure if it doesn't exist."""
    wiki_root.mkdir(parents=True, exist_ok=True)
    for subdir in ("sources", "entities", "concepts", "analyses", "raw"):
        (wiki_root / subdir).mkdir(exist_ok=True)

    # Seed index.md if missing
    index_path = wiki_root / "index.md"
    if not index_path.exists():
        index_path.write_text(
            "# Wiki Index\n\n"
            "Content catalog — each page listed with a link and one-line summary.\n\n"
            "## Sources\n\n"
            "## Entities\n\n"
            "## Concepts\n\n"
            "## Analyses\n\n",
            encoding="utf-8",
        )

    # Seed log.md if missing
    log_path = wiki_root / "log.md"
    if not log_path.exists():
        now = datetime.now().strftime("%Y-%m-%d")
        log_path.write_text(
            "# Wiki Log\n\n"
            "Chronological record of wiki operations.\n\n"
            f"## [{now}] init | Wiki initialized\n\n"
            "Empty wiki created with default directory structure.\n",
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# Search implementation
# ---------------------------------------------------------------------------

def _search_wiki(query: str, wiki_root: Path, max_results: int = 20) -> List[Dict[str, Any]]:
    """Search wiki pages for a query string.

    Uses case-insensitive substring matching with context snippets.
    Searches all .md files in the wiki directory (excluding raw/).
    """
    results = []
    query_lower = query.lower()
    query_pattern = re.compile(re.escape(query), re.IGNORECASE)

    for md_file in sorted(wiki_root.rglob("*.md")):
        # Skip raw sources — they're immutable reference material
        try:
            rel = md_file.relative_to(wiki_root)
        except ValueError:
            continue
        if rel.parts and rel.parts[0] == "raw":
            continue

        try:
            content = md_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        matches = list(query_pattern.finditer(content))
        if not matches:
            continue

        # Build context snippets (first 3 matches)
        snippets = []
        for match in matches[:3]:
            start = max(0, match.start() - 60)
            end = min(len(content), match.end() + 60)
            snippet = content[start:end].replace("\n", " ").strip()
            if start > 0:
                snippet = "…" + snippet
            if end < len(content):
                snippet = snippet + "…"
            snippets.append(snippet)

        results.append({
            "page": str(rel),
            "matches": len(matches),
            "snippets": snippets,
        })

        if len(results) >= max_results:
            break

    return results


# ---------------------------------------------------------------------------
# Status implementation
# ---------------------------------------------------------------------------

def _get_wiki_status(wiki_root: Path) -> Dict[str, Any]:
    """Gather wiki statistics."""
    if not wiki_root.exists():
        return {
            "initialized": False,
            "path": str(wiki_root),
            "message": "Wiki not yet initialized. Use the /llm-wiki skill to set it up.",
        }

    # Count pages by category
    categories: Dict[str, int] = {}
    total_pages = 0
    total_chars = 0
    all_pages: set = set()
    linked_pages: set = set()
    wikilink_pattern = re.compile(r"\[\[([^\]]+)\]\]")

    for md_file in wiki_root.rglob("*.md"):
        try:
            rel = md_file.relative_to(wiki_root)
        except ValueError:
            continue
        if rel.parts and rel.parts[0] == "raw":
            continue

        # Skip index.md and log.md from page counts
        if rel.name in ("index.md", "log.md"):
            continue

        category = rel.parts[0] if len(rel.parts) > 1 else "root"
        categories[category] = categories.get(category, 0) + 1
        total_pages += 1
        page_name = rel.stem
        all_pages.add(page_name)

        try:
            content = md_file.read_text(encoding="utf-8")
            total_chars += len(content)
            # Track outbound wikilinks
            for match in wikilink_pattern.finditer(content):
                linked_pages.add(match.group(1))
        except (OSError, UnicodeDecodeError):
            pass

    # Count raw sources
    raw_dir = wiki_root / "raw"
    raw_count = sum(1 for _ in raw_dir.rglob("*") if _.is_file()) if raw_dir.exists() else 0

    # Orphan detection (pages with no inbound links)
    orphans = sorted(all_pages - linked_pages) if all_pages else []

    # Recent log entries
    recent_log: List[str] = []
    log_path = wiki_root / "log.md"
    if log_path.exists():
        try:
            log_content = log_path.read_text(encoding="utf-8")
            # Extract ## [date] entries
            for line in log_content.splitlines():
                if line.startswith("## ["):
                    recent_log.append(line.strip())
            recent_log = recent_log[-5:]  # Last 5 entries
        except (OSError, UnicodeDecodeError):
            pass

    return {
        "initialized": True,
        "path": str(wiki_root),
        "total_pages": total_pages,
        "total_chars": total_chars,
        "raw_sources": raw_count,
        "categories": categories,
        "orphan_pages": orphans[:10],  # Cap at 10 for display
        "orphan_count": len(orphans),
        "recent_log": recent_log,
    }


# ---------------------------------------------------------------------------
# Log implementation
# ---------------------------------------------------------------------------

def _append_log(wiki_root: Path, operation: str, title: str, details: str = "") -> str:
    """Append a timestamped entry to log.md.

    Format: ## [YYYY-MM-DD] operation | title
    """
    _ensure_wiki_structure(wiki_root)
    log_path = wiki_root / "log.md"

    now = datetime.now().strftime("%Y-%m-%d")
    entry = f"\n## [{now}] {operation} | {title}\n"
    if details:
        entry += f"\n{details}\n"

    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry)
        return f"Logged: [{now}] {operation} | {title}"
    except OSError as e:
        return f"Failed to write log: {e}"


# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------

def wiki_tool(
    action: str,
    query: str = "",
    operation: str = "",
    title: str = "",
    details: str = "",
    max_results: int = 20,
    task_id: str = None,
) -> str:
    """Single entry point for the wiki tool. Dispatches by action.

    Returns JSON string with results.
    """
    wiki_root = _get_wiki_root()

    if action == "search":
        if not query:
            return json.dumps({"success": False, "error": "query is required for search."}, ensure_ascii=False)
        if not wiki_root.exists():
            return json.dumps({
                "success": False,
                "error": "Wiki not initialized. Load the /llm-wiki skill to set it up, or create pages with file tools.",
                "wiki_path": str(wiki_root),
            }, ensure_ascii=False)

        results = _search_wiki(query, wiki_root, max_results=max_results)
        return json.dumps({
            "success": True,
            "query": query,
            "result_count": len(results),
            "results": results,
            "wiki_path": str(wiki_root),
        }, ensure_ascii=False)

    elif action == "status":
        status = _get_wiki_status(wiki_root)
        return json.dumps({"success": True, **status}, ensure_ascii=False)

    elif action == "log":
        if not operation:
            return json.dumps({"success": False, "error": "operation is required (e.g. 'ingest', 'query', 'lint', 'update')."}, ensure_ascii=False)
        if not title:
            return json.dumps({"success": False, "error": "title is required (short description of what was done)."}, ensure_ascii=False)

        _ensure_wiki_structure(wiki_root)
        message = _append_log(wiki_root, operation, title, details)
        return json.dumps({"success": True, "message": message}, ensure_ascii=False)

    elif action == "init":
        _ensure_wiki_structure(wiki_root)
        return json.dumps({
            "success": True,
            "message": "Wiki initialized.",
            "wiki_path": str(wiki_root),
            "structure": {
                "sources/": "Source summaries (LLM-generated from raw/ materials)",
                "entities/": "Entity pages (people, places, organizations)",
                "concepts/": "Concept pages (ideas, systems, patterns)",
                "analyses/": "Analysis and comparison pages",
                "raw/": "User-curated source documents (immutable)",
                "index.md": "Content catalog with links and summaries",
                "log.md": "Chronological operation log",
            },
        }, ensure_ascii=False)

    else:
        return json.dumps({
            "success": False,
            "error": f"Unknown action '{action}'. Available: search, status, log, init",
        }, ensure_ascii=False)


def check_wiki_requirements() -> bool:
    """Wiki tool has no external requirements — always available."""
    return True


# =============================================================================
# OpenAI Function-Calling Schema
# =============================================================================

WIKI_SCHEMA = {
    "name": "wiki",
    "description": (
        "Operate on your persistent knowledge wiki — a structured, interlinked collection "
        "of markdown files that you maintain. The wiki compiles knowledge from raw sources "
        "into summaries, entity pages, concept pages, and cross-references.\n\n"
        "ACTIONS:\n"
        "- search: Full-text search across wiki pages. Returns matching pages with context snippets.\n"
        "- status: Get wiki stats — page counts by category, orphan pages, recent log entries.\n"
        "- log: Append a timestamped entry to the wiki's chronological log.\n"
        "- init: Initialize the wiki directory structure (safe to call if already exists).\n\n"
        "For reading, writing, and updating wiki pages, use the standard file tools "
        "(read_file, write_file, patch). The wiki is stored at the path shown in status output.\n\n"
        "Load the /llm-wiki skill for full wiki workflow guidance (ingest, query, lint)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "status", "log", "init"],
                "description": "The operation to perform.",
            },
            "query": {
                "type": "string",
                "description": "Search query (required for 'search' action).",
            },
            "operation": {
                "type": "string",
                "description": "Log operation type: 'ingest', 'query', 'lint', 'update', etc. (required for 'log' action).",
            },
            "title": {
                "type": "string",
                "description": "Short title for the log entry (required for 'log' action).",
            },
            "details": {
                "type": "string",
                "description": "Optional details/body for the log entry.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum search results to return (default 20).",
            },
        },
        "required": ["action"],
    },
}


# --- Registry ---
from tools.registry import registry

registry.register(
    name="wiki",
    toolset="wiki",
    schema=WIKI_SCHEMA,
    handler=lambda args, **kw: wiki_tool(
        action=args.get("action", ""),
        query=args.get("query", ""),
        operation=args.get("operation", ""),
        title=args.get("title", ""),
        details=args.get("details", ""),
        max_results=args.get("max_results", 20),
        task_id=kw.get("task_id"),
    ),
    check_fn=check_wiki_requirements,
    emoji="📚",
)
