"""cohort import -- preference import from ChatGPT, Claude Code, config files, and paste."""

from __future__ import annotations

import argparse
import sys

from cohort.cli._base import format_output, resolve_agents_dir

# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _format_facts(facts: list, source: str) -> str:
    """Pretty-print extracted facts."""
    if not facts:
        return f"  No preferences extracted from {source}."

    lines: list[str] = [
        f"\n  Extracted Preferences from {source} ({len(facts)})",
        "  " + "-" * 55,
    ]
    for i, f in enumerate(facts, 1):
        cat = f.get("category", "?")
        text = f.get("fact", f.get("text", "?"))[:80]
        lines.append(f"  {i:3d}. [{cat}] {text}")
    return "\n".join(lines)


def _format_detect(info: dict) -> str:
    """Pretty-print detection results."""
    lines: list[str] = [
        "\n  Preference Source Detection",
        "  " + "-" * 50,
    ]

    claude = info.get("claude", {})
    if claude.get("exists"):
        lines.append(f"  [OK] Claude Code memory: {claude.get('path', '?')}")
        lines.append(f"       Memory files: {claude.get('memory_files', 0)}")
        lines.append(f"       Projects: {claude.get('project_count', 0)}")
    else:
        lines.append("  [--] Claude Code memory: not found")

    config_files = info.get("config_files", [])
    if config_files:
        lines.append(f"\n  [OK] Config files detected ({len(config_files)}):")
        for cf in config_files[:10]:
            lines.append(f"       {cf}")
    else:
        lines.append("  [--] No config files detected in current directory")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_import_detect(args: argparse.Namespace) -> int:
    """Detect available preference sources."""
    from pathlib import Path

    from cohort.import_seed import detect_claude_dir

    info = {}

    # Claude Code
    info["claude"] = detect_claude_dir()

    # Config files in current directory
    config_patterns = [
        "pyproject.toml", ".editorconfig", ".prettierrc", ".prettierrc.json",
        "tsconfig.json", ".eslintrc", ".eslintrc.json", "package.json",
        ".vscode/settings.json",
    ]
    cwd = Path(".")
    found = [p for p in config_patterns if (cwd / p).exists()]
    info["config_files"] = found

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(info, json_flag=True)
    else:
        print(_format_detect(info))
    return 0


def _cmd_import_claude(args: argparse.Namespace) -> int:
    """Import preferences from Claude Code memory."""
    from cohort.import_seed import detect_claude_dir, parse_claude_memory

    info = detect_claude_dir()
    if not info.get("exists"):
        print("  [X] Claude Code memory not found (~/.claude/).", file=sys.stderr)
        return 1

    facts = parse_claude_memory()

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(facts, json_flag=True)
    else:
        print(_format_facts(facts, "Claude Code"))
        if facts and not getattr(args, "dry_run", False):
            print("\n  Use --dry-run to preview without committing.")
            print("  To commit: python -m cohort import claude --commit")

    if getattr(args, "commit", False) and facts:
        from cohort.agent_store import AgentStore
        from cohort.import_seed import commit_facts

        store = AgentStore(agents_dir=resolve_agents_dir())
        count = commit_facts(facts, store)
        print(f"  [OK] Committed {count} facts to all agents.")

    return 0


def _cmd_import_config(args: argparse.Namespace) -> int:
    """Import preferences from config files (pyproject.toml, .editorconfig, etc.)."""
    from pathlib import Path

    from cohort.import_seed import extract_from_config_files

    project_root = Path(getattr(args, "project_root", "."))
    config_files = {}

    for name in ["pyproject.toml", ".editorconfig", ".prettierrc", ".prettierrc.json",
                  "tsconfig.json", ".eslintrc", ".eslintrc.json", "package.json",
                  ".vscode/settings.json"]:
        path = project_root / name
        if path.exists():
            try:
                config_files[name] = path.read_text(encoding="utf-8")
            except Exception:
                pass

    if not config_files:
        print(f"  [X] No config files found in {project_root}", file=sys.stderr)
        return 1

    facts = extract_from_config_files(config_files)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(facts, json_flag=True)
    else:
        print(_format_facts(facts, f"config files ({len(config_files)} files)"))

    if getattr(args, "commit", False) and facts:
        from cohort.agent_store import AgentStore
        from cohort.import_seed import commit_facts

        store = AgentStore(agents_dir=resolve_agents_dir())
        count = commit_facts(facts, store)
        print(f"  [OK] Committed {count} facts to all agents.")

    return 0


def _cmd_import_paste(args: argparse.Namespace) -> int:
    """Import preferences from pasted text."""
    from cohort.import_seed import parse_profile_paste

    text = args.text
    facts = parse_profile_paste(text)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(facts, json_flag=True)
    else:
        print(_format_facts(facts, "pasted text"))

    if getattr(args, "commit", False) and facts:
        from cohort.agent_store import AgentStore
        from cohort.import_seed import commit_facts

        store = AgentStore(agents_dir=resolve_agents_dir())
        count = commit_facts(facts, store)
        print(f"  [OK] Committed {count} facts to all agents.")

    return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort import`` command group."""

    imp_parser = subparsers.add_parser("import", help="Import preferences from external sources")
    imp_sub = imp_parser.add_subparsers(dest="import_command")

    # detect
    det_p = imp_sub.add_parser("detect", help="Detect available preference sources (default)")
    det_p.add_argument("--json", action="store_true", help="Output as JSON")

    # claude
    claude_p = imp_sub.add_parser("claude", help="Import from Claude Code memory (~/.claude/)")
    claude_p.add_argument("--commit", action="store_true", help="Commit facts to all agents")
    claude_p.add_argument("--dry-run", action="store_true", help="Preview without committing")
    claude_p.add_argument("--json", action="store_true", help="Output as JSON")

    # config
    config_p = imp_sub.add_parser("config", help="Import from config files (pyproject.toml, etc.)")
    config_p.add_argument("project_root", nargs="?", default=".",
                          help="Project root directory (default: current)")
    config_p.add_argument("--commit", action="store_true", help="Commit facts to all agents")
    config_p.add_argument("--json", action="store_true", help="Output as JSON")

    # paste
    paste_p = imp_sub.add_parser("paste", help="Import from pasted preference text")
    paste_p.add_argument("text", help="Text with one preference per line")
    paste_p.add_argument("--commit", action="store_true", help="Commit facts to all agents")
    paste_p.add_argument("--json", action="store_true", help="Output as JSON")

    imp_parser.add_argument("--json", action="store_true", help="Output as JSON")


def handle(args: argparse.Namespace) -> int:
    """Dispatch import commands."""
    sub = getattr(args, "import_command", None)
    if sub == "detect" or sub is None:
        return _cmd_import_detect(args)
    elif sub == "claude":
        return _cmd_import_claude(args)
    elif sub == "config":
        return _cmd_import_config(args)
    elif sub == "paste":
        return _cmd_import_paste(args)
    else:
        print(f"Unknown import subcommand: {sub}", file=sys.stderr)
        return 1
