"""Persona file loader for Cohort agents.

Loads lightweight persona files (~25-35 lines, <500 tokens) as the
default system prompt, with fallback to full agent_prompt.md for
heavy/thinking mode.

Fallback chain:
  1. agent_persona.md in personas/ directory
  2. First 1000 chars of agent_prompt.md (truncated fallback)
  3. One-line identity string from AgentConfig

Every loaded persona gets an ecosystem awareness footer appended so
agents understand their place in the multi-agent system regardless of
which code path loads them (agent_router, compiled_roundtable, MCP, etc.).
"""

from __future__ import annotations

import os
from pathlib import Path

_PERSONAS_DIR = Path(__file__).parent

# Appended to every persona so agents understand the system they operate in.
# Kept short (~120 words) to stay within lightweight persona token budgets.
_ECOSYSTEM_FOOTER = """
## System Context

You are one agent in **Cohort**, a multi-agent team chat. You are not working alone.

**What this gives you:**
- **@mention** any agent by ID (e.g. @python_developer, @security_agent) to pull them into the conversation. Mentions are routed automatically.
- **Escalate** to @coding_orchestrator for workflow orchestration when you are stuck or a task crosses domain boundaries.
- **Specialist handoff** -- if a question falls outside your expertise, tag the right specialist rather than guessing.
- **Code work** flows through the code queue, managed by @coding_orchestrator. Flag implementation needs to them rather than writing code yourself unless that is your role.

Stay in your lane, collaborate through mentions, and trust the system to route.
""".lstrip()


def load_persona(agent_id: str) -> str | None:
    """Load a persona file for the given agent_id.

    Returns the persona text with ecosystem footer appended,
    or None if no persona file exists.
    Rejects agent_id values containing path traversal characters.
    """
    # D7: Path traversal sanitization
    if not agent_id or agent_id != os.path.basename(agent_id):
        return None
    if ".." in agent_id or "/" in agent_id or "\\" in agent_id:
        return None

    persona_path = _PERSONAS_DIR / f"{agent_id}.md"
    if not persona_path.exists():
        return None
    try:
        persona_text = persona_path.read_text(encoding="utf-8")
        return f"{persona_text}\n{_ECOSYSTEM_FOOTER}"
    except OSError:
        return None
