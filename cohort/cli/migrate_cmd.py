"""cohort migrate -- data migration utilities."""

from __future__ import annotations

import argparse
import sys

from cohort.cli._base import resolve_data_dir

# ---------------------------------------------------------------------------
# Command handler
# ---------------------------------------------------------------------------

def _cmd_migrate_sqlite(args: argparse.Namespace) -> int:
    """Migrate JSON storage to SQLite."""
    from cohort.migrate_json_to_sqlite import migrate

    data_dir = str(resolve_data_dir(args))

    print(f"  Migrating JSON -> SQLite in {data_dir}...")
    print()

    ok = migrate(data_dir)

    if ok:
        print()
        print("  [OK] Migration complete.")
        return 0
    else:
        print()
        print("  [X] Migration failed. Check errors above.")
        return 1


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort migrate`` command."""

    migrate_parser = subparsers.add_parser(
        "migrate", help="Data migration utilities",
    )
    migrate_sub = migrate_parser.add_subparsers(dest="migrate_command")

    sqlite_parser = migrate_sub.add_parser(
        "to-sqlite", help="Migrate JSON storage to SQLite",
    )
    sqlite_parser.add_argument("--data-dir", default="data", help="Data directory")


def handle(args: argparse.Namespace) -> int:
    """Dispatch migrate commands."""
    sub = getattr(args, "migrate_command", None)
    if sub == "to-sqlite":
        return _cmd_migrate_sqlite(args)
    else:
        print("Usage: python -m cohort migrate to-sqlite [--data-dir <dir>]", file=sys.stderr)
        return 1
