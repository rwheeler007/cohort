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
You are beginning a new task assignment. Review the task brief and have a
natural conversation with the user to understand what's needed.  You are
a skilled professional clarifying a work assignment -- not a bot filling
out a form.

Before you can begin work, make sure you understand:
- The specific goal and expected outcome
- Any constraints or preferences on approach
- Which files or systems are involved (if applicable)
- How we'll know the task is done (acceptance criteria)

Ask about these naturally based on what's missing from the brief.  If the
brief is already clear and complete, say so and move straight to your
confirmation.

When you have enough context, post a confirmation block using this exact
format (the delimiters must appear on their own lines):

---TASK_CONFIRMED---
Goal: <one-line goal>
Approach: <how you'll do it>
Scope: <files/areas involved>
Acceptance: <how we'll know it's done>
---END_CONFIRMED---

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

    parts = [
        f"You are responding as the {agent_id} agent.\n",
        "Follow this agent prompt exactly:\n",
        f"---\n{agent_prompt}\n---\n",
        BRIEFING_DIRECTIVE,
        f"\n=== TASK BRIEF ===\n"
        f"Priority: {priority}\n"
        f"Description: {description}\n"
        f"=== END TASK BRIEF ===\n",
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

_FIELD_RE = re.compile(r"^(Goal|Approach|Scope|Acceptance)\s*:\s*(.+)", re.MULTILINE)


def parse_confirmation(message_content: str) -> dict[str, str] | None:
    """Extract a structured task confirmation from an agent's message.

    Returns a dict with keys ``goal``, ``approach``, ``scope``,
    ``acceptance`` if a valid block is found, otherwise ``None``.
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
