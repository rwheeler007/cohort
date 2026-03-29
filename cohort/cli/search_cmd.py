"""cohort search -- web search from CLI (DuckDuckGo, free)."""

from __future__ import annotations

import argparse

from cohort.cli._base import format_output

# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------

def _format_results(data: dict) -> str:
    """Pretty-print search results."""
    results = data.get("results", [])
    provider = data.get("provider", "unknown")
    query = data.get("query", "")

    if data.get("error"):
        return f"  [X] Search error: {data['error']}"

    if not results:
        return f"  No results for: {query}"

    lines: list[str] = [
        f"\n  Search: {query}  (via {provider})",
        "  " + "-" * 55,
    ]
    for r in results:
        pos = r.get("position", "")
        title = r.get("title", "")
        url = r.get("url", "")
        snippet = r.get("snippet", "")
        if len(snippet) > 120:
            snippet = snippet[:120] + "..."
        lines.append(f"\n  {pos}. {title}")
        lines.append(f"     {url}")
        if snippet:
            lines.append(f"     {snippet}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command handler
# ---------------------------------------------------------------------------

def _cmd_search(args: argparse.Namespace) -> int:
    """Run a web search."""
    from cohort.web_search import search

    num = getattr(args, "results", 5)
    data = search(args.query, num_results=num)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(data, json_flag=True)
    else:
        print(_format_results(data))

    if data.get("error"):
        return 1
    return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort search`` command."""

    search_parser = subparsers.add_parser(
        "search", help="Search the web (DuckDuckGo, free, no API key)",
    )
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--results", "-n", type=int, default=5, help="Number of results (default: 5)")
    search_parser.add_argument("--json", action="store_true", help="Output as JSON")


def handle(args: argparse.Namespace) -> int:
    """Dispatch search command."""
    return _cmd_search(args)
