"""Briefing prompt builder and confirmation parser for Cohort task intake.

Handles the conversational pre-flight between user and agent before
task execution begins.  The agent drives the conversation naturally,
gathering what it needs based on its own capabilities and the task
description.  When ready, it posts a structured confirmation block
that gets parsed and presented with an Execute button.
"""

from __future__ import annotations

import re
from typing import Any

# =====================================================================
# Briefing directive (injected into agent's system prompt)
# =====================================================================

BRIEFING_DIRECTIVE = """\
You are beginning a new task assignment.  Your job during this briefing is
to establish the TRIGGER-ACTION-OUTCOME triad -- the three things every
task must have before execution can begin:

1. TRIGGER -- already set (manual, scheduled, etc.).  No action needed.
2. ACTION -- What specific tool, script, or action will you execute?
   Every task must DO something concrete.  "Think about it" is not an action.
   Examples: run a script, call an API, generate a report, scan files.
3. OUTCOME -- What artifact or state change will this task produce, and
   how will we know it succeeded?  Examples: "HTML report at data/reports/",
   "database updated with new records", "security scan with 0 critical issues".

Have a natural conversation to fill in any gaps.  You are a skilled
professional clarifying a work assignment -- not a bot filling out a form.
If the brief already specifies the action and outcome, say so and move
straight to your confirmation.

When you have enough context, post a confirmation block using this exact
format (the delimiters must appear on their own lines):

---TASK_CONFIRMED---
Goal: <one-line goal>
Approach: <how you'll do it>
Scope: <files/areas involved>
Acceptance: <how we'll know it's done>
Tool: <the specific tool, script, or action to execute>
Outcome: <what concrete artifact or result will be produced>
---END_CONFIRMED---

IMPORTANT: The Tool and Outcome fields are NOT optional.  If you cannot
determine a concrete action and verifiable outcome from the conversation,
ask the user directly before posting your confirmation.

Then wait for the user to approve before proceeding.
"""


# =====================================================================
# Prompt builders
# =====================================================================

def build_briefing_prompt(
    agent_prompt: str,
    task: dict[str, Any],
    channel_context: str = "",
) -> str:
    """Construct the full prompt for a briefing-mode invocation.

    Parameters
    ----------
    agent_prompt:
        Raw contents of the agent's ``agent_prompt.md``.
    task:
        Task dict from the data layer (agent_id, description, priority, ...).
    channel_context:
        Recent channel messages for additional context (optional).
    """
    agent_id = task.get("agent_id", "agent")
    description = task.get("description", "")
    priority = task.get("priority", "medium")

    # Surface any pre-filled triad fields from the task
    action = task.get("action") or {}
    outcome = task.get("outcome") or {}
    triad_lines = []
    if action.get("tool"):
        ref = action.get("tool_ref")
        ref_note = f" ({ref})" if ref else ""
        triad_lines.append(f"Action: {action['tool']}{ref_note}")
    if outcome.get("success_criteria"):
        triad_lines.append(f"Expected Outcome: {outcome['success_criteria']}")

    brief_block = (
        f"\n=== TASK BRIEF ===\n"
        f"Priority: {priority}\n"
        f"Description: {description}\n"
    )
    if triad_lines:
        brief_block += "\n".join(triad_lines) + "\n"
    brief_block += "=== END TASK BRIEF ===\n"

    parts = [
        f"You are responding as the {agent_id} agent.\n",
        "Follow this agent prompt exactly:\n",
        f"---\n{agent_prompt}\n---\n",
        BRIEFING_DIRECTIVE,
        brief_block,
    ]

    if channel_context:
        parts.append(channel_context)

    parts.append(
        "Now begin the briefing conversation.  "
        "Review the brief and respond naturally."
    )

    return "\n".join(parts)


def build_execution_prompt(
    agent_prompt: str,
    task: dict[str, Any],
    confirmed_brief: dict[str, Any],
    channel_context: str = "",
) -> str:
    """Construct the execution prompt with the full briefing baked in.

    Called after the user has confirmed the task -- the agent now has
    all the structured context it needs to execute.
    """
    agent_id = task.get("agent_id", "agent")
    description = task.get("description", "")

    brief_lines = "\n".join(
        f"- {key}: {value}" for key, value in confirmed_brief.items() if value
    )

    # Inject action/outcome constraints if available on the task
    action = task.get("action") or {}
    outcome = task.get("outcome") or {}
    constraints = []

    tool_name = action.get("tool")
    if tool_name:
        tool_ref = action.get("tool_ref")
        ref_note = f" ({tool_ref})" if tool_ref else ""
        constraints.append(f"REQUIRED TOOL: You MUST use {tool_name}{ref_note}.")

    success_criteria = outcome.get("success_criteria")
    if success_criteria:
        constraints.append(
            f"SUCCESS CRITERIA: {success_criteria}\n"
            f"Your output MUST include a reference to the artifact produced "
            f"(file path, URL, or description of the state change)."
        )

    constraint_block = ""
    if constraints:
        constraint_block = (
            "\n=== EXECUTION CONSTRAINTS ===\n"
            + "\n".join(constraints)
            + "\n=== END CONSTRAINTS ===\n\n"
        )

    # Channel discussion context from enrichment (if available)
    enriched_context = confirmed_brief.get("channel_context", "")
    enriched_block = ""
    if enriched_context:
        enriched_block = (
            f"\n## Channel Discussion Context\n{enriched_context}\n\n"
        )

    return (
        f"You are the {agent_id} agent executing an approved task.\n\n"
        f"Follow this agent prompt exactly:\n"
        f"---\n{agent_prompt}\n---\n\n"
        f"RESPONSE LENGTH: Be thorough but focused.  Provide the actual "
        f"work product, not a description of what you would do.\n\n"
        f"=== CONFIRMED TASK ===\n"
        f"Original request: {description}\n\n"
        f"Agreed plan:\n{brief_lines}\n"
        f"=== END CONFIRMED TASK ===\n\n"
        f"{constraint_block}"
        f"{enriched_block}"
        f"{channel_context}"
        f"Now execute the task.  Produce the deliverable."
    )


# =====================================================================
# Confirmation parser
# =====================================================================

_CONFIRMED_RE = re.compile(
    r"---TASK_CONFIRMED---\s*\n(.*?)\n\s*---END_CONFIRMED---",
    re.DOTALL,
)

_FIELD_RE = re.compile(
    r"^(Goal|Approach|Scope|Acceptance|Tool|Outcome)\s*:\s*(.+)", re.MULTILINE,
)


def parse_confirmation(message_content: str) -> dict[str, str] | None:
    """Extract a structured task confirmation from an agent's message.

    Returns a dict with keys ``goal``, ``approach``, ``scope``,
    ``acceptance``, and optionally ``tool`` and ``outcome``
    if a valid block is found, otherwise ``None``.
    """
    match = _CONFIRMED_RE.search(message_content)
    if not match:
        return None

    block = match.group(1)
    fields: dict[str, str] = {}

    for field_match in _FIELD_RE.finditer(block):
        key = field_match.group(1).lower()
        value = field_match.group(2).strip()
        fields[key] = value

    # Require at least goal to be present
    if "goal" not in fields:
        return None

    return fields


def extract_triad_from_brief(
    confirmed_brief: dict[str, str],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Extract action and outcome dicts from a parsed confirmation brief.

    Returns (action_dict, outcome_dict). Either may be None if the
    corresponding field wasn't in the confirmation block.
    """
    action = None
    outcome = None

    tool_text = confirmed_brief.get("tool")
    if tool_text:
        action = {"tool": tool_text, "tool_ref": None, "parameters": {}}

    outcome_text = confirmed_brief.get("outcome")
    acceptance_text = confirmed_brief.get("acceptance")
    if outcome_text or acceptance_text:
        outcome = {
            "type": _infer_outcome_type(outcome_text or ""),
            "success_criteria": acceptance_text or outcome_text,
            "artifact_ref": None,
            "verified": False,
        }

    return action, outcome


def _infer_outcome_type(outcome_text: str) -> str:
    """Infer the outcome type from free-text description."""
    text_lower = outcome_text.lower()
    if any(w in text_lower for w in ("report", "summary", "briefing", "digest")):
        return "report"
    if any(w in text_lower for w in ("file", "document", "pdf", "html", "csv")):
        return "artifact"
    if any(w in text_lower for w in ("update", "change", "modify", "set", "toggle")):
        return "state_change"
    if any(w in text_lower for w in ("notify", "alert", "email", "message", "post")):
        return "notification"
    if any(w in text_lower for w in ("analyze", "scan", "audit", "review", "assess")):
        return "analysis"
    return "artifact"  # default
