"""cohort intel -- RSS feed ingestion, article browsing, and feed management."""

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


def _format_feeds(feeds: list) -> str:
    """Pretty-print feed list."""
    if not feeds:
        return "  No feeds configured.\n  Run 'python -m cohort intel feeds add' to add one."

    lines: list[str] = [
        f"\n  Configured Feeds ({len(feeds)})",
        "  " + "-" * 60,
    ]
    for i, f in enumerate(feeds, 1):
        name = f.get("name", "unnamed")
        url = f.get("url", "")
        cat = f.get("category", "general")
        lines.append(f"  {i:3d}. [{cat}] {name}")
        lines.append(f"       {url}")

    return "\n".join(lines)


def _format_stats(stats: dict) -> str:
    """Pretty-print article database stats."""
    lines: list[str] = [
        "\n  Article Database Stats",
        "  " + "-" * 50,
    ]
    lines.append(f"  Total articles: {stats.get('total', 0)}")
    oldest = stats.get("oldest") or "n/a"
    newest = stats.get("newest") or "n/a"
    lines.append(f"  Date range:     {oldest} to {newest}")

    sources = stats.get("sources", {})
    if sources:
        lines.append(f"\n  Sources ({len(sources)}):")
        for src, count in list(sources.items())[:15]:
            lines.append(f"    {count:4d}  {src}")

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


def _cmd_intel_feeds(args: argparse.Namespace) -> int:
    """List configured feeds."""
    from cohort.intel_fetcher import IntelFetcher

    data_dir = resolve_data_dir(args)
    fetcher = IntelFetcher(data_dir=data_dir)
    feeds = fetcher.get_feeds()

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(feeds, json_flag=True)
    else:
        print(_format_feeds(feeds))

    return 0


def _cmd_intel_feeds_add(args: argparse.Namespace) -> int:
    """Add an RSS feed."""
    from cohort.intel_fetcher import IntelFetcher

    data_dir = resolve_data_dir(args)
    fetcher = IntelFetcher(data_dir=data_dir)

    url = args.url
    name = getattr(args, "name", None) or url.split("/")[-1] or "feed"
    category = getattr(args, "category", "general") or "general"

    if fetcher.add_feed(url=url, name=name, category=category):
        print(f"  [OK] Added feed: {name}")
        print(f"       URL: {url}")
        print(f"       Category: {category}")
        return 0
    else:
        print(f"  [!] Feed already exists: {url}")
        return 1


def _cmd_intel_feeds_remove(args: argparse.Namespace) -> int:
    """Remove an RSS feed by URL."""
    from cohort.intel_fetcher import IntelFetcher

    data_dir = resolve_data_dir(args)
    fetcher = IntelFetcher(data_dir=data_dir)

    if fetcher.remove_feed(args.url):
        print(f"  [OK] Removed feed: {args.url}")
        return 0
    else:
        print(f"  [X] Feed not found: {args.url}", file=sys.stderr)
        return 1


def _cmd_intel_stats(args: argparse.Namespace) -> int:
    """Show article database statistics."""
    from cohort.intel_fetcher import IntelFetcher

    data_dir = resolve_data_dir(args)
    fetcher = IntelFetcher(data_dir=data_dir)
    stats = fetcher.get_article_stats()

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(stats, json_flag=True)
    else:
        print(_format_stats(stats))

    return 0


def _cmd_intel_prune(args: argparse.Namespace) -> int:
    """Prune old articles from the database."""
    from cohort.intel_fetcher import IntelFetcher

    data_dir = resolve_data_dir(args)
    fetcher = IntelFetcher(data_dir=data_dir)

    max_age = getattr(args, "older_than", 30)
    keep_max = getattr(args, "keep_max", 500)

    result = fetcher.prune_articles(max_age_days=max_age, keep_max=keep_max)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(result, json_flag=True)
    else:
        print(f"  [OK] Pruned articles (>{max_age} days, max {keep_max}):")
        print(f"    Removed: {result['removed']}")
        print(f"    Kept:    {result['kept']}")

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

    # feeds (list)
    feeds_parser = intel_sub.add_parser("feeds", help="List configured RSS feeds")
    feeds_parser.add_argument("--json", action="store_true", help="Output as JSON")
    feeds_parser.add_argument("--data-dir", default="data", help="Data directory")

    feeds_sub = feeds_parser.add_subparsers(dest="feeds_command")

    # feeds add
    feeds_add = feeds_sub.add_parser("add", help="Add an RSS feed")
    feeds_add.add_argument("url", help="Feed URL")
    feeds_add.add_argument("--name", "-n", help="Feed name (default: derived from URL)")
    feeds_add.add_argument("--category", "-c", default="general", help="Category (default: general)")
    feeds_add.add_argument("--data-dir", default="data", help="Data directory")

    # feeds remove
    feeds_rm = feeds_sub.add_parser("remove", help="Remove an RSS feed")
    feeds_rm.add_argument("url", help="Feed URL to remove")
    feeds_rm.add_argument("--data-dir", default="data", help="Data directory")

    # stats
    stats_parser = intel_sub.add_parser("stats", help="Article database statistics")
    stats_parser.add_argument("--json", action="store_true", help="Output as JSON")
    stats_parser.add_argument("--data-dir", default="data", help="Data directory")

    # prune
    prune_parser = intel_sub.add_parser("prune", help="Prune old articles")
    prune_parser.add_argument("--older-than", type=int, default=30, help="Max age in days (default: 30)")
    prune_parser.add_argument("--keep-max", type=int, default=500, help="Max articles to keep (default: 500)")
    prune_parser.add_argument("--json", action="store_true", help="Output as JSON")
    prune_parser.add_argument("--data-dir", default="data", help="Data directory")

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
    elif sub == "feeds":
        feeds_sub = getattr(args, "feeds_command", None)
        if feeds_sub == "add":
            return _cmd_intel_feeds_add(args)
        elif feeds_sub == "remove":
            return _cmd_intel_feeds_remove(args)
        else:
            return _cmd_intel_feeds(args)
    elif sub == "stats":
        return _cmd_intel_stats(args)
    elif sub == "prune":
        return _cmd_intel_prune(args)
    else:
        print(f"Unknown intel subcommand: {sub}", file=sys.stderr)
        return 1
