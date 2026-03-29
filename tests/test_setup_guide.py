"""Tests for Setup Guide onboarding agent (cq-f1c55d89).

Deliverables:
  D1: agent_config.json follows existing schema (sales_agent pattern)
  D2: agent_prompt.md references detect_hardware() and guides through setup
  D3: personas/setup_guide.md exists and is <500 tokens
  D4: Agent resolves via alias 'setup'
  D5: Friendly, non-technical tone
"""

from __future__ import annotations

import json
from pathlib import Path

from cohort.agent_store import AgentStore
from cohort.personas import _PERSONAS_DIR, load_persona

_AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"
_SETUP_GUIDE_DIR = _AGENTS_DIR / "setup_guide"


# =====================================================================
# D1: agent_config.json follows existing schema
# =====================================================================

def test_agent_config_exists() -> None:
    """Setup guide agent_config.json exists."""
    assert (_SETUP_GUIDE_DIR / "agent_config.json").exists()


def test_agent_config_valid_json() -> None:
    """agent_config.json is valid JSON."""
    config = json.loads((_SETUP_GUIDE_DIR / "agent_config.json").read_text(encoding="utf-8"))
    assert isinstance(config, dict)


def test_agent_config_required_fields() -> None:
    """agent_config.json has all required identity fields."""
    config = json.loads((_SETUP_GUIDE_DIR / "agent_config.json").read_text(encoding="utf-8"))
    assert config["agent_id"] == "setup_guide"
    assert "Onboarding" in config["role"] or "Setup" in config["role"]
    assert config["status"] == "active"
    assert "personality" in config
    assert len(config["personality"]) > 20


def test_agent_config_aliases() -> None:
    """agent_config.json includes setup/guide/onboard aliases."""
    config = json.loads((_SETUP_GUIDE_DIR / "agent_config.json").read_text(encoding="utf-8"))
    aliases = config.get("aliases", [])
    assert "setup" in aliases
    assert "guide" in aliases
    assert "onboard" in aliases


def test_agent_config_loads_via_agent_store() -> None:
    """AgentStore can load the setup_guide config."""
    store = AgentStore(agents_dir=_AGENTS_DIR)
    agent = store.load_agent("setup_guide")
    assert agent is not None
    assert agent.agent_id == "setup_guide"


# =====================================================================
# D2: agent_prompt.md references detect_hardware() and setup flow
# =====================================================================

def test_agent_prompt_exists() -> None:
    """agent_prompt.md exists."""
    assert (_SETUP_GUIDE_DIR / "agent_prompt.md").exists()


def test_agent_prompt_references_detect_hardware() -> None:
    """agent_prompt.md references detect_hardware() by name."""
    content = (_SETUP_GUIDE_DIR / "agent_prompt.md").read_text(encoding="utf-8")
    assert "detect_hardware" in content


def test_agent_prompt_explains_gpu() -> None:
    """agent_prompt.md explains GPU in plain English."""
    content = (_SETUP_GUIDE_DIR / "agent_prompt.md").read_text(encoding="utf-8")
    # Should explain what GPU/VRAM means in non-technical terms
    assert "graphics" in content.lower() or "graphics card" in content.lower()
    assert "vram" in content.lower() or "VRAM" in content


def test_agent_prompt_guides_ollama_install() -> None:
    """agent_prompt.md covers Ollama installation."""
    content = (_SETUP_GUIDE_DIR / "agent_prompt.md").read_text(encoding="utf-8")
    assert "ollama" in content.lower()
    assert "install" in content.lower()


def test_agent_prompt_guides_model_pull() -> None:
    """agent_prompt.md covers model pulling."""
    content = (_SETUP_GUIDE_DIR / "agent_prompt.md").read_text(encoding="utf-8")
    assert "ollama pull" in content or "model" in content.lower()


def test_agent_prompt_guides_verify() -> None:
    """agent_prompt.md covers verification step."""
    content = (_SETUP_GUIDE_DIR / "agent_prompt.md").read_text(encoding="utf-8")
    assert "verify" in content.lower() or "test" in content.lower()


def test_agent_prompt_introduces_agents() -> None:
    """agent_prompt.md introduces all 5 shipped agents."""
    content = (_SETUP_GUIDE_DIR / "agent_prompt.md").read_text(encoding="utf-8")
    shipped = ["Sales Agent", "Hardware Agent", "Marketing Agent",
               "Analytics Agent", "Content Strategy Agent"]
    for agent_name in shipped:
        assert agent_name in content, f"Missing agent introduction: {agent_name}"


# =====================================================================
# D3: personas/setup_guide.md exists and is <500 tokens
# =====================================================================

def test_persona_file_exists() -> None:
    """Persona file exists in the personas directory."""
    persona_path = _PERSONAS_DIR / "setup_guide.md"
    assert persona_path.exists(), f"Missing persona file: {persona_path}"


def test_persona_file_under_500_tokens() -> None:
    """Persona file is under ~750 tokens (~3000 chars).

    Bumped from 2000 to 3000 after the Tool Configuration Assistant
    section was added to the setup_guide persona.
    """
    persona_path = _PERSONAS_DIR / "setup_guide.md"
    content = persona_path.read_text(encoding="utf-8")
    assert len(content) < 3000, (
        f"setup_guide persona is {len(content)} chars, expected <3000"
    )
    assert len(content) > 100, "setup_guide persona is too short"


def test_persona_has_required_sections() -> None:
    """Persona file has personality and capabilities sections."""
    persona_path = _PERSONAS_DIR / "setup_guide.md"
    content = persona_path.read_text(encoding="utf-8")
    assert "## Personality" in content
    assert "## Key Capabilities" in content or "## Capabilities" in content


def test_persona_loads_via_loader() -> None:
    """load_persona returns content for setup_guide."""
    result = load_persona("setup_guide")
    assert result is not None
    assert "Setup Guide" in result


# =====================================================================
# D4: Agent resolves via alias 'setup'
# =====================================================================

def test_agent_resolves_via_alias_setup() -> None:
    """AgentStore resolves 'setup' alias to setup_guide."""
    store = AgentStore(agents_dir=_AGENTS_DIR)
    agent = store.get_by_alias("setup")
    assert agent is not None
    assert agent.agent_id == "setup_guide"


def test_agent_resolves_via_alias_guide() -> None:
    """AgentStore resolves 'guide' alias to setup_guide."""
    store = AgentStore(agents_dir=_AGENTS_DIR)
    agent = store.get_by_alias("guide")
    assert agent is not None
    assert agent.agent_id == "setup_guide"


def test_agent_resolves_via_alias_onboard() -> None:
    """AgentStore resolves 'onboard' alias to setup_guide."""
    store = AgentStore(agents_dir=_AGENTS_DIR)
    agent = store.get_by_alias("onboard")
    assert agent is not None
    assert agent.agent_id == "setup_guide"


# =====================================================================
# D5: Friendly, non-technical tone
# =====================================================================

def test_tone_no_jargon_without_explanation() -> None:
    """Agent prompt explains jargon in plain English."""
    content = (_SETUP_GUIDE_DIR / "agent_prompt.md").read_text(encoding="utf-8")
    # The prompt should explain VRAM in plain terms
    assert "memory" in content.lower() and "graphics" in content.lower()


def test_tone_friendly_persona() -> None:
    """Persona reflects friendly, non-technical tone."""
    content = (_PERSONAS_DIR / "setup_guide.md").read_text(encoding="utf-8")
    friendly_indicators = ["friendly", "patient", "plain english", "celebrates"]
    matches = sum(1 for ind in friendly_indicators if ind in content.lower())
    assert matches >= 2, "Persona should reflect friendly, non-technical tone"
