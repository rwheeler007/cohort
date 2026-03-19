"""Top-level codegen pipeline.

Ties planner -> generator -> verifier into a single async-compatible
entry point.
"""

from __future__ import annotations

import logging
import time

from cohort.codegen.generator import generate as _generate
from cohort.codegen.models import CodegenResult, CodegenTask
from cohort.codegen.planner import plan as _plan
from cohort.codegen.verifier import verify as _verify

logger = logging.getLogger(__name__)


def generate(
    task: CodegenTask,
    *,
    run_e2e: bool = True,
    skip_verify: bool = False,
) -> CodegenResult:
    """Run the full codegen pipeline: plan -> generate -> verify.

    Args:
        task: The code generation task specification.
        run_e2e: Whether to run E2E tests (requires Playwright).
        skip_verify: Skip all verification (syntax, tests, E2E).
            Use only for debugging -- never in production.

    Returns:
        CodegenResult with changes and verification report.
        Changes are NOT applied to disk -- call result.apply()
        explicitly after reviewing.
    """
    pipeline_start = time.monotonic()

    # Phase 1: Plan
    logger.info("[>>] Phase 1: Planning (%s, %d targets)",
                task.task_type, len(task.target_files))
    code_plan = _plan(task)

    if not code_plan.is_valid:
        return CodegenResult(
            success=False,
            error=f"Planning failed: {'; '.join(code_plan.errors)}",
            project_root=task.project_root,
        )

    logger.info("[OK] Plan ready: %d target files, %d context files, %d chars",
                len(code_plan.target_contents),
                len(code_plan.context_contents),
                code_plan.total_context_chars)

    # Phase 2: Generate
    logger.info("[>>] Phase 2: Generating code (%s mode)", task.response_mode)
    gen_result = _generate(code_plan)

    if gen_result.error:
        return CodegenResult(
            success=False,
            error=gen_result.error,
            model=gen_result.model,
            tokens_in=gen_result.tokens_in,
            tokens_out=gen_result.tokens_out,
            elapsed_seconds=gen_result.elapsed_seconds,
            project_root=task.project_root,
        )

    logger.info("[OK] Generated %d file changes (model: %s, %d tokens in, %d out)",
                len(gen_result.changes), gen_result.model,
                gen_result.tokens_in, gen_result.tokens_out)

    # Phase 3: Verify
    if skip_verify:
        logger.info("[*] Verification skipped (skip_verify=True)")
        return CodegenResult(
            success=True,
            changes=gen_result.changes,
            model=gen_result.model,
            tokens_in=gen_result.tokens_in,
            tokens_out=gen_result.tokens_out,
            elapsed_seconds=time.monotonic() - pipeline_start,
            project_root=task.project_root,
        )

    logger.info("[>>] Phase 3: Verifying (e2e=%s)", run_e2e)
    report = _verify(
        changes=gen_result.changes,
        task=task,
        run_e2e_tests=run_e2e,
    )

    elapsed = time.monotonic() - pipeline_start
    success = report.passed

    if success:
        logger.info("[OK] All verification passed in %.1fs", elapsed)
    else:
        logger.warning("[X] Verification failed: %s", report.summary)

    return CodegenResult(
        success=success,
        changes=gen_result.changes,
        verification=report,
        error=None if success else report.summary,
        model=gen_result.model,
        tokens_in=gen_result.tokens_in,
        tokens_out=gen_result.tokens_out,
        elapsed_seconds=elapsed,
        project_root=task.project_root,
    )
