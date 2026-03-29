"""Inventory query — relevance matching against the ecosystem inventory.

Two scoring modes:
    - **Fast** (default for chat): keyword overlap scoring in Python.
      Zero latency, no model needed. Used by agent_router.py.
    - **LLM** (opt-in): sends inventory + query to local model for semantic
      matching. Used by code queue enrichment (which already budgets for
      an LLM call). Callers opt in via use_llm=True.

Budget: 300 tokens (~1200 chars) for the output block.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from cohort.inventory_schema import InventoryEntry

logger = logging.getLogger(__name__)

# ── Budget ───────────────────────────────────────────────────────────────

MAX_INVENTORY_TOKENS = 300
APPROX_CHARS_PER_TOKEN = 4
MAX_INVENTORY_CHARS = MAX_INVENTORY_TOKENS * APPROX_CHARS_PER_TOKEN  # 1200

# ── Keyword gate ─────────────────────────────────────────────────────────

IMPL_SIGNALS = frozenset({
    "build", "create", "implement", "add", "fix", "refactor",
    "port", "migrate", "extend", "write", "modify", "replace",
    "design", "architect", "integrate", "connect", "wire",
    "extract", "parse", "generate", "render", "deploy",
})


def should_query_inventory(message: str) -> bool:
    """Return True if the message likely involves implementation work.

    Simple keyword gate — avoids burning an LLM call for greetings,
    status questions, and general chat.
    """
    words = set(re.findall(r"[a-z]+", message.lower()))
    return bool(words & IMPL_SIGNALS)


# ── LLM prompt ───────────────────────────────────────────────────────────

_QUERY_PROMPT = (
    "You are an institutional knowledge assistant. Given a message and an "
    "inventory of ecosystem capabilities (tools, patterns, projects, exports), "
    "identify the TOP 3 most relevant existing assets the responder should "
    "know about.\n\n"
    "For each match, output ONE line in this exact format:\n"
    "- **[type: id]** entry_point -- why this is relevant\n\n"
    "Types: tool, pattern, project, export\n\n"
    "Rules:\n"
    "- Only include genuinely relevant matches (0 matches is fine)\n"
    "- Be specific about WHY it's relevant (not just keyword overlap)\n"
    "- If something already implements what's being discussed, say "
    "'consider extending rather than rebuilding'\n"
    "- Max 3 matches. Fewer is better than padding with weak matches.\n"
    "- Do NOT output anything except the bullet list (no preamble, no summary)\n"
)


# ── Query function ───────────────────────────────────────────────────────

def query_inventory(
    query: str,
    inventory: list[InventoryEntry] | list[dict[str, Any]] | None = None,
    server_url: str = "http://localhost:5100",
) -> str:
    """Score inventory against a query and return formatted top matches.

    Args:
        query:       The user message or task description to match against.
        inventory:   Pre-loaded inventory entries. If None, fetches from server
                     (with fallback to local YAML files).
        server_url:  Cohort server URL for inventory fetch (only used if
                     inventory is None).

    Returns:
        Formatted markdown string (bullet list of matches), or empty string
        if no relevant matches or LLM unavailable.
    """
    if not query:
        return ""

    # Resolve inventory
    entries = _resolve_inventory(inventory, server_url)
    if not entries:
        return ""

    # Build the inventory block for the LLM (one line per entry)
    inventory_lines = "\n".join(
        e.to_inventory_line() if isinstance(e, InventoryEntry)
        else InventoryEntry.from_dict(e).to_inventory_line()
        for e in entries
    )

    prompt = (
        f"{_QUERY_PROMPT}\n"
        f"## INVENTORY ({len(entries)} entries)\n{inventory_lines}\n\n"
        f"## MESSAGE\n{query}\n\n"
        f"## RELEVANT ASSETS (top 3 max)\n"
    )

    # Call local LLM
    result = _llm_generate(prompt)
    if not result:
        return ""

    # Enforce character budget
    if len(result) > MAX_INVENTORY_CHARS:
        truncated = result[:MAX_INVENTORY_CHARS]
        last_newline = truncated.rfind("\n")
        result = truncated[:last_newline] if last_newline > 0 else truncated

    return result


def query_inventory_block(query: str, **kwargs: Any) -> str:
    """Like query_inventory but wrapped in section headers for prompt injection.

    Returns empty string if no matches (so it adds zero tokens to the prompt).
    """
    result = query_inventory(query, **kwargs)
    if not result.strip():
        return ""
    return (
        "=== ECOSYSTEM CAPABILITIES ===\n"
        "The following existing tools/patterns may be relevant. "
        "Consider reusing or extending them rather than building from scratch.\n\n"
        f"{result}\n"
        "=== END ECOSYSTEM CAPABILITIES ===\n\n"
    )


# ── Internal helpers ─────────────────────────────────────────────────────

def _resolve_inventory(
    inventory: list[InventoryEntry] | list[dict[str, Any]] | None,
    server_url: str,
) -> list[InventoryEntry | dict[str, Any]]:
    """Get inventory from provided list, server, or local YAML fallback."""
    if inventory is not None:
        return inventory

    # Try Cohort server
    entries = _fetch_from_server(server_url)
    if entries:
        return entries

    # Fallback to local YAML files
    return _load_local_yaml()


def _fetch_from_server(server_url: str) -> list[dict[str, Any]]:
    """Fetch inventory from Cohort's /api/inventory endpoint."""
    try:
        import json
        import urllib.request
        url = f"{server_url}/api/inventory"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("entries", [])
    except Exception as exc:
        logger.debug("Could not fetch inventory from %s: %s", server_url, exc)
        return []


def _load_local_yaml() -> list[InventoryEntry]:
    """Fallback: load BOSS YAML inventories directly."""
    try:
        from cohort.inventory_loader import load_yaml_inventories
        return load_yaml_inventories()
    except Exception as exc:
        logger.debug("Local YAML fallback failed: %s", exc)
        return []


_router_instance = None


def _llm_generate(prompt: str) -> str | None:
    """Call local LLM via LocalRouter. Returns text or None."""
    global _router_instance  # noqa: PLW0603
    try:
        if _router_instance is None:
            from cohort.local.router import LocalRouter
            _router_instance = LocalRouter()

        result = _router_instance.route(
            prompt=prompt,
            task_type="general",
            temperature=0.2,  # Low temp for structured extraction
            response_mode="smart",  # No thinking tokens, fast
        )
        if result and result.text:
            text = re.sub(r"<think>.*?</think>", "", result.text, flags=re.DOTALL)
            return text.strip()
        return None
    except Exception as exc:
        logger.debug("[!] Inventory LLM query failed: %s", exc)
        return None
