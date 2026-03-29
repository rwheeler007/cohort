"""cohort test -- run E2E and unit tests from the CLI."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _project_root() -> Path:
    """Return the cohort project root (parent of cohort/ package)."""
    return Path(__file__).resolve().parent.parent.parent


def _e2e_dir() -> Path:
    return _project_root() / "tests" / "e2e"


def _unit_dir() -> Path:
    return _project_root() / "tests"


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------

def _run_e2e(args: argparse.Namespace) -> int:
    """Run Playwright E2E tests."""
    e2e = _e2e_dir()

    if not (e2e / "playwright.config.ts").exists():
        print("  [X] No playwright.config.ts found at", e2e)
        return 1

    # Check Playwright is installed
    npx = shutil.which("npx")
    if not npx:
        print("  [X] npx not found -- install Node.js to run E2E tests")
        return 1

    # Check node_modules exist
    if not (e2e / "node_modules").exists():
        print("  [*] Installing E2E test dependencies...")
        subprocess.run(["npm", "install"], cwd=str(e2e), check=True)

    # Build Playwright command
    cmd = [npx, "playwright", "test"]

    # Config path
    cmd.extend(["--config", str(e2e / "playwright.config.ts")])

    # Filter by tag
    tag = getattr(args, "tag", None)
    if tag:
        # Support both "@smoke" and "smoke"
        tag_filter = tag if tag.startswith("@") else f"@{tag}"
        cmd.extend(["--grep", tag_filter])

    # Filter by spec file
    spec = getattr(args, "spec", None)
    if spec:
        # Accept "smoke", "smoke.spec.ts", or full path
        if not spec.endswith(".spec.ts"):
            spec = f"{spec}.spec.ts"
        spec_path = e2e / "specs" / spec
        if not spec_path.exists():
            print(f"  [X] Spec file not found: {spec_path}")
            return 1
        cmd.append(str(spec_path))

    # Reporter
    reporter = getattr(args, "reporter", "list")
    cmd.extend(["--reporter", reporter])

    # Workers
    workers = getattr(args, "workers", None)
    if workers:
        cmd.extend(["--workers", str(workers)])

    # Isolated data directory (never touch production data)
    data_dir = tempfile.mkdtemp(prefix="cohort-e2e-")
    port = getattr(args, "port", 5199)

    env = {**os.environ}
    env["COHORT_E2E_PORT"] = str(port)
    env["COHORT_E2E_DATA_DIR"] = data_dir

    print("  [>>] Running E2E tests")
    print(f"       Port: {port}")
    print(f"       Data: {data_dir} (isolated)")
    if tag:
        print(f"       Tag:  {tag_filter}")
    if spec:
        print(f"       Spec: {spec}")
    print()

    try:
        result = subprocess.run(cmd, cwd=str(e2e), env=env)
        return result.returncode
    finally:
        # Clean up temp data dir
        try:
            shutil.rmtree(data_dir, ignore_errors=True)
        except Exception:
            pass


def _run_unit(args: argparse.Namespace) -> int:
    """Run pytest unit tests."""
    pytest_exe = shutil.which("pytest")
    if not pytest_exe:
        print("  [X] pytest not found -- install with: pip install pytest")
        return 1

    unit = _unit_dir()
    if not unit.exists():
        print("  [X] No tests/ directory found")
        return 1

    cmd = [sys.executable, "-m", "pytest", str(unit)]

    # Exclude e2e directory (Playwright, not pytest)
    cmd.extend(["--ignore", str(unit / "e2e")])

    # Verbose flag
    if getattr(args, "verbose", False):
        cmd.append("-v")

    # Fail fast
    if getattr(args, "fail_fast", False):
        cmd.append("-x")

    # Filter by keyword
    keyword = getattr(args, "keyword", None)
    if keyword:
        cmd.extend(["-k", keyword])

    print("  [>>] Running unit tests")
    print()

    result = subprocess.run(cmd)
    return result.returncode


def _run_all(args: argparse.Namespace) -> int:
    """Run unit tests then E2E tests."""
    print("  [>>] Phase 1: Unit tests")
    print("  " + "-" * 40)
    rc = _run_unit(args)
    if rc != 0 and not getattr(args, "no_bail", False):
        print("\n  [X] Unit tests failed -- skipping E2E")
        return rc

    print()
    print("  [>>] Phase 2: E2E tests")
    print("  " + "-" * 40)
    rc_e2e = _run_e2e(args)

    return rc_e2e if rc == 0 else rc


def _list_specs(_args: argparse.Namespace) -> int:
    """List available E2E test specs."""
    e2e = _e2e_dir()
    specs_dir = e2e / "specs"

    if not specs_dir.exists():
        print("  [X] No specs directory found")
        return 1

    print("\n  Available E2E Specs")
    print("  " + "-" * 40)

    for spec in sorted(specs_dir.glob("*.spec.ts")):
        # Count tests by counting `test(` occurrences
        content = spec.read_text(encoding="utf-8", errors="replace")
        test_count = content.count("test(\"") + content.count("test('")
        # Extract tags
        tags = set()
        for line in content.split("\n"):
            if "test.describe(" in line:
                for part in line.split():
                    if part.startswith('"@') or part.startswith("'@"):
                        tags.add(part.strip("\"'"))

        tag_str = ", ".join(sorted(tags)) if tags else ""
        name = spec.stem.replace(".spec", "")
        print(f"  {name:30s} {test_count:2d} tests  {tag_str}")

    print()
    return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort test`` command."""

    test_parser = subparsers.add_parser(
        "test",
        help="Run E2E and unit tests",
    )

    test_sub = test_parser.add_subparsers(dest="test_command")

    # cohort test e2e
    e2e_parser = test_sub.add_parser("e2e", help="Run Playwright E2E tests")
    e2e_parser.add_argument("--tag", help="Filter by tag (e.g., smoke, import, chat)")
    e2e_parser.add_argument("--spec", help="Run a specific spec file (e.g., smoke, import-preferences)")
    e2e_parser.add_argument("--port", type=int, default=5199, help="Test server port (default: 5199)")
    e2e_parser.add_argument("--reporter", default="list", help="Playwright reporter (default: list)")
    e2e_parser.add_argument("--workers", type=int, help="Number of parallel workers")

    # cohort test unit
    unit_parser = test_sub.add_parser("unit", help="Run pytest unit tests")
    unit_parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    unit_parser.add_argument("-x", "--fail-fast", action="store_true", help="Stop on first failure")
    unit_parser.add_argument("-k", "--keyword", help="Filter by keyword expression")

    # cohort test all
    all_parser = test_sub.add_parser("all", help="Run unit tests then E2E tests")
    all_parser.add_argument("--tag", help="Filter E2E by tag")
    all_parser.add_argument("--port", type=int, default=5199, help="E2E test server port")
    all_parser.add_argument("--no-bail", action="store_true", help="Run E2E even if unit tests fail")

    # cohort test list
    test_sub.add_parser("list", help="List available E2E test specs")


def handle(args: argparse.Namespace) -> int:
    """Dispatch test subcommands."""
    cmd = getattr(args, "test_command", None)

    if cmd == "e2e":
        return _run_e2e(args)
    elif cmd == "unit":
        return _run_unit(args)
    elif cmd == "all":
        return _run_all(args)
    elif cmd == "list":
        return _list_specs(args)
    else:
        # Default: show help
        print("  Usage: cohort test {e2e,unit,all,list}")
        print()
        print("  cohort test e2e              Run Playwright E2E tests")
        print("  cohort test e2e --tag smoke   Run only @smoke tagged tests")
        print("  cohort test e2e --spec import-preferences")
        print("  cohort test unit             Run pytest unit tests")
        print("  cohort test unit -x          Stop on first failure")
        print("  cohort test all              Run unit + E2E")
        print("  cohort test list             List available specs")
        return 0
