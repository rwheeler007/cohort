"""cohort sessions -- channel session management CLI.

Per-channel session management is deferred. The channel integration
currently operates in single-pipe mode.
"""

from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort sessions`` commands."""
    subparsers.add_parser(
        "sessions", help="Channel session management (single-pipe mode)"
    )


def handle(args: argparse.Namespace) -> int:
    """Show single-pipe mode message."""
    print("[*] Channel sessions are in single-pipe mode.")
    print("    Per-channel session management is deferred.")
    print("    The channel plugin connects via heartbeat -- no registration needed.")
    return 0
