"""cohort analyze -- text analysis, keyword extraction, and content scoring CLI."""

from __future__ import annotations

import argparse
import json
import sys

from cohort.cli._base import format_output, resolve_data_dir


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _format_keywords(keywords: list[str]) -> str:
    """Pretty-print keyword list."""
    if not keywords:
        return "  No keywords extracted."
    lines: list[str] = [
        f"\n  Keywords ({len(keywords)})",
        "  " + "-" * 40,
    ]
    for i, kw in enumerate(keywords, 1):
        lines.append(f"  {i:3d}. {kw}")
    return "\n".join(lines)


def _format_overlap(score: float, keywords1: list[str], keywords2: list[str]) -> str:
    """Pretty-print overlap result."""
    pct = score * 100
    bar_len = 20
    filled = int(pct / 100 * bar_len)
    bar = "#" * filled + "." * (bar_len - filled)
    lines: list[str] = [
        "\n  Keyword Overlap",
        "  " + "-" * 40,
        f"  Score: [{bar}] {pct:.1f}%",
        f"  Text 1 keywords: {len(keywords1)}",
        f"  Text 2 keywords: {len(keywords2)}",
    ]
    shared = set(keywords1) & set(keywords2)
    if shared:
        lines.append(f"  Shared: {', '.join(sorted(shared))}")
    return "\n".join(lines)


def _format_article_score(result: dict) -> str:
    """Pretty-print article scoring result."""
    lines: list[str] = [
        "\n  Article Score",
        "  " + "-" * 40,
    ]
    score = result.get("score", 0)
    score_bar = "#" * min(score, 10) + "." * max(10 - score, 0)
    lines.append(f"  Score: [{score_bar}] {score}/10")

    if result.get("negative_hit"):
        lines.append(f"  EXCLUDED: {result.get('reason', 'negative keyword match')}")
        return "\n".join(lines)

    matched = result.get("matched_keywords", [])
    if matched:
        lines.append(f"  Matched keywords: {', '.join(matched)}")

    pillars = result.get("pillar_matches", [])
    if pillars:
        lines.append(f"  Pillar matches: {', '.join(pillars)}")

    audience = result.get("audience_match")
    if audience:
        lines.append(f"  Audience match: {audience}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_analyze_keywords(args: argparse.Namespace) -> int:
    """Extract keywords from text."""
    from cohort.meeting import extract_keywords

    text = args.text
    keywords = extract_keywords(text)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(keywords, json_flag=True)
    else:
        print(_format_keywords(keywords))
    return 0


def _cmd_analyze_overlap(args: argparse.Namespace) -> int:
    """Calculate keyword overlap between two texts."""
    from cohort.meeting import extract_keywords, calculate_keyword_overlap

    kw1 = extract_keywords(args.text1)
    kw2 = extract_keywords(args.text2)
    score = calculate_keyword_overlap(kw1, kw2)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output({
            "overlap_score": score,
            "keywords_1": kw1,
            "keywords_2": kw2,
            "shared": sorted(set(kw1) & set(kw2)),
        }, json_flag=True)
    else:
        print(_format_overlap(score, kw1, kw2))
    return 0


def _cmd_analyze_score(args: argparse.Namespace) -> int:
    """Score an article against a project strategy."""
    from cohort.content_analyzer import score_article

    # Load article
    try:
        article_data = json.loads(args.article)
    except json.JSONDecodeError:
        # Treat as title string
        article_data = {"title": args.article, "summary": args.article}

    # Load project
    project_path = getattr(args, "project", None)
    if project_path:
        try:
            with open(project_path) as f:
                project_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"  [X] Failed to load project config: {e}", file=sys.stderr)
            return 1
    else:
        # Try loading default project config
        from pathlib import Path
        data_dir = resolve_data_dir(args)
        config_path = data_dir / "content_projects.json"
        if config_path.exists():
            try:
                projects = json.loads(config_path.read_text(encoding="utf-8"))
                if isinstance(projects, list) and projects:
                    project_data = projects[0]
                elif isinstance(projects, dict):
                    project_data = projects
                else:
                    print("  [X] No project config found. Use --project <file>.", file=sys.stderr)
                    return 1
            except json.JSONDecodeError:
                print("  [X] Invalid content_projects.json.", file=sys.stderr)
                return 1
        else:
            print("  [X] No project config found. Use --project <file>.", file=sys.stderr)
            return 1

    result = score_article(article_data, project_data)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(result, json_flag=True)
    else:
        print(_format_article_score(result))
    return 0


def _cmd_analyze_distill(args: argparse.Namespace) -> int:
    """Distill verbose text to a structured briefing."""
    from cohort.cli._base import require_ollama

    if not require_ollama():
        return 1

    from cohort.local.router import LocalRouter

    router = LocalRouter()
    text = args.text

    result = router.distill(text)
    if result is None:
        print("  [X] Distillation failed (model returned nothing).", file=sys.stderr)
        return 1

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output({"distilled": result}, json_flag=True)
    else:
        print(f"\n  Distilled ({len(result)} chars):")
        print("  " + "-" * 50)
        for line in result.split("\n"):
            print(f"  {line}")
    return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort analyze`` command group."""

    analyze_parser = subparsers.add_parser("analyze", help="Text analysis, keywords, and content scoring")
    analyze_sub = analyze_parser.add_subparsers(dest="analyze_command")

    # keywords
    kw_parser = analyze_sub.add_parser("keywords", help="Extract keywords from text")
    kw_parser.add_argument("text", help="Text to extract keywords from")
    kw_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # overlap
    ov_parser = analyze_sub.add_parser("overlap", help="Calculate keyword overlap between two texts")
    ov_parser.add_argument("text1", help="First text")
    ov_parser.add_argument("text2", help="Second text")
    ov_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # score
    sc_parser = analyze_sub.add_parser("score", help="Score article against project strategy")
    sc_parser.add_argument("article", help="Article as JSON string or title text")
    sc_parser.add_argument("--project", help="Path to project config JSON")
    sc_parser.add_argument("--json", action="store_true", help="Output as JSON")
    sc_parser.add_argument("--data-dir", default="data", help="Data directory")

    # distill
    dist_parser = analyze_sub.add_parser("distill", help="Distill verbose text to structured briefing (requires Ollama)")
    dist_parser.add_argument("text", help="Text to distill")
    dist_parser.add_argument("--json", action="store_true", help="Output as JSON")

    analyze_parser.add_argument("--json", action="store_true", help="Output as JSON")


def handle(args: argparse.Namespace) -> int:
    """Dispatch analyze commands."""
    sub = getattr(args, "analyze_command", None)
    if sub == "keywords":
        return _cmd_analyze_keywords(args)
    elif sub == "overlap":
        return _cmd_analyze_overlap(args)
    elif sub == "score":
        return _cmd_analyze_score(args)
    elif sub == "distill":
        return _cmd_analyze_distill(args)
    elif sub is None:
        print("  Usage: python -m cohort analyze {keywords|overlap|score|distill}")
        return 0
    else:
        print(f"Unknown analyze subcommand: {sub}", file=sys.stderr)
        return 1
