"""cohort inventory -- cross-project ecosystem inventory CLI.

List, search, and refresh the unified inventory of tools, exports, patterns,
and projects across the ecosystem. Sources: VS Code project registry,
CLAUDE.md Exports tables, BOSS YAML inventories.
"""

from __future__ import annotations

import argparse

from cohort.cli._base import format_output, truncation_notice

# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _format_entry(entry, index: int) -> str:
    """Format a single inventory entry for display."""
    type_markers = {
        "tool": "[T]",
        "export": "[E]",
        "project": "[P]",
        "pattern": "[G]",
    }
    marker = type_markers.get(entry.type, "[?]")
    status = "" if entry.status == "active" else f" ({entry.status})"
    kw = ", ".join(entry.keywords[:6]) if entry.keywords else ""
    lines = [f"  {index:3d}. {marker} {entry.id}{status}"]
    if entry.description:
        lines.append(f"       {entry.description}")
    if entry.entry_point:
        lines.append(f"       -> {entry.entry_point}")
    if kw:
        lines.append(f"       keywords: {kw}")
    if entry.source_project:
        lines.append(f"       source: {entry.source_project}")
    return "\n".join(lines)


def _format_inventory(entries: list, limit: int) -> str:
    """Pretty-print the full inventory list."""
    if not entries:
        return "  No inventory entries found."

    total = len(entries)
    shown = entries[:limit]

    lines: list[str] = [
        f"\n  Ecosystem Inventory ({total} entries)",
        "  [T]=tool  [E]=export  [P]=project  [G]=pattern",
        "  " + "-" * 55,
    ]
    for i, entry in enumerate(shown, 1):
        lines.append(_format_entry(entry, i))

    notice = truncation_notice(len(shown), total)
    if notice:
        lines.append(notice)

    return "\n".join(lines)


def _format_search_results(results: list, query: str, limit: int) -> str:
    """Pretty-print search results."""
    if not results:
        return f"  No matches for '{query}'."

    total = len(results)
    shown = results[:limit]

    lines: list[str] = [
        f"\n  Search: '{query}' ({total} matches)",
        "  " + "-" * 55,
    ]
    for i, entry in enumerate(shown, 1):
        lines.append(_format_entry(entry, i))

    notice = truncation_notice(len(shown), total)
    if notice:
        lines.append(notice)

    return "\n".join(lines)


def _format_sources(entries: list) -> str:
    """Show entry counts grouped by source project."""
    from collections import Counter
    by_source = Counter(e.source_project for e in entries)
    by_type = Counter(e.type for e in entries)

    lines: list[str] = [
        f"\n  Inventory Sources ({len(entries)} total entries)",
        "  " + "-" * 40,
        "",
        "  By source:",
    ]
    for source, count in by_source.most_common():
        lines.append(f"    {source or '(unknown)':30s} {count:4d}")

    lines.append("")
    lines.append("  By type:")
    for etype, count in by_type.most_common():
        lines.append(f"    {etype:30s} {count:4d}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_list(args: argparse.Namespace) -> int:
    """List all inventory entries."""
    from cohort.inventory_loader import load_merged_inventory

    entries = load_merged_inventory()

    # Filter by type if specified
    type_filter = getattr(args, "type", None)
    if type_filter:
        entries = [e for e in entries if e.type == type_filter]

    # Filter by source if specified
    source_filter = getattr(args, "source", None)
    if source_filter:
        entries = [e for e in entries if source_filter.lower() in e.source_project.lower()]

    json_flag = getattr(args, "json", False)
    limit = getattr(args, "limit", 50)

    if json_flag:
        format_output(entries[:limit], json_flag=True)
    else:
        print(_format_inventory(entries, limit))

    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    """Search inventory by keyword."""
    from cohort.inventory_loader import load_merged_inventory

    entries = load_merged_inventory()
    query = args.query.lower()
    query_words = set(query.split())

    # Score each entry by keyword overlap
    scored = []
    for entry in entries:
        searchable = (
            f"{entry.id} {entry.description} {entry.source_project} "
            f"{' '.join(entry.keywords)} {entry.entry_point}"
        ).lower()

        # Count how many query words match
        matches = sum(1 for w in query_words if w in searchable)
        if matches > 0:
            scored.append((matches, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    results = [entry for _, entry in scored]

    json_flag = getattr(args, "json", False)
    limit = getattr(args, "limit", 20)

    if json_flag:
        format_output(results[:limit], json_flag=True)
    else:
        print(_format_search_results(results, args.query, limit))

    return 0


def _cmd_sources(args: argparse.Namespace) -> int:
    """Show inventory sources and counts."""
    from cohort.inventory_loader import load_merged_inventory

    entries = load_merged_inventory()

    json_flag = getattr(args, "json", False)
    if json_flag:
        from collections import Counter
        data = {
            "total": len(entries),
            "by_source": dict(Counter(e.source_project for e in entries)),
            "by_type": dict(Counter(e.type for e in entries)),
        }
        format_output(data, json_flag=True)
    else:
        print(_format_sources(entries))

    return 0


def _cmd_refresh(args: argparse.Namespace) -> int:
    """Refresh inventory from all sources (via server if available)."""
    from cohort.cli._base import require_server

    if require_server():
        import json
        import urllib.request
        url = "http://localhost:5100/api/inventory/refresh"
        req = urllib.request.Request(url, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                count = data.get("count", "?")
                print(f"  [OK] Inventory refreshed: {count} entries loaded")
                return 0
        except Exception as exc:
            print(f"  [X] Refresh failed: {exc}")
            return 1
    else:
        # Fallback: just load locally to verify it works
        from cohort.inventory_loader import load_merged_inventory
        entries = load_merged_inventory()
        print(f"  [OK] Local inventory loaded: {len(entries)} entries (server not running)")
        return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort inventory`` commands."""
    inv_parser = subparsers.add_parser(
        "inventory", help="Cross-project ecosystem inventory"
    )
    inv_sub = inv_parser.add_subparsers(dest="inventory_command")

    # cohort inventory list [--type TYPE] [--source NAME]
    list_parser = inv_sub.add_parser("list", help="List all entries (default)")
    list_parser.add_argument("--type", choices=["tool", "export", "project", "pattern"],
                             help="Filter by entry type")
    list_parser.add_argument("--source", help="Filter by source project name")
    list_parser.add_argument("--limit", type=int, default=50, help="Max entries (default: 50)")
    list_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # cohort inventory search <query>
    search_parser = inv_sub.add_parser("search", help="Search inventory by keyword")
    search_parser.add_argument("query", help="Search terms")
    search_parser.add_argument("--limit", type=int, default=20, help="Max results (default: 20)")
    search_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # cohort inventory sources
    sources_parser = inv_sub.add_parser("sources", help="Show entry counts by source")
    sources_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # cohort inventory refresh
    inv_sub.add_parser("refresh", help="Refresh inventory from all sources")

    # Default flags on the parent parser too
    inv_parser.add_argument("--type", choices=["tool", "export", "project", "pattern"],
                            help="Filter by entry type")
    inv_parser.add_argument("--source", help="Filter by source project name")
    inv_parser.add_argument("--limit", type=int, default=50, help="Max entries (default: 50)")
    inv_parser.add_argument("--json", action="store_true", help="Output as JSON")


def handle(args: argparse.Namespace) -> int:
    """Dispatch inventory commands."""
    sub = getattr(args, "inventory_command", None)
    if sub == "search":
        return _cmd_search(args)
    elif sub == "sources":
        return _cmd_sources(args)
    elif sub == "refresh":
        return _cmd_refresh(args)
    else:
        # Default: list
        return _cmd_list(args)
