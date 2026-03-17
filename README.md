# Cohort

Multi-agent orchestration with loop prevention, contribution scoring, and MCP integration.

Cohort manages structured discussions between AI agents. It decides **who speaks next**, prevents conversational loops, and scores contributions across five dimensions -- so your agents stay productive instead of talking in circles.

## Why Cohort?

Every multi-agent framework lets agents talk. Cohort decides **who should talk, when they should stop, and whether what they said was worth hearing.**

| | Cohort | CrewAI | LangGraph |
| --- | --- | --- | --- |
| **Core deps** | 0 | 25+ | 30+ (transitive) |
| **Default inference** | Local (Ollama / llama.cpp) | Cloud (OpenAI) | Cloud (varies) |
| **API key required** | No (local) / optional (cloud) | Yes | Yes |
| **Contribution scoring** | 5-dimension engine | -- | -- |
| **Loop prevention** | Architectural (recency + novelty + gating) | Max iterations (timeout) | Conditional edges (manual) |
| **Data leaves your machine** | Your choice (local default) | Yes + telemetry | Optional |
| **MCP integration** | Built-in (lite + full modes) | -- | -- |
| **Cost** | $0 local | API + $99-$10K Enterprise | API + $39/seat Platform |

**Zero dependencies.** `pip install cohort` pulls nothing. Your agents, your hardware, your data.

**Extracted from production.** These patterns weren't designed from theory -- they were extracted from a system running 60+ agents, then packaged clean with 785+ tests.

## Features

- **Zero dependencies** -- `pip install cohort` pulls nothing
- **MCP integration** -- Claude Code tools for channels, agents, search, checklists (works standalone or with server)
- **Contribution scoring** -- 5-dimension relevance matrix (novelty, expertise, ownership, phase alignment, data ownership)
- **Loop prevention** -- Stakeholder gating keeps agents from repeating each other
- **Topic shift detection** -- Automatically re-engages relevant agents when the conversation changes direction
- **Local LLM** -- Hardware detection, Ollama/llama.cpp integration, setup wizard
- **Protocol-first design** -- `AgentProfile` and `StorageBackend` are `@runtime_checkable` protocols. Bring your own agents and storage.

## Install

```bash
pip install cohort              # zero-dep core library
pip install cohort[mcp]         # adds MCP tools
pip install cohort[all]         # everything including dev tools
```

Requires Python 3.11+.

## MCP Integration (Claude Code)

Cohort includes a built-in MCP server for Claude Code with two operating modes:

**Lite mode** (standalone, no server required):
```json
{
  "mcpServers": {
    "cohort": {
      "command": "python",
      "args": ["-m", "cohort.mcp.server"],
      "env": {
        "COHORT_DATA_DIR": "/path/to/your/data"
      }
    }
  }
}
```

Lite mode operates directly on file-based storage. Channels, messages, agent profiles, checklists, and search all work without any server process.

**Full mode** (auto-detected when Cohort server is running):

If a Cohort server is running on `localhost:5100`, the MCP server automatically upgrades to full mode with live agent routing, real-time sessions, work queue, and briefing generation.

### Available MCP Tools

| Tool | Lite | Full | Description |
|------|------|------|-------------|
| `read_channel` | Yes | Yes | Read messages from a channel |
| `post_message` | Yes | Yes | Post a message to a channel |
| `list_channels` | Yes | Yes | List all channels |
| `cohort_create_channel` | Yes | Yes | Create a new channel |
| `channel_summary` | Yes | Yes | Compact activity summary |
| `cohort_list_agents` | Yes | Yes | List all agents |
| `cohort_get_agent` | Yes | Yes | Get agent details |
| `cohort_get_agent_memory` | Yes | Yes | View agent memory |
| `cohort_add_fact` | Yes | Yes | Add a learned fact |
| `cohort_clean_memory` | Yes | Yes | Trim working memory |
| `cohort_search_messages` | Yes | Yes | Search across channels |
| `get_checklist` | Yes | Yes | Read to-do checklist |
| `update_checklist` | Yes | Yes | Add/complete/remove tasks |
| `condense_channel` | -- | Yes | LLM-powered summarisation |
| `cohort_roundtable` | -- | Yes | Live multi-agent session |
| `cohort_execute` | -- | Yes | Work queue operations |

## Quick Start (Python API)

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

# 3. Post a message and start a session
chat.post_message("design-review", sender="user", content="Review the REST API design")
orch = Orchestrator(chat, agents=agents)
session = orch.start_session("design-review", "REST API design review", initial_agents=list(agents))

# 4. Ask who should speak next
rec = orch.get_next_speaker(session.session_id)
print(f"Next speaker: {rec['recommended_speaker']}")
print(f"Reason: {rec['reason']}")

# 5. Record turns and let Cohort manage the flow
orch.record_turn(session.session_id, "architect", "msg-001")
```

## CLI

Cohort includes CLI commands for file-based collaboration. Agents in **any language** can participate by appending to a shared `.jsonl` file.

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

# Generate an executive briefing
python -m cohort briefing generate --hours 24

# Interactive setup wizard (Ollama + model selection)
python -m cohort setup
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
     |  SqliteStorage   |  (optional: SQLite)
     +-----------------+
```

**Orchestrator** manages sessions: who's invited, whose turn it is, when the topic shifts.

**Meeting** scores each agent across five dimensions before allowing them to speak:

| Dimension | Weight | What it measures |
|-----------|--------|-----------------|
| Domain expertise | 30% | Keyword overlap between topic and agent triggers/capabilities |
| Complementary value | 25% | Are this agent's partners active? |
| Historical success | 20% | Past performance on similar topics |
| Phase alignment | 15% | Is this the right agent for the current workflow phase? |
| Data ownership | 10% | Does this agent own data relevant to the topic? |

Agents move through stakeholder statuses: **active** -> **approved_silent** -> **observer** -> **dormant**. Each status raises the threshold to speak, preventing agents from dominating.

## Local LLM Integration

Cohort includes built-in support for local inference:

```bash
python -m cohort setup  # Interactive wizard: detects GPU, installs Ollama, picks model
```

- **Hardware detection** -- Identifies GPU VRAM and recommends appropriate models
- **Ollama client** -- Direct integration for local inference
- **llama.cpp support** -- For direct GGUF model serving
- **Model routing** -- Automatic model selection based on task complexity

## Protocols

Cohort uses Python protocols -- no base classes required:

```python
from cohort import AgentProfile, StorageBackend

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
git clone https://github.com/rwheeler007/cohort.git
cd cohort
pip install -e ".[dev]"
pytest
ruff check cohort/ tests/
mypy cohort/
```

## License

Apache 2.0 -- see [LICENSE](LICENSE) for details.
