"""cohort web -- web fetch and page analysis."""

from __future__ import annotations

import argparse
import sys

from cohort.cli._base import format_output

# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_web_fetch(args: argparse.Namespace) -> int:
    """Fetch and display a web page."""
    import urllib.error
    import urllib.request

    url = args.url
    timeout = getattr(args, "timeout", 10)

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Cohort/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            raw = resp.read()

            # Try to decode as text
            encoding = "utf-8"
            if "charset=" in content_type:
                encoding = content_type.split("charset=")[-1].split(";")[0].strip()

            try:
                text = raw.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                text = raw.decode("utf-8", errors="replace")

            # Strip HTML tags for readable output (simple approach)
            if getattr(args, "raw", False):
                output = text
            else:
                import re
                # Remove script/style blocks
                clean = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", text, flags=re.DOTALL | re.IGNORECASE)
                # Remove tags
                clean = re.sub(r"<[^>]+>", " ", clean)
                # Collapse whitespace
                clean = re.sub(r"\s+", " ", clean).strip()
                # Trim to reasonable length
                limit = getattr(args, "limit", 2000)
                if len(clean) > limit:
                    clean = clean[:limit] + f"\n\n  [...truncated at {limit} chars, use --limit to see more]"
                output = clean

    except urllib.error.URLError as e:
        print(f"[X] Failed to fetch: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"[X] Connection error: {e}", file=sys.stderr)
        return 1

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output({
            "url": url,
            "content_type": content_type,
            "length": len(raw),
            "text": output[:5000],
        }, json_flag=True)
    else:
        print(output)

    return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort web`` command group."""

    web_parser = subparsers.add_parser("web", help="Web fetch and page tools")
    web_sub = web_parser.add_subparsers(dest="web_command")

    fetch_parser = web_sub.add_parser("fetch", help="Fetch and display a web page (text extracted)")
    fetch_parser.add_argument("url", help="URL to fetch")
    fetch_parser.add_argument("--raw", action="store_true", help="Show raw HTML instead of extracted text")
    fetch_parser.add_argument("--limit", type=int, default=2000, help="Max characters (default: 2000)")
    fetch_parser.add_argument("--timeout", type=int, default=10, help="Timeout in seconds (default: 10)")
    fetch_parser.add_argument("--json", action="store_true", help="Output as JSON")


def handle(args: argparse.Namespace) -> int:
    """Dispatch web commands."""
    sub = getattr(args, "web_command", None)
    if sub == "fetch":
        return _cmd_web_fetch(args)
    else:
        print("Usage: python -m cohort web fetch <url>", file=sys.stderr)
        return 1
