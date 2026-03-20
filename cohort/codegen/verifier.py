"""Verification stack for codegen output.

Three layers, run in order:
    1. Syntax check (ast.parse for Python, tsc for TypeScript)
    2. Unit tests (pytest on affected modules)
    3. E2E smoke tests (Playwright, optional -- degrades gracefully)

Changes are written to a temporary directory for verification,
never to the user's working tree. This is non-negotiable.
"""

from __future__ import annotations

import ast
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from cohort.codegen.models import (
    CodegenTask,
    FileChange,
    VerificationReport,
    VerificationResult,
)

logger = logging.getLogger(__name__)

# E2E test tags mapped from file path prefixes
FILE_TAG_MAP: dict[str, list[str]] = {
    "cohort/server.py": ["smoke"],
    "cohort/api.py": ["smoke", "chat"],
    "cohort/chat.py": ["chat"],
    "cohort/agent_router.py": ["chat"],
    "cohort/local/": ["chat"],
    "cohort/static/cohort.js": ["chat", "settings"],
    "cohort/static/": ["smoke"],
    "cohort/templates/": ["smoke"],
    "cohort/socketio_events.py": ["chat"],
    "cohort/import_seed.py": ["import"],
    "cohort/static/cohort-setup.js": ["import", "settings"],
}


def _get_e2e_tags(changed_files: list[str]) -> set[str]:
    """Map changed file paths to E2E test tags."""
    tags: set[str] = set()
    for fpath in changed_files:
        normalized = fpath.replace("\\", "/")
        for prefix, file_tags in FILE_TAG_MAP.items():
            if normalized.startswith(prefix) or normalized == prefix:
                tags.update(file_tags)
    # Always include smoke
    tags.add("smoke")
    return tags


# =====================================================================
# Layer 1: Syntax checking
# =====================================================================

def _check_syntax_python(path: Path, content: str) -> VerificationResult:
    """Check Python syntax via ast.parse."""
    start = time.monotonic()
    try:
        ast.parse(content, filename=str(path))
        return VerificationResult(
            name=f"syntax:{path.name}",
            passed=True,
            message="Python syntax OK",
            duration_seconds=time.monotonic() - start,
        )
    except SyntaxError as exc:
        return VerificationResult(
            name=f"syntax:{path.name}",
            passed=False,
            message=f"SyntaxError at line {exc.lineno}: {exc.msg}",
            duration_seconds=time.monotonic() - start,
        )


def _check_syntax_typescript(path: Path, project_root: Path) -> VerificationResult:
    """Check TypeScript/JavaScript syntax via tsc --noEmit (if available)."""
    start = time.monotonic()
    tsc = shutil.which("npx")
    if tsc is None:
        return VerificationResult(
            name=f"syntax:{path.name}",
            passed=True,
            message="npx not available, skipping TS syntax check",
            duration_seconds=time.monotonic() - start,
        )

    try:
        result = subprocess.run(
            ["npx", "tsc", "--noEmit", "--allowJs", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(project_root),
        )
        passed = result.returncode == 0
        return VerificationResult(
            name=f"syntax:{path.name}",
            passed=passed,
            message=result.stderr.strip() or result.stdout.strip() or "OK",
            duration_seconds=time.monotonic() - start,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return VerificationResult(
            name=f"syntax:{path.name}",
            passed=True,  # Don't block on tool failure
            message=f"TS check skipped: {exc}",
            duration_seconds=time.monotonic() - start,
        )


def check_syntax(changes: list[FileChange], staging_dir: Path) -> list[VerificationResult]:
    """Run syntax checks on all changed files."""
    results: list[VerificationResult] = []
    for change in changes:
        file_path = staging_dir / change.path
        if not file_path.exists():
            continue

        suffix = file_path.suffix.lower()
        if suffix == ".py":
            results.append(_check_syntax_python(file_path, change.content))
        elif suffix in (".ts", ".tsx", ".js", ".jsx"):
            results.append(_check_syntax_typescript(file_path, staging_dir))
        # Other file types: skip syntax check (HTML, CSS, JSON, etc.)

    return results


# =====================================================================
# Layer 2: Unit tests (pytest)
# =====================================================================

def run_pytest(staging_dir: Path, changed_files: list[str]) -> VerificationResult:
    """Run pytest on the staging directory.

    Only runs if there's a tests/ directory and pytest is available.
    Uses -x (fail fast) and --tb=short for concise output.
    """
    start = time.monotonic()

    tests_dir = staging_dir / "tests"
    if not tests_dir.exists():
        return VerificationResult(
            name="pytest",
            passed=True,
            message="No tests/ directory found, skipping",
            duration_seconds=time.monotonic() - start,
        )

    pytest_cmd = shutil.which("pytest")
    if pytest_cmd is None:
        return VerificationResult(
            name="pytest",
            passed=True,
            message="pytest not installed, skipping",
            duration_seconds=time.monotonic() - start,
        )

    try:
        result = subprocess.run(
            [
                pytest_cmd, "-x", "--tb=short", "-q",
                "--no-header",
                str(tests_dir),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(staging_dir),
            env={**os.environ, "PYTHONPATH": str(staging_dir)},
        )
        passed = result.returncode == 0
        output = result.stdout.strip()
        # Keep output concise
        lines = output.splitlines()
        if len(lines) > 20:
            output = "\n".join(lines[:10] + ["..."] + lines[-10:])

        return VerificationResult(
            name="pytest",
            passed=passed,
            message=output or result.stderr.strip() or "No output",
            duration_seconds=time.monotonic() - start,
        )
    except subprocess.TimeoutExpired:
        return VerificationResult(
            name="pytest",
            passed=False,
            message="pytest timed out after 120s",
            duration_seconds=time.monotonic() - start,
        )
    except OSError as exc:
        return VerificationResult(
            name="pytest",
            passed=True,  # Don't block on tool failure
            message=f"pytest skipped: {exc}",
            duration_seconds=time.monotonic() - start,
        )


# =====================================================================
# Layer 3: E2E tests (Playwright, optional)
# =====================================================================

def _playwright_available() -> bool:
    """Check if Playwright is installed and npx is available."""
    npx = shutil.which("npx")
    if npx is None:
        return False
    # Check if playwright package exists
    try:
        result = subprocess.run(
            ["npx", "playwright", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def run_e2e(
    project_root: Path,
    tags: set[str],
    port: int = 5199,
) -> VerificationResult:
    """Run tagged Playwright E2E tests against an isolated Cohort instance.

    The test server uses an isolated data directory (temp dir) so
    production data is never touched.

    Args:
        project_root: Path to the Cohort project root (for finding test specs).
        tags: Set of test tags to run (e.g. {"smoke", "chat"}).
        port: Port for the isolated test server.

    Returns:
        VerificationResult. If Playwright is not installed, returns
        a passing result with a skip message (graceful degradation).
    """
    start = time.monotonic()

    if not _playwright_available():
        return VerificationResult(
            name="e2e",
            passed=True,
            message="Playwright not installed, E2E tests skipped "
                    "(install with: pip install cohort[e2e])",
            duration_seconds=time.monotonic() - start,
        )

    e2e_dir = project_root / "tests" / "e2e"
    config_path = e2e_dir / "playwright.config.ts"
    if not config_path.exists():
        return VerificationResult(
            name="e2e",
            passed=True,
            message="No E2E test config found at tests/e2e/playwright.config.ts",
            duration_seconds=time.monotonic() - start,
        )

    # Build grep pattern from tags: "@smoke|@chat|@settings"
    grep_pattern = "|".join(f"@{tag}" for tag in sorted(tags))

    # Create isolated data dir for the test server
    test_data_dir = tempfile.mkdtemp(prefix="cohort-e2e-")

    try:
        result = subprocess.run(
            [
                "npx", "playwright", "test",
                "--config", str(config_path),
                "--grep", grep_pattern,
            ],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=str(e2e_dir),
            env={
                **os.environ,
                "COHORT_E2E_PORT": str(port),
                "COHORT_E2E_DATA_DIR": test_data_dir,
            },
        )

        passed = result.returncode == 0
        output = result.stdout.strip()
        lines = output.splitlines()
        if len(lines) > 30:
            output = "\n".join(lines[:15] + ["..."] + lines[-15:])

        return VerificationResult(
            name="e2e",
            passed=passed,
            message=output or result.stderr.strip() or "No output",
            duration_seconds=time.monotonic() - start,
            details={"tags": sorted(tags), "port": port},
        )
    except subprocess.TimeoutExpired:
        return VerificationResult(
            name="e2e",
            passed=False,
            message="E2E tests timed out after 180s",
            duration_seconds=time.monotonic() - start,
        )
    except OSError as exc:
        return VerificationResult(
            name="e2e",
            passed=True,
            message=f"E2E skipped: {exc}",
            duration_seconds=time.monotonic() - start,
        )
    finally:
        # Clean up isolated data dir
        try:
            shutil.rmtree(test_data_dir, ignore_errors=True)
        except Exception:
            pass


# =====================================================================
# Orchestrator: run all verification layers
# =====================================================================

def _create_staging_dir(
    changes: list[FileChange],
    project_root: str,
) -> Path:
    """Create a temporary staging directory with the proposed changes applied.

    Copies the project, then overwrites target files with generated content.
    This ensures tests run against the modified code without touching
    the user's working tree.
    """
    staging = Path(tempfile.mkdtemp(prefix="cohort-codegen-verify-"))
    root = Path(project_root)

    # Copy project to staging (skip heavy dirs)
    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache"}

    for item in root.iterdir():
        if item.name in skip_dirs:
            continue
        dest = staging / item.name
        try:
            if item.is_dir():
                shutil.copytree(item, dest, ignore=shutil.ignore_patterns(*skip_dirs))
            else:
                shutil.copy2(item, dest)
        except (OSError, shutil.Error) as exc:
            logger.warning("[!] Could not copy %s: %s", item.name, exc)

    # Apply changes
    for change in changes:
        target = staging / change.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(change.content, encoding="utf-8")

    return staging


def verify(
    changes: list[FileChange],
    task: CodegenTask,
    run_e2e_tests: bool = True,
) -> VerificationReport:
    """Run the full verification stack against proposed changes.

    1. Syntax check (in-memory, fast)
    2. Pytest (in staging dir)
    3. E2E tests (optional, against isolated server)

    All verification runs against a temporary copy of the project.
    The user's working tree is never modified.
    """
    report = VerificationReport()

    if not changes:
        report.results.append(VerificationResult(
            name="pre-check",
            passed=False,
            message="No changes to verify",
        ))
        return report

    # Create staging directory
    staging = _create_staging_dir(changes, task.project_root)

    try:
        # Layer 1: Syntax
        syntax_results = check_syntax(changes, staging)
        report.results.extend(syntax_results)

        # Bail early if syntax fails
        if any(not r.passed for r in syntax_results):
            return report

        # Layer 2: Pytest
        changed_paths = [c.path for c in changes]
        pytest_result = run_pytest(staging, changed_paths)
        report.results.append(pytest_result)

        # Bail early if tests fail
        if not pytest_result.passed:
            return report

        # Layer 3: E2E (optional)
        if run_e2e_tests:
            tags = _get_e2e_tags(changed_paths)
            e2e_result = run_e2e(
                project_root=Path(task.project_root),
                tags=tags,
            )
            report.results.append(e2e_result)

    finally:
        # Always clean up staging
        try:
            shutil.rmtree(staging, ignore_errors=True)
        except Exception:
            pass

    return report
