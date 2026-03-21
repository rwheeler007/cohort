"""cohort scan -- discover exportable capabilities in a project directory.

Walks a project tree, identifies standalone entry points (CLIs, services,
importable modules), and outputs an Exports table in CLAUDE.md format.

Can optionally use the local LLM to classify what each module does.

Usage:
    cohort scan /path/to/project                  # Quick heuristic scan
    cohort scan /path/to/project --deep           # LLM-assisted classification
    cohort scan /path/to/project --save           # Save to project's .cohort-exports.json
    cohort scan /path/to/project --json           # JSON output
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from cohort.cli._base import format_output


# ---------------------------------------------------------------------------
# Heuristic signals for exportable modules
# ---------------------------------------------------------------------------

# Files that are likely standalone entry points
ENTRY_POINT_PATTERNS = [
    "service.py", "server.py", "cli.py", "main.py",
    "__main__.py", "app.py", "api.py",
]

# Filename patterns that suggest reusable tools
TOOL_PATTERNS = re.compile(
    r"(client|engine|resolver|handler|extractor|processor|renderer|builder|"
    r"manager|allocator|router|classifier|calculator|loader|deployer|deploy|"
    r"scanner|parser|analyzer|generator|scheduler|pipeline|adapter|bridge|"
    r"validator|verifier|formatter|exporter|importer)\b",
    re.IGNORECASE,
)

# Directories to skip
SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".tox", "dist", "build", "egg-info", ".eggs",
    "test-results", "coverage", ".next", ".nuxt",
}

# File extensions to consider
CODE_EXTENSIONS = {".py", ".ts", ".js"}

# Patterns inside files that indicate exportable capability
EXPORT_SIGNALS = {
    "fastapi": re.compile(r"FastAPI\(\)|app\s*=\s*FastAPI", re.IGNORECASE),
    "flask": re.compile(r"Flask\(__name__\)"),
    "cli_argparse": re.compile(r"argparse\.ArgumentParser|def main\(\)"),
    "cli_click": re.compile(r"@click\.command|@click\.group"),
    "class_def": re.compile(r"^class\s+\w+(Client|Service|Engine|Router|Manager|Backend|Processor)", re.MULTILINE),
}


# ---------------------------------------------------------------------------
# Scanner core
# ---------------------------------------------------------------------------

def scan_project(project_dir: Path, *, deep: bool = False) -> list[dict]:
    """Scan a project directory for exportable capabilities.

    Args:
        project_dir: Root directory to scan.
        deep: If True, use local LLM to classify modules (slower, better results).

    Returns:
        List of export dicts: [{name, entry_point, description, signals}]
    """
    project_dir = Path(project_dir).resolve()
    if not project_dir.is_dir():
        raise FileNotFoundError(f"Not a directory: {project_dir}")

    candidates: list[dict] = []

    for path in _walk_code_files(project_dir):
        rel = path.relative_to(project_dir).as_posix()

        # Skip test files
        if _is_test_file(rel):
            continue

        signals = _detect_signals(path)
        if not signals:
            continue

        name = _derive_name(rel, path)
        description = _derive_description(path, signals)

        candidates.append({
            "name": name,
            "entry_point": rel,
            "description": description,
            "signals": signals,
        })

    # Deduplicate (prefer shorter paths for same-name exports)
    seen: dict[str, dict] = {}
    for c in candidates:
        key = c["name"]
        if key not in seen or len(c["entry_point"]) < len(seen[key]["entry_point"]):
            seen[key] = c
    candidates = list(seen.values())

    # LLM enrichment for deep mode
    if deep and candidates:
        candidates = _enrich_with_llm(candidates, project_dir)

    # Sort by name
    candidates.sort(key=lambda c: c["name"])

    return candidates


def _walk_code_files(root: Path):
    """Yield code files, skipping excluded directories."""
    for child in sorted(root.iterdir()):
        if child.name.startswith(".") and child.is_dir():
            continue
        if child.is_dir():
            if child.name in SKIP_DIRS:
                continue
            yield from _walk_code_files(child)
        elif child.is_file() and child.suffix in CODE_EXTENSIONS:
            yield child


def _is_test_file(rel_path: str) -> bool:
    """Check if a path looks like a test file."""
    parts = rel_path.lower()
    return (
        "/test" in parts
        or parts.startswith("test")
        or "/tests/" in parts
        or "_test.py" in parts
        or ".test." in parts
        or ".spec." in parts
        or "/fixtures/" in parts
        or "/conftest" in parts
    )


def _detect_signals(path: Path) -> list[str]:
    """Detect what makes this file look exportable."""
    signals: list[str] = []
    name = path.name.lower()
    stem = path.stem.lower()

    # Filename-based signals
    if name in ENTRY_POINT_PATTERNS:
        signals.append("entry_point")

    if TOOL_PATTERNS.search(stem):
        signals.append("tool_pattern")

    # Content-based signals (read first 200 lines)
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
        # Limit to first 200 lines for speed
        lines = content.split("\n")[:200]
        head = "\n".join(lines)
    except (OSError, PermissionError):
        return signals

    for signal_name, pattern in EXPORT_SIGNALS.items():
        if pattern.search(head):
            signals.append(signal_name)

    # Docstring as extra signal
    if '"""' in head[:500] or "'''" in head[:500]:
        signals.append("has_docstring")

    return signals


def _derive_name(rel_path: str, path: Path) -> str:
    """Generate a kebab-case export name from the file path."""
    stem = path.stem.lower()

    # Strip common suffixes
    for suffix in ("_service", "_server", "_client", "_cmd", "_cli"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break

    # Convert snake_case to kebab-case
    name = stem.replace("_", "-")

    # If it's a generic name (main, app, service), use parent dir
    if name in ("main", "app", "service", "server", "index", "cli", "api"):
        parent = path.parent.name.lower().replace("_", "-")
        if parent and parent not in ("src", "lib", "cohort", "tools"):
            name = parent

    return name


def _derive_description(path: Path, signals: list[str]) -> str:
    """Extract a one-line description from the file's docstring or first comment."""
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except (OSError, PermissionError):
        return ""

    # Try to find module docstring
    for pattern in [
        r'^"""(.*?)"""',       # triple-double on one line
        r"^'''(.*?)'''",       # triple-single on one line
        r'^"""(.*?)$',         # triple-double first line of multi-line
        r"^'''(.*?)$",         # triple-single first line of multi-line
    ]:
        m = re.search(pattern, content, re.MULTILINE)
        if m:
            desc = m.group(1).strip()
            if desc:
                # Truncate at first sentence or 100 chars
                desc = desc.split("\n")[0].strip()
                if len(desc) > 100:
                    desc = desc[:97] + "..."
                return desc

    # Try first comment line
    for line in content.split("\n")[:10]:
        line = line.strip()
        if line.startswith("#") and not line.startswith("#!"):
            desc = line.lstrip("# ").strip()
            if len(desc) > 10:
                return desc[:100]

    # Fallback: describe by signals
    if "fastapi" in signals:
        return "FastAPI service"
    elif "cli_argparse" in signals or "cli_click" in signals:
        return "CLI tool"
    elif "class_def" in signals:
        return "Reusable module"

    return ""


def _enrich_with_llm(candidates: list[dict], project_dir: Path) -> list[dict]:
    """Use local LLM to improve descriptions for candidates with weak ones."""
    try:
        from cohort.local.router import LocalRouter
        router = LocalRouter()
    except Exception:
        return candidates  # Graceful fallback -- no LLM available

    for c in candidates:
        if c["description"] and len(c["description"]) > 20:
            continue  # Already has a decent description

        # Read first 50 lines for context
        try:
            full_path = project_dir / c["entry_point"]
            content = full_path.read_text(encoding="utf-8", errors="ignore")
            head = "\n".join(content.split("\n")[:50])
        except (OSError, PermissionError):
            continue

        prompt = (
            f"In one short sentence (under 80 chars), describe what this Python module does. "
            f"Focus on what it exports that other projects could reuse.\n\n"
            f"File: {c['entry_point']}\n\n```\n{head}\n```\n\n"
            f"Description:"
        )

        try:
            result = router.generate(prompt, max_tokens=100, temperature=0.1)
            if result and len(result.strip()) > 5:
                c["description"] = result.strip().split("\n")[0].strip('" ')
        except Exception:
            pass

    return candidates


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

EXPORTS_FILENAME = ".cohort-exports.json"


def save_exports(project_dir: Path, exports: list[dict]) -> Path:
    """Save scanned exports to .cohort-exports.json in the project root."""
    project_dir = Path(project_dir).resolve()
    out_path = project_dir / EXPORTS_FILENAME

    data = {
        "project": project_dir.name,
        "project_path": str(project_dir),
        "exports": [
            {
                "name": e["name"],
                "entry_point": e["entry_point"],
                "description": e["description"],
            }
            for e in exports
        ],
    }

    out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return out_path


def load_exports(project_dir: Path) -> list[dict] | None:
    """Load previously saved exports from .cohort-exports.json."""
    exports_file = Path(project_dir) / EXPORTS_FILENAME
    if not exports_file.exists():
        return None

    try:
        data = json.loads(exports_file.read_text(encoding="utf-8"))
        return data.get("exports", [])
    except (json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _format_table(exports: list[dict], project_name: str) -> str:
    """Format exports as a markdown table (CLAUDE.md Exports format)."""
    if not exports:
        return f"  No exportable capabilities found in {project_name}."

    lines = [
        f"\n  {project_name} -- {len(exports)} exportable capabilities\n",
        "  | Capability | Entry Point | What It Does |",
        "  |------------|-------------|--------------|",
    ]
    for e in exports:
        name = e["name"]
        entry = f"`{e['entry_point']}`"
        desc = e.get("description", "")
        lines.append(f"  | {name} | {entry} | {desc} |")

    return "\n".join(lines)


def _format_compact(exports: list[dict], project_name: str) -> str:
    """Format exports as a compact list."""
    if not exports:
        return f"  No exportable capabilities found in {project_name}."

    lines = [f"\n  {project_name} -- {len(exports)} exports\n"]
    for e in exports:
        desc = f" -- {e['description']}" if e.get("description") else ""
        lines.append(f"  {e['name']:30s} {e['entry_point']}{desc}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_scan(args: argparse.Namespace) -> int:
    """Scan a project for exportable capabilities."""
    project_dir = Path(args.path).resolve()
    if not project_dir.is_dir():
        print(f"[X] Not a directory: {project_dir}")
        return 1

    deep = getattr(args, "deep", False)
    save = getattr(args, "save", False)
    json_flag = getattr(args, "json", False)

    if not json_flag:
        print(f"  Scanning {project_dir.name}{'  (deep mode)' if deep else ''}...")

    exports = scan_project(project_dir, deep=deep)

    if json_flag:
        format_output(exports, json_flag=True)
    else:
        print(_format_table(exports, project_dir.name))

    if save and exports:
        out_path = save_exports(project_dir, exports)
        print(f"\n  [OK] Saved to {out_path}")

    if not exports:
        print("\n  Tip: try --deep for LLM-assisted discovery")

    return 0


def _cmd_lookup(args: argparse.Namespace) -> int:
    """Search all registered project exports for a keyword."""
    keyword = args.keyword.lower()
    data_dir = Path(getattr(args, "data_dir", "data"))
    registry_path = data_dir / "project_registry.json"

    if not registry_path.exists():
        print("  No projects registered yet. Run: cohort scan <path> --save")
        return 1

    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        print("  [X] Could not read project registry.")
        return 1

    found = False
    for project in registry.get("projects", []):
        name = project.get("name", "unknown")
        project_path = project.get("path", "")

        # Try loading from .cohort-exports.json
        exports = load_exports(Path(project_path)) if project_path else None

        # Also try CLAUDE.md Exports section
        if not exports:
            claude_md = Path(project_path) / "CLAUDE.md" if project_path else None
            if claude_md and claude_md.exists():
                exports = _parse_claude_md_exports(claude_md)

        if not exports:
            continue

        matches = [
            e for e in exports
            if keyword in e.get("name", "").lower()
            or keyword in e.get("description", "").lower()
            or keyword in e.get("entry_point", "").lower()
        ]

        if matches:
            found = True
            print(f"\n  [{name}]")
            for m in matches:
                desc = f" -- {m['description']}" if m.get("description") else ""
                print(f"  {m['name']:30s} {m.get('entry_point', '')}{desc}")

    if not found:
        print(f"  No exports matched: {keyword}")
        print("  Try broader keywords or run: cohort scan <path> --save")

    return 0


def _parse_claude_md_exports(claude_md: Path) -> list[dict]:
    """Parse the ## Exports table from a CLAUDE.md file."""
    try:
        content = claude_md.read_text(encoding="utf-8")
    except (OSError, PermissionError):
        return []

    # Find the Exports section
    in_exports = False
    exports: list[dict] = []

    for line in content.split("\n"):
        if line.strip().startswith("## Exports"):
            in_exports = True
            continue
        if in_exports and line.strip().startswith("## "):
            break
        if in_exports and line.strip().startswith("|") and "---" not in line:
            parts = [p.strip().strip("`") for p in line.split("|")]
            # Filter header row
            if len(parts) >= 4 and parts[1].lower() not in ("capability", ""):
                exports.append({
                    "name": parts[1],
                    "entry_point": parts[2],
                    "description": parts[3] if len(parts) > 3 else "",
                })

    return exports


def _cmd_register(args: argparse.Namespace) -> int:
    """Register a project in Cohort's project registry."""
    project_dir = Path(args.path).resolve()
    if not project_dir.is_dir():
        print(f"[X] Not a directory: {project_dir}")
        return 1

    data_dir = Path(getattr(args, "data_dir", "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    registry_path = data_dir / "project_registry.json"

    # Load or create registry
    if registry_path.exists():
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            registry = {"projects": []}
    else:
        registry = {"projects": []}

    # Check for duplicate
    existing = [p for p in registry["projects"] if p.get("path") == str(project_dir)]
    if existing:
        print(f"  [!] Already registered: {project_dir.name}")
        print(f"  Run: cohort scan {project_dir} --save  to update exports")
        return 0

    # Register
    registry["projects"].append({
        "name": project_dir.name,
        "path": str(project_dir),
    })

    registry_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    print(f"  [OK] Registered: {project_dir.name} ({project_dir})")

    # Auto-scan if no exports exist yet
    exports_file = project_dir / EXPORTS_FILENAME
    claude_md = project_dir / "CLAUDE.md"
    if not exports_file.exists() and not claude_md.exists():
        print(f"  Tip: run  cohort scan {project_dir} --save  to discover exports")

    return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort scan`` and ``cohort lookup``."""

    # scan
    scan_parser = subparsers.add_parser(
        "scan",
        help="Scan a project directory for exportable capabilities",
    )
    scan_parser.add_argument("path", help="Project directory to scan")
    scan_parser.add_argument("--deep", action="store_true",
                             help="Use local LLM for better classification (slower)")
    scan_parser.add_argument("--save", action="store_true",
                             help="Save results to .cohort-exports.json")
    scan_parser.add_argument("--json", action="store_true",
                             help="Output as JSON")

    # lookup
    lookup_parser = subparsers.add_parser(
        "lookup",
        help="Search registered project exports for a capability",
    )
    lookup_parser.add_argument("keyword", help="Keyword to search for")
    lookup_parser.add_argument("--data-dir", default="data",
                               help="Cohort data directory")

    # register
    reg_parser = subparsers.add_parser(
        "register",
        help="Register a project directory with Cohort",
    )
    reg_parser.add_argument("path", help="Project directory to register")
    reg_parser.add_argument("--data-dir", default="data",
                            help="Cohort data directory")


def handle(args: argparse.Namespace) -> int:
    """Dispatch scan/lookup/register commands."""
    if args.command == "scan":
        return _cmd_scan(args)
    elif args.command == "lookup":
        return _cmd_lookup(args)
    elif args.command == "register":
        return _cmd_register(args)
    return 1
