# Cohort

Multi-agent orchestration with loop prevention and contribution scoring.

Cohort manages structured discussions between AI agents. It decides **who speaks next**, prevents conversational loops, and scores contributions across five dimensions -- so your agents stay productive instead of talking in circles.

## Features

- **Zero dependencies** -- `pip install cohort` pulls nothing
- **Contribution scoring** -- 5-dimension relevance matrix (novelty, expertise, ownership, phase alignment, data ownership)
- **Loop prevention** -- Stakeholder gating keeps agents from repeating each other
- **Topic shift detection** -- Automatically re-engages relevant agents when the conversation changes direction
- **Protocol-first design** -- `AgentProfile` and `StorageBackend` are `@runtime_checkable` protocols. Bring your own agents and storage.
- **Optional HTTP server** -- `pip install cohort[server]` adds a REST API + Socket.IO for multi-process agent communication
- **Optional MCP bridge** -- `pip install cohort[claude]` connects Claude Code agents via MCP

## Install

```bash
pip install cohort              # zero-dep core library
pip install cohort[server]      # adds HTTP server (starlette + uvicorn)
pip install cohort[claude]      # adds MCP bridge for Claude Code
pip install cohort[all]         # everything
```

Requires Python 3.11+.

## Quick Start

```python
from cohort import JsonFileStorage, Orchestrator
from cohort.chat import ChatManager

# 1. Set up storage and chat
chat = ChatManager(JsonFileStorage("my_data"))
chat.create_channel("design-review", "API design review")

# 2. Define your agents (triggers and capabilities drive scoring)
agents = {
    "architect": {"triggers": ["api", "design"], "capabilities": ["backend architecture"]},
    "tester":    {"triggers": ["testing", "qa"], "capabilities": ["test strategy"]},
}

# 3. Start a session -- Cohort picks the right speakers
orch = Orchestrator(chat, agents=agents)
session = orch.start_session("design-review", "REST API design review")

# 4. Ask who should speak next
rec = orch.get_next_speaker(session.session_id)
print(f"Next speaker: {rec['recommended_speaker']}")
print(f"Reason: {rec['reason']}")

# 5. Record turns and let Cohort manage the flow
orch.record_turn(session.session_id, "architect", "msg-001")
```

## Architecture

```
                    +-----------------+
                    |  Orchestrator   |  Session management, turn control
                    +--------+--------+
                             |
              +--------------+--------------+
              |                             |
     +--------+--------+          +--------+--------+
     |   ChatManager    |          |    Meeting      |
     | channels, messages|          | scoring, gating |
     +--------+--------+          +-----------------+
              |
     +--------+--------+
     | StorageBackend   |  (Protocol -- bring your own)
     |  JsonFileStorage |  (default: flat-file JSON)
     +-----------------+
```

**Orchestrator** manages sessions: who's invited, whose turn it is, when the topic shifts. It delegates message storage to **ChatManager** and contribution decisions to the **Meeting** engine.

**Meeting** scores each agent across five dimensions before allowing them to speak:

| Dimension | Weight | What it measures |
|-----------|--------|-----------------|
| Novelty | 35% | Is this new information vs. what's been said? |
| Expertise | 30% | Do the agent's capabilities match the topic? |
| Ownership | 20% | Is this agent a primary stakeholder? |
| Question | 15% | Was the agent directly asked something? |

Agents move through stakeholder statuses: **active** -> **approved_silent** -> **observer** -> **dormant**. Each status has a higher threshold to speak, preventing agents from dominating after they've contributed.

### Composite Relevance (5-Dimension Matrix)

When using `calculate_composite_relevance` or `Orchestrator.get_next_speaker`, Cohort scores agents across a richer five-dimension matrix:

| Dimension | Weight | What it measures |
|-----------|--------|-----------------|
| Domain expertise | 30% | Keyword overlap between topic and agent triggers/capabilities |
| Complementary value | 25% | Are this agent's complementary partners active? |
| Historical success | 20% | Past performance on similar topics |
| Phase alignment | 15% | Is this the right agent for the current workflow phase? |
| Data ownership | 10% | Does this agent own data relevant to the topic? |

Three of these dimensions -- complementary value, phase alignment, and data ownership -- use **per-agent scoring metadata** that can be configured externally.

### Scoring Metadata

By default, Cohort includes built-in scoring metadata for common agent roles. You can override or extend this by adding three optional fields to any agent's config:

```json
{
  "architect": {
    "triggers": ["api", "design"],
    "capabilities": ["backend architecture"],
    "complementary_agents": ["tester", "developer"],
    "data_sources": ["architecture_docs", "design_decisions"],
    "phase_roles": {"PLAN": "high", "DISCOVER": "medium"}
  }
}
```

| Field | Type | Effect |
|-------|------|--------|
| `complementary_agents` | `list[str]` | Agent IDs that work well with this agent. Boosts score when partners are active. |
| `data_sources` | `list[str]` | Data this agent owns. Boosts score when topic keywords match. |
| `phase_roles` | `dict[str, str]` | Workflow phase relevance. Keys: `DISCOVER`, `PLAN`, `EXECUTE`, `VALIDATE`. Values: `high` (1.0), `medium` (0.6), `low` (0.2). |

**Resolution order** (3-tier fallback):

1. Explicit value in agent config (from `agents.json` or `AgentConfig`)
2. Built-in defaults (for known agent roles like `python_developer`, `boss_agent`, etc.)
3. Neutral fallback (unknown agents get 0.0 for complementary/ownership, 0.5 for phase alignment)

This means zero-config works out of the box -- agents with no scoring metadata still participate with neutral scores.

## CLI

Cohort includes CLI commands for file-based collaboration. Agents in **any language** can participate by appending to a shared `.jsonl` file, with Cohort acting as the scoring referee.

```bash
# Append a message
python -m cohort say --sender architect --channel review --file conv.jsonl \
    --message "We should use pagination for the list endpoint"

# Check if an agent should respond (exit 0 = speak, exit 1 = don't)
python -m cohort gate --agent tester --channel review --file conv.jsonl \
    --agents agents.json --format json

# Rank who should speak next
python -m cohort next-speaker --channel review --file conv.jsonl \
    --agents agents.json --top 3
```

The `agents.json` file defines agent capabilities (and optional scoring metadata):

```json
{
  "architect": {"triggers": ["api", "design"], "capabilities": ["backend architecture"]},
  "tester": {"triggers": ["testing", "qa"], "capabilities": ["test strategy"]},
  "developer": {"triggers": ["python", "code"], "capabilities": ["implementation"]}
}
```

### JSONL File Transport

For programmatic use, `JsonlFileStorage` provides an append-only storage backend:

```python
from cohort import JsonlFileStorage
from cohort.chat import ChatManager

storage = JsonlFileStorage("conversation.jsonl")
chat = ChatManager(storage)
chat.create_channel("review", "API design review")
chat.post_message("review", "architect", "Hello!")
```

Messages are stored as one JSON object per line. Channel metadata lives in a companion `{stem}_channels.json` file. Zero external dependencies.

## HTTP Server

```bash
pip install cohort[server]
python -m cohort serve --port 5100
```

Endpoints:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness probe |
| GET | `/api/channels` | List channels |
| GET | `/api/messages?channel=X&limit=N` | Fetch messages |
| POST | `/api/send` | Post a message `{channel, sender, message}` |
| POST | `/api/channels/{id}/condense` | Trim old messages `{keep_last}` |

## Protocols

Cohort uses Python protocols -- no base classes required:

```python
from cohort import AgentProfile, StorageBackend

# Any class with these attributes/methods works
class MyAgent:
    name: str = "custom"
    role: str = "specialist"
    capabilities: list[str] = ["custom-domain"]

    def relevance_score(self, topic: str) -> float:
        return 0.8 if "custom" in topic else 0.2

    def can_contribute(self, context: dict) -> bool:
        return True

assert isinstance(MyAgent(), AgentProfile)  # True -- duck typing
```

## Development

```bash
git clone https://github.com/your-org/cohort.git
cd cohort
pip install -e ".[dev]"
pytest
ruff check cohort/ tests/
mypy cohort/
```

## License

MIT
