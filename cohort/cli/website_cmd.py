"""cohort website -- website generation pipeline CLI."""

from __future__ import annotations

import argparse
import sys

# ---------------------------------------------------------------------------
# Command handler
# ---------------------------------------------------------------------------

def _cmd_website_create(args: argparse.Namespace) -> int:
    """Run the website creation engine.

    Delegates to the existing run_engine module which has its own
    argument handling and pipeline orchestration.
    """
    # Build argv for run_engine
    engine_argv: list[str] = []

    if getattr(args, "name", None):
        engine_argv.extend(["--name", args.name])
    if getattr(args, "description", None):
        engine_argv.extend(["--description", args.description])
    if getattr(args, "phone", None):
        engine_argv.extend(["--phone", args.phone])
    if getattr(args, "email", None):
        engine_argv.extend(["--email", args.email])
    if getattr(args, "address", None):
        engine_argv.extend(["--address", args.address])
    if getattr(args, "output", None):
        engine_argv.extend(["--output", args.output])
    if getattr(args, "action", None):
        engine_argv.extend(["--action", args.action])
    if getattr(args, "pages", None):
        engine_argv.extend(["--pages", str(args.pages)])
    if getattr(args, "dry_run", False):
        engine_argv.append("--dry-run")
    if getattr(args, "competitor_html", None):
        engine_argv.extend(["--competitor-html", args.competitor_html])

    try:
        from cohort.website_creator.run_engine import main as engine_main
        return engine_main(engine_argv)
    except ImportError:
        print("[X] Website creator module not found.", file=sys.stderr)
        print("    Check that cohort/website_creator/ exists.", file=sys.stderr)
        return 2
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort website`` command."""

    web_parser = subparsers.add_parser(
        "website", help="Generate a static website (requires Ollama)",
    )
    web_sub = web_parser.add_subparsers(dest="website_command")

    create_parser = web_sub.add_parser("create", help="Create a new website")
    create_parser.add_argument("--name", required=True, help="Business name")
    create_parser.add_argument("--description", required=True, help="Business description")
    create_parser.add_argument("--phone", default=None, help="Phone number")
    create_parser.add_argument("--email", default=None, help="Email address")
    create_parser.add_argument("--address", default=None, help="Business address")
    create_parser.add_argument("--output", "-o", default=None, help="Output directory")
    create_parser.add_argument("--action", choices=["call", "buy"], default="call",
                               help="Primary CTA type (default: call)")
    create_parser.add_argument("--pages", type=int, default=3, help="Number of pages (default: 3)")
    create_parser.add_argument("--competitor-html", default=None, help="Path to competitor HTML for reference")
    create_parser.add_argument("--dry-run", action="store_true", help="Run pipeline without writing files")


def handle(args: argparse.Namespace) -> int:
    """Dispatch website commands."""
    sub = getattr(args, "website_command", None)
    if sub == "create":
        return _cmd_website_create(args)
    else:
        print("Usage: python -m cohort website create --name <name> --description <desc>", file=sys.stderr)
        return 1
