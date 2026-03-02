"""Persona file loader for Cohort agents.

Loads lightweight persona files (~25-35 lines, <500 tokens) as the
default system prompt, with fallback to full agent_prompt.md for
heavy/thinking mode.

Fallback chain:
  1. agent_persona.md in personas/ directory
  2. First 1000 chars of agent_prompt.md (truncated fallback)
  3. One-line identity string from AgentConfig
"""

from __future__ import annotations

import os
from pathlib import Path

_PERSONAS_DIR = Path(__file__).parent


def load_persona(agent_id: str) -> str | None:
    """Load a persona file for the given agent_id.

    Returns the persona text, or None if no persona file exists.
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
        return persona_path.read_text(encoding="utf-8")
    except OSError:
        return None
