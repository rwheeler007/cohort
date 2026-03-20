"""Shared CLI utilities: output formatting, pre-flight checks, error handling."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Data directory resolution
# ---------------------------------------------------------------------------

def resolve_data_dir(args: Any) -> Path:
    """Return the data directory from args or COHORT_DATA_DIR env var."""
    raw = os.environ.get("COHORT_DATA_DIR", getattr(args, "data_dir", "data"))
    return Path(raw)


def resolve_agents_dir() -> Path:
    """Return the agents/ directory relative to the cohort package root."""
    return Path(__file__).resolve().parent.parent.parent / "agents"


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def format_output(
    data: Any,
    *,
    json_flag: bool = False,
    formatter: Callable[[Any], str] | None = None,
) -> None:
    """Print *data* as JSON or human-readable text.

    If *json_flag* is True, dump as indented JSON.  Otherwise call *formatter*
    (or fall back to ``str``).
    """
    if json_flag:
        if hasattr(data, "to_dict"):
            data = data.to_dict()
        elif isinstance(data, list) and data and hasattr(data[0], "to_dict"):
            data = [d.to_dict() for d in data]
        try:
            print(json.dumps(data, indent=2, default=str))
        except (TypeError, ValueError):
            print(json.dumps(str(data), indent=2))
    else:
        if formatter:
            print(formatter(data))
        else:
            print(data)


def truncation_notice(shown: int, total: int) -> str:
    """Return a notice string if results were truncated."""
    if shown < total:
        return f"\n  Showing {shown} of {total}. Use --limit to see more."
    return ""


# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

def require_ollama(host: str = "http://localhost:11434") -> bool:
    """Check Ollama is reachable.  Print error and return False if not."""
    import urllib.request
    import urllib.error

    try:
        req = urllib.request.Request(f"{host}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=3):
            return True
    except (urllib.error.URLError, OSError):
        print(
            f"[X] Ollama is not reachable at {host}\n"
            "    Start Ollama first: ollama serve",
            file=sys.stderr,
        )
        return False


def require_server(port: int = 5100) -> bool:
    """Check Cohort server is reachable.  Print error and return False if not."""
    import urllib.request
    import urllib.error

    try:
        req = urllib.request.Request(f"http://localhost:{port}/api/health", method="GET")
        with urllib.request.urlopen(req, timeout=3):
            return True
    except (urllib.error.URLError, OSError):
        print(
            f"[X] Cohort server is not running on port {port}\n"
            "    Start it first: python -m cohort serve",
            file=sys.stderr,
        )
        return False


# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------

def agent_not_found(agent_id: str) -> int:
    """Print agent-not-found message and return exit code 1."""
    print(f"[X] Agent '{agent_id}' not found.", file=sys.stderr)
    print("    Run 'python -m cohort agents list' to see available agents.", file=sys.stderr)
    return 1


def channel_not_found(channel_id: str) -> int:
    """Print channel-not-found message and return exit code 1."""
    print(f"[X] Channel '{channel_id}' not found.", file=sys.stderr)
    print("    Run 'python -m cohort channels list' to see available channels.", file=sys.stderr)
    return 1
