"""cohort youtube -- YouTube transcript and search CLI."""

from __future__ import annotations

import argparse
import sys

from cohort.cli._base import format_output


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _format_transcript(data: dict) -> str:
    """Pretty-print a transcript result."""
    if data.get("error"):
        return f"  [X] {data['error']}"

    vid = data.get("video_id", "")
    segments = data.get("transcript", [])
    lang = data.get("language", "en")

    if not segments:
        return f"  No transcript available for {vid} ({lang})."

    lines: list[str] = [
        f"\n  Transcript: {vid} ({lang}, {len(segments)} segments)",
        "  " + "-" * 55,
    ]

    for seg in segments:
        start = seg.get("start", 0)
        minutes = int(start // 60)
        seconds = int(start % 60)
        text = seg.get("text", "")
        lines.append(f"  [{minutes:02d}:{seconds:02d}] {text}")

    return "\n".join(lines)


def _format_languages(data: dict) -> str:
    """Pretty-print available transcript languages."""
    if data.get("error"):
        return f"  [X] {data['error']}"

    vid = data.get("video_id", "")
    langs = data.get("languages", [])

    if not langs:
        return f"  No transcripts available for {vid}."

    lines: list[str] = [f"\n  Transcript languages for {vid}", "  " + "-" * 40]
    for lang in langs:
        code = lang.get("code", "")
        name = lang.get("name", "")
        auto = " (auto-generated)" if lang.get("is_generated") else ""
        lines.append(f"  {code:6s}  {name}{auto}")

    return "\n".join(lines)


def _format_search(data: dict) -> str:
    """Pretty-print YouTube search results."""
    if data.get("error"):
        return f"  [X] {data['error']}"

    results = data.get("results", [])
    query = data.get("query", "")

    if not results:
        return f"  No results for: {query}"

    lines: list[str] = [f"\n  YouTube: {query} ({len(results)} results)", "  " + "-" * 55]
    for r in results:
        title = r.get("title", "")
        vid = r.get("video_id", "")
        channel = r.get("channel_title", "")
        views = r.get("view_count")
        views_str = f"  {int(views):,} views" if views else ""
        lines.append(f"\n  {title}")
        lines.append(f"     {vid}  by {channel}{views_str}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_transcript(args: argparse.Namespace) -> int:
    """Get a video transcript (free, no API key)."""
    from cohort.youtube import get_transcript

    lang = getattr(args, "language", "en") or "en"
    data = get_transcript(args.video_id, language=lang)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(data, json_flag=True)
    else:
        print(_format_transcript(data))

    return 1 if data.get("error") else 0


def _cmd_languages(args: argparse.Namespace) -> int:
    """List available transcript languages."""
    from cohort.youtube import list_transcript_languages

    data = list_transcript_languages(args.video_id)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(data, json_flag=True)
    else:
        print(_format_languages(data))

    return 1 if data.get("error") else 0


def _cmd_youtube_search(args: argparse.Namespace) -> int:
    """Search YouTube (requires Google API key)."""
    from cohort.youtube import search_videos

    api_key = getattr(args, "api_key", None)
    if not api_key:
        # Try loading from settings
        import json
        settings_path = _settings_path()
        if settings_path.exists():
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            for sk in settings.get("service_keys", []):
                if sk.get("provider") in ("google", "youtube"):
                    from cohort.secret_store import decode_secret
                    api_key = decode_secret(sk.get("key", ""))
                    break

    if not api_key:
        print("[X] YouTube search requires a Google API key.", file=sys.stderr)
        print("    Set it: python -m cohort secret set service:google <key>", file=sys.stderr)
        print("    Or pass: --api-key <key>", file=sys.stderr)
        return 1

    max_results = getattr(args, "results", 5)
    data = search_videos(args.query, api_key=api_key, max_results=max_results)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(data, json_flag=True)
    else:
        print(_format_search(data))

    return 1 if data.get("error") else 0


def _settings_path():
    from pathlib import Path
    return Path(__file__).resolve().parent.parent.parent / "data" / "settings.json"


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort youtube`` command group."""

    yt_parser = subparsers.add_parser("youtube", help="YouTube tools (transcript, search)")
    yt_sub = yt_parser.add_subparsers(dest="youtube_command")

    # transcript (FREE)
    trans_parser = yt_sub.add_parser("transcript", help="Get video transcript (free, no API key)")
    trans_parser.add_argument("video_id", help="YouTube video ID (e.g. dQw4w9WgXcQ)")
    trans_parser.add_argument("--language", "-l", default="en", help="Language code (default: en)")
    trans_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # languages (FREE)
    lang_parser = yt_sub.add_parser("languages", help="List available transcript languages (free)")
    lang_parser.add_argument("video_id", help="YouTube video ID")
    lang_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # search (PAID - requires API key)
    search_parser = yt_sub.add_parser("search", help="Search YouTube (requires Google API key)")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--results", "-n", type=int, default=5, help="Max results (default: 5)")
    search_parser.add_argument("--api-key", default=None, help="Google API key (or set via cohort secret)")
    search_parser.add_argument("--json", action="store_true", help="Output as JSON")


def handle(args: argparse.Namespace) -> int:
    """Dispatch youtube commands."""
    sub = getattr(args, "youtube_command", None)
    if sub == "transcript":
        return _cmd_transcript(args)
    elif sub == "languages":
        return _cmd_languages(args)
    elif sub == "search":
        return _cmd_youtube_search(args)
    else:
        print("Usage: python -m cohort youtube [transcript|languages|search]", file=sys.stderr)
        return 1
