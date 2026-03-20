"""cohort intel -- RSS feed ingestion and article browsing."""

from __future__ import annotations

import argparse
import sys

from cohort.cli._base import format_output, resolve_data_dir, truncation_notice


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _format_articles(articles: list, limit: int) -> str:
    """Pretty-print article list."""
    if not articles:
        return "  No articles found."

    total = len(articles)
    shown = articles[:limit]

    lines: list[str] = [f"\n  Intel Articles ({total} total)", "  " + "-" * 60]
    for a in shown:
        score = a.get("relevance_score", 0)
        title = a.get("title", "")[:60]
        source = a.get("source", "")
        pub = a.get("published", "")[:10]
        score_bar = "#" * min(score, 10) + "." * max(10 - score, 0)
        lines.append(f"  [{score_bar}] {score:2d}  {title}")
        lines.append(f"         {source}  {pub}")

    notice = truncation_notice(len(shown), total)
    if notice:
        lines.append(notice)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_intel_fetch(args: argparse.Namespace) -> int:
    """Fetch RSS feeds and store articles."""
    from cohort.intel_fetcher import IntelFetcher

    data_dir = resolve_data_dir(args)
    fetcher = IntelFetcher(data_dir=data_dir)

    print("  Fetching RSS feeds...")
    count = fetcher.fetch()
    print(f"  [OK] {count} new articles ingested.")
    return 0


def _cmd_intel_top(args: argparse.Namespace) -> int:
    """Show top-scored articles."""
    from cohort.intel_fetcher import IntelFetcher

    data_dir = resolve_data_dir(args)
    fetcher = IntelFetcher(data_dir=data_dir)

    limit = getattr(args, "limit", 15)
    min_score = getattr(args, "min_score", 5)
    articles = fetcher.get_top(limit=limit, min_score=min_score)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(articles, json_flag=True)
    else:
        print(_format_articles(articles, limit))

    return 0


def _cmd_intel_recent(args: argparse.Namespace) -> int:
    """Show recent articles."""
    from cohort.intel_fetcher import IntelFetcher

    data_dir = resolve_data_dir(args)
    fetcher = IntelFetcher(data_dir=data_dir)

    limit = getattr(args, "limit", 20)
    articles = fetcher.get_articles(limit=limit)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(articles, json_flag=True)
    else:
        print(_format_articles(articles, limit))

    return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort intel`` command group."""

    intel_parser = subparsers.add_parser("intel", help="RSS feed ingestion and intel browsing")
    intel_sub = intel_parser.add_subparsers(dest="intel_command")

    # fetch
    fetch_parser = intel_sub.add_parser("fetch", help="Fetch RSS feeds now")
    fetch_parser.add_argument("--data-dir", default="data", help="Data directory")

    # top
    top_parser = intel_sub.add_parser("top", help="Show top-scored articles")
    top_parser.add_argument("--limit", type=int, default=15, help="Max articles (default: 15)")
    top_parser.add_argument("--min-score", type=int, default=5, help="Minimum score (default: 5)")
    top_parser.add_argument("--json", action="store_true", help="Output as JSON")
    top_parser.add_argument("--data-dir", default="data", help="Data directory")

    # recent
    recent_parser = intel_sub.add_parser("recent", help="Show recent articles")
    recent_parser.add_argument("--limit", type=int, default=20, help="Max articles (default: 20)")
    recent_parser.add_argument("--json", action="store_true", help="Output as JSON")
    recent_parser.add_argument("--data-dir", default="data", help="Data directory")

    intel_parser.add_argument("--json", action="store_true", help="Output as JSON")
    intel_parser.add_argument("--data-dir", default="data", help="Data directory")


def handle(args: argparse.Namespace) -> int:
    """Dispatch intel commands."""
    sub = getattr(args, "intel_command", None)
    if sub == "fetch":
        return _cmd_intel_fetch(args)
    elif sub == "top":
        return _cmd_intel_top(args)
    elif sub == "recent" or sub is None:
        return _cmd_intel_recent(args)
    else:
        print(f"Unknown intel subcommand: {sub}", file=sys.stderr)
        return 1
