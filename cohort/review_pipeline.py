"""Multi-Stage Review Pipeline for Cohort.

Configurable multi-stakeholder review orchestration with majority voting.
Extracted and generalized from BOSS's 3-layer review pipeline
(CEO checklist gate + Code quality review + Orchestrator review).

The pipeline is LLM-agnostic: callers provide a ``reviewer_fn`` callback
that handles the actual inference (Ollama, Claude API, compiled roundtable, etc.).

Usage::

    pipeline = ReviewPipeline(stages=[
        ReviewStage(role="deliverables_gate", agent_id="qa_agent", ...),
        ReviewStage(role="quality_review", agent_id="security_agent", ...),
        ReviewStage(role="architecture_review", agent_id="coding_orchestrator", ...),
    ])

    results = pipeline.run_reviews(
        task_context={"description": "...", "deliverables": [...], "code": "..."},
        reviewer_fn=my_llm_call,
    )
    verdict = pipeline.evaluate_verdict(results)

Storage: ``{data_dir}/review_pipeline_config.json``
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# =====================================================================
# Data models
# =====================================================================

class PipelineVerdict(str, Enum):
    """Outcome of a full pipeline evaluation."""
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_WORK = "needs_work"
    INCOMPLETE = "incomplete"  # Not enough reviews completed


@dataclass
class ReviewStage:
    """Configuration for a single review stage."""

    role: str                    # e.g. "deliverables_gate", "quality_review"
    agent_id: str                # Cohort agent to use
    description: str             # Human-readable purpose
    required: bool = True        # If False, failure is logged but doesn't block
    system_prompt: str = ""
    review_prompt_template: str = ""  # Template with {description}, {deliverables}, {code}, etc.

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ReviewStage:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class ReviewResult:
    """Result of a single review stage."""

    stage_role: str
    agent_id: str
    verdict: str                 # "approve" | "reject" | "needs_work"
    summary: str = ""
    deliverables_checklist: List[Dict[str, Any]] = field(default_factory=list)
    issues: List[Dict[str, Any]] = field(default_factory=list)
    rejection_feedback: str = ""
    defer_items: List[str] = field(default_factory=list)
    timestamp: str = ""
    model_used: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ReviewResult:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


# =====================================================================
# Review response parsing
# =====================================================================

def parse_review_response(text: str, agent_id: str, model_used: str = "") -> Optional[ReviewResult]:
    """Parse an LLM review response (JSON, possibly wrapped in markdown fences).

    Handles:
    - Plain JSON
    - JSON wrapped in ```json ... ``` fences
    - JSON embedded in surrounding text

    Returns ``None`` if no valid JSON found.
    """
    clean = text.strip()

    # Strip markdown code fences
    if clean.startswith("```"):
        clean = re.sub(r"^```\w*\n?", "", clean)
        clean = re.sub(r"\n?```$", "", clean)
        clean = clean.strip()

    # Try direct parse
    data = _try_parse_json(clean)

    # Fallback: find first JSON object in text
    if data is None:
        match = re.search(r"\{[\s\S]*\}", clean)
        if match:
            data = _try_parse_json(match.group())

    if data is None:
        logger.warning("[!] %s review: could not parse JSON response", agent_id)
        return None

    return ReviewResult(
        stage_role=data.get("stage_role", ""),
        agent_id=agent_id,
        verdict=data.get("verdict", "needs_work"),
        summary=data.get("summary", ""),
        deliverables_checklist=data.get("deliverables_checklist", []),
        issues=data.get("issues", []),
        rejection_feedback=data.get("rejection_feedback", ""),
        defer_items=data.get("defer_items", []),
        timestamp=datetime.now(timezone.utc).isoformat(),
        model_used=model_used,
    )


def _try_parse_json(text: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


# =====================================================================
# Default prompt templates
# =====================================================================

DELIVERABLES_GATE_TEMPLATE = (
    "Review the following generated code against its acceptance criteria.\n\n"
    "TASK DESCRIPTION:\n{description}\n\n"
    "DELIVERABLES (acceptance criteria):\n{deliverables}\n\n"
    "GENERATED CODE:\n{code}\n\n"
    "SELF-REVIEW: {self_review}\n\n"
    "For each deliverable, determine if the code satisfies it.\n"
    "Respond with JSON ONLY:\n"
    '{{\n'
    '  "verdict": "approve|reject",\n'
    '  "summary": "1 paragraph - does this deliver what was asked for?",\n'
    '  "deliverables_checklist": [{{"id": "D1", "status": "pass|fail", "note": "brief"}}],\n'
    '  "defer_items": ["anything worth improving later but not blocking"]\n'
    '}}'
)

QUALITY_REVIEW_TEMPLATE = (
    "Review the following generated code for bugs, security issues, and correctness.\n\n"
    "TASK DESCRIPTION:\n{description}\n\n"
    "GENERATED CODE:\n{code}\n\n"
    "Focus on:\n"
    "- Real bugs (incorrect logic, wrong API usage, missing imports)\n"
    "- Security issues (injection, path traversal, SSRF)\n"
    "- Runtime crashes (unhandled exceptions, type errors)\n"
    "- Missing error handling for critical paths\n\n"
    "Do NOT nitpick style, naming, or minor improvements.\n"
    "Respond with JSON ONLY:\n"
    '{{\n'
    '  "verdict": "approve|reject|needs_work",\n'
    '  "summary": "1 paragraph technical assessment",\n'
    '  "issues": [{{"severity": "critical|warning", "file": "path", "description": "..."}}],\n'
    '  "rejection_feedback": "If rejecting: specific instructions for fixing. Empty string if approving."\n'
    '}}'
)

ARCHITECTURE_REVIEW_TEMPLATE = (
    "Review this auto-generated code for a programming task.\n\n"
    "TASK:\n{description}\n\n"
    "CODE:\n{code}\n\n"
    "Check for:\n"
    "- Real bugs (incorrect logic, wrong API usage, missing imports)\n"
    "- Security issues (injection, SSRF, path traversal)\n"
    "- Missing error handling for critical paths\n"
    "- Code that will crash at runtime\n\n"
    "Do NOT nitpick style, naming, or minor improvements.\n"
    "Respond with JSON ONLY:\n"
    '{{\n'
    '  "verdict": "approve|reject|needs_work",\n'
    '  "summary": "1 paragraph technical assessment",\n'
    '  "issues": [{{"severity": "critical|warning", "line": 0, "description": "..."}}],\n'
    '  "rejection_feedback": "If rejecting: specific instructions for fixing. Empty string if approving."\n'
    '}}'
)


# =====================================================================
# Default stage configurations
# =====================================================================

def default_stages() -> List[ReviewStage]:
    """Return the default 3-stage review pipeline configuration."""
    return [
        ReviewStage(
            role="deliverables_gate",
            agent_id="qa_agent",
            description="Mechanical deliverables checklist gate",
            required=True,
            system_prompt=(
                "You are a QA agent reviewing code against acceptance criteria.\n"
                "Check each deliverable pass/fail. Approve if requirements are met.\n"
                "Reject ONLY if critical deliverables are missing or broken.\n"
                "Respond with JSON only."
            ),
            review_prompt_template=DELIVERABLES_GATE_TEMPLATE,
        ),
        ReviewStage(
            role="quality_review",
            agent_id="security_agent",
            description="Deep bug and security analysis",
            required=True,
            system_prompt=(
                "You are a senior code reviewer. Find real bugs and security issues.\n"
                "Approve if the code is correct and safe. Reject only for real problems.\n"
                "Respond with JSON only."
            ),
            review_prompt_template=QUALITY_REVIEW_TEMPLATE,
        ),
        ReviewStage(
            role="architecture_review",
            agent_id="coding_orchestrator",
            description="Final architectural quality check",
            required=False,
            system_prompt="You are an expert code reviewer. Respond ONLY with valid JSON.",
            review_prompt_template=ARCHITECTURE_REVIEW_TEMPLATE,
        ),
    ]


# =====================================================================
# ReviewPipeline
# =====================================================================

class ReviewPipeline:
    """Multi-stage review pipeline with configurable stages and majority voting.

    Parameters
    ----------
    stages:
        Ordered list of review stages to execute.
    majority_threshold:
        Minimum number of rejections required to auto-reject.
        Default is 2 (2/3 majority -- single-reviewer hallucinations
        were the #1 cause of false rejections in BOSS).
    """

    def __init__(
        self,
        stages: Optional[List[ReviewStage]] = None,
        majority_threshold: int = 2,
    ) -> None:
        self.stages = stages or default_stages()
        self.majority_threshold = majority_threshold

    def run_reviews(
        self,
        task_context: Dict[str, Any],
        reviewer_fn: Callable[[ReviewStage, str, str], Optional[str]],
    ) -> List[ReviewResult]:
        """Execute all review stages sequentially.

        Parameters
        ----------
        task_context:
            Dict with keys used in prompt templates:
            ``description``, ``deliverables``, ``code``,
            ``self_review`` (optional).
        reviewer_fn:
            Callback ``(stage, system_prompt, user_prompt) -> response_text``.
            Must return the LLM's text response, or ``None`` on failure.
            The callback is responsible for LLM routing (model selection,
            temperature, etc.).

        Returns
        -------
        List of ``ReviewResult`` for stages that produced a parseable response.
        """
        results: List[ReviewResult] = []

        for stage in self.stages:
            try:
                # Build prompt from template
                template = stage.review_prompt_template
                prompt = template.format(
                    description=task_context.get("description", "(no description)"),
                    deliverables=task_context.get("deliverables", "(no deliverables)"),
                    code=task_context.get("code", "(no code)"),
                    self_review=task_context.get("self_review", "(no self-review)"),
                )

                # Call the pluggable LLM backend
                response_text = reviewer_fn(stage, stage.system_prompt, prompt)

                if response_text is None:
                    logger.warning(
                        "[!] %s review (%s): reviewer_fn returned None",
                        stage.role, stage.agent_id,
                    )
                    continue

                # Parse the response
                result = parse_review_response(
                    response_text, stage.agent_id,
                    model_used=f"via_{stage.agent_id}",
                )

                if result is not None:
                    result.stage_role = stage.role
                    results.append(result)
                    logger.info(
                        "[OK] %s review by %s: %s",
                        stage.role, stage.agent_id, result.verdict,
                    )

            except Exception as exc:
                logger.warning(
                    "[!] %s review failed (non-fatal): %s",
                    stage.role, exc,
                )

        return results

    def evaluate_verdict(self, reviews: List[ReviewResult]) -> PipelineVerdict:
        """Evaluate the overall pipeline verdict from individual reviews.

        Logic (from BOSS, proven in production):
        - If fewer than ``majority_threshold`` reviews completed: INCOMPLETE
        - If ``majority_threshold`` or more reject/needs_work: REJECTED
        - If exactly 1 dissent: APPROVED (single-reviewer override, logged)
        - If all approve: APPROVED

        Returns
        -------
        PipelineVerdict
        """
        if len(reviews) < self.majority_threshold:
            return PipelineVerdict.INCOMPLETE

        rejections = [
            r for r in reviews
            if r.verdict in ("reject", "needs_work")
        ]

        if len(rejections) >= self.majority_threshold:
            return PipelineVerdict.REJECTED

        if len(rejections) == 1:
            r = rejections[0]
            logger.info(
                "[*] Single reviewer dissent from %s (verdict=%s), "
                "overridden by majority",
                r.agent_id, r.verdict,
            )

        return PipelineVerdict.APPROVED

    def collect_rejection_feedback(self, reviews: List[ReviewResult]) -> str:
        """Combine rejection feedback from all rejecting reviewers.

        Format: ``[agent_id] feedback`` per rejector, newline-separated.
        """
        parts: List[str] = []
        for r in reviews:
            if r.verdict in ("reject", "needs_work"):
                fb = r.rejection_feedback or r.summary or "(no details)"
                parts.append(f"[{r.agent_id}] {fb}")
        return "\n".join(parts) if parts else "No feedback provided"

    def to_dict(self) -> Dict[str, Any]:
        """Serialize pipeline configuration."""
        return {
            "stages": [s.to_dict() for s in self.stages],
            "majority_threshold": self.majority_threshold,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ReviewPipeline:
        """Deserialize pipeline configuration."""
        stages = [ReviewStage.from_dict(s) for s in data.get("stages", [])]
        return cls(
            stages=stages,
            majority_threshold=data.get("majority_threshold", 2),
        )

    def save_config(self, data_dir: Path) -> None:
        """Save pipeline config to ``{data_dir}/review_pipeline_config.json``."""
        path = data_dir / "review_pipeline_config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @classmethod
    def load_config(cls, data_dir: Path) -> ReviewPipeline:
        """Load pipeline config, falling back to defaults if not found."""
        path = data_dir / "review_pipeline_config.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return cls.from_dict(data)
            except Exception as exc:
                logger.warning("[!] Failed to load pipeline config: %s", exc)
        return cls()
