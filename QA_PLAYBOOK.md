# Cohort v0.2.0 -- Manual QA Playbook

Pre-ship verification checklist. Work through each section top to bottom.
Check off each item as you verify it. Any failure is a ship blocker.

---

## Environment Setup

Create a clean sandbox so you test what a real user gets:

```bash
# 1. Create fresh venv
python -m venv qa_sandbox
qa_sandbox\Scripts\activate        # Windows
# source qa_sandbox/bin/activate   # Mac/Linux

# 2. Install Cohort with all extras
pip install -e G:\cohort[all]

# 3. Create a fresh data directory (isolated from dev data)
mkdir qa_test_data

# 4. Verify the CLI entry point works
cohort --help
```

- [ ] Venv created and activated
- [ ] `pip install -e .[all]` completes without errors
- [ ] `cohort --help` prints usage with subcommands: serve, setup, gate, say, next-speaker, serve-agents

---

## Section 1: Installation Verification

```bash
# Core (zero dependencies)
pip install cohort
python -c "import cohort; print('OK')"

# Check extras resolve
pip install cohort[server]
python -c "import starlette; import uvicorn; print('server deps OK')"

pip install cohort[agent-api]
python -c "import fastapi; import pydantic; print('agent-api deps OK')"

# Entry point
cohort --help
python -m cohort --help
```

- [ ] `import cohort` succeeds (no ImportError)
- [ ] `cohort[server]` installs starlette + uvicorn + python-socketio
- [ ] `cohort[agent-api]` installs fastapi + pydantic + python-dotenv
- [ ] `cohort` CLI and `python -m cohort` both show help text
- [ ] Help text lists all 6 subcommands: serve, serve-agents, setup, gate, say, next-speaker
- [ ] Version shows `0.2.0` (check `pip show cohort`)

---

## Section 2: Setup Wizard

Run the interactive wizard. This tests hardware detection, Ollama integration,
model pulling, and content pipeline setup.

```bash
cohort setup
```

### Step 1: Hardware Detection

- [ ] Displays system info (Windows/Mac/Linux)
- [ ] GPU detected with name and VRAM (or "CPU-only" message with positive framing)
- [ ] VRAM displayed in human-readable format (e.g., "8.0 GB")
- [ ] No Unicode errors in console output (ASCII only: [OK], [*], [!])

### Step 2: Ollama Check

- [ ] Checks if Ollama binary is on PATH
- [ ] Checks if Ollama server is running (http://127.0.0.1:11434)
- [ ] If running: shows [OK] and skips to Step 4
- [ ] If not running but installed: suggests platform-specific start command

### Step 3: Ollama Install (if needed)

- [ ] Windows: offers to download OllamaSetup.exe with progress bar
- [ ] Mac: suggests `brew install ollama` (or DMG link)
- [ ] Linux: suggests curl one-liner
- [ ] After install: waits for user to confirm, then re-checks server

### Step 4: Model Pull

- [ ] Recommends model based on detected VRAM tier:
  - CPU/<4GB: qwen2.5-coder:1.5b (~1 GB)
  - 4-6GB: gemma3:4b (~2.5 GB)
  - 6-8GB: qwen3:8b (~4.7 GB)
  - 8GB+: qwen3:30b-a3b (~18 GB)
- [ ] Shows model description (size + summary)
- [ ] If model already pulled: "[OK] Already installed" and skips download
- [ ] If downloading: ASCII progress bar updates (e.g., `[=========>          ] 45%`)
- [ ] Download completes without error

### Step 5: Verify

- [ ] Sends test prompt to Ollama
- [ ] Receives response (displayed to user)
- [ ] Shows elapsed time
- [ ] Prints [OK] on success

### Step 6: Content Pipeline (Optional)

- [ ] Asks "Want to set this up now? [Y/n]"
- [ ] Pressing Enter (default Y) continues; typing "n" skips
- [ ] Lists topic categories (web dev, python, AI, etc.)
- [ ] Selecting a topic shows curated RSS feeds with names and URLs
- [ ] Selecting feeds by number works (e.g., "1,3")
- [ ] Writes `data/content_config.json` with correct structure
- [ ] Config file contains: feeds array, topic string, check_interval_minutes, max_articles_per_feed

### Wizard Completion

- [ ] Prints 6-item summary (hardware, engine, model, next steps)
- [ ] Next steps mention: `cohort serve`, browser URL, "meet your agents"
- [ ] Exit code is 0

### Wizard Edge Cases

- [ ] Ctrl+C at any point prints friendly interrupt message and exits cleanly
- [ ] Re-running `cohort setup` detects already-completed steps and skips them

---

## Section 3: Core Server

```bash
# Start the server with fresh data dir
cohort serve --port 5100 --data-dir qa_test_data
```

### Startup

- [ ] Console shows: `[*] cohort server starting on 0.0.0.0:5100`
- [ ] Console shows: `[*] data dir: qa_test_data`
- [ ] No Python errors or tracebacks on startup
- [ ] Server stays running (doesn't crash immediately)

### Health Check

```bash
curl http://localhost:5100/health
```

- [ ] Returns `{"status": "ok"}` (HTTP 200)

### Dashboard UI

Open `http://localhost:5100/` in browser.

- [ ] Page loads (not blank, not 404)
- [ ] Dark theme renders correctly
- [ ] Sidebar visible with panel buttons: Team, Chat, Work Queue, Output
- [ ] Team panel shows agent cards (at least the on-disk agents)
- [ ] Agent cards show: name, role, status badge, skills

### Channel Management

```bash
# Create a channel via API
curl -X POST http://localhost:5100/api/channels \
  -H "Content-Type: application/json" \
  -d "{\"name\": \"qa-test\", \"description\": \"QA testing channel\"}"
```

- [ ] Returns success with channel object
- [ ] Channel appears in sidebar (refresh if needed)
- [ ] Click channel in sidebar -- chat panel loads

### Message Sending

```bash
# Send a message via API
curl -X POST http://localhost:5100/api/send \
  -H "Content-Type: application/json" \
  -d "{\"channel\": \"qa-test\", \"sender\": \"human\", \"message\": \"Hello from QA playbook\"}"
```

- [ ] Returns success with message_id
- [ ] Message appears in chat panel (may need refresh)
- [ ] Message shows sender as "human"
- [ ] Message persists after page refresh

### Agent List

```bash
curl http://localhost:5100/api/agents
```

- [ ] Returns JSON array of agents
- [ ] Each agent has: agent_id, name, role, status
- [ ] On-disk agents included (python_developer, web_developer, javascript_developer, security_agent, qa_agent)

### Data Persistence

- [ ] Check `qa_test_data/` directory -- files created (channels, messages, etc.)
- [ ] Stop server (Ctrl+C), restart it, verify channels and messages still there

---

## Section 4: Agent API (Tier Gating)

```bash
# Start Agent API server (separate terminal)
# Point it at Cohort's own agents directory
cohort serve-agents --port 8200 --agents-dir G:\cohort\agents
```

### Startup

- [ ] Console shows startup message with agent count
- [ ] No Python errors

### Health Endpoint

```bash
curl http://localhost:8200/health
```

- [ ] Returns JSON with: status, uptime_seconds, agent_count
- [ ] agent_count matches number of on-disk agent directories

### Free Tier (No API Key)

```bash
curl http://localhost:8200/agents
```

- [ ] Returns agent list
- [ ] Contains on-disk agents (python_developer, web_developer, etc.)
- [ ] Response includes `tier` field

### Tier Descriptions

```bash
curl http://localhost:8200/tiers
```

- [ ] Returns three tiers: free, pro, enterprise
- [ ] Free tier lists 15 agent IDs
- [ ] Enterprise tier lists boss_agent and supervisor_agent
- [ ] Tier descriptions are accurate (not BOSS-specific language)

### Enterprise Gating

```bash
# Try to access boss_agent without enterprise key
curl http://localhost:8200/agents/boss_agent/config
```

- [ ] Returns HTTP 403 (forbidden) or appropriate error
- [ ] Error message mentions tier requirement

### Agent Config Endpoint

```bash
curl http://localhost:8200/agents/python_developer/config
```

- [ ] Returns full agent_config.json content
- [ ] Has all expected fields: agent_id, name, role, capabilities, triggers, etc.

### Agent Prompt Endpoint

```bash
curl http://localhost:8200/agents/python_developer/prompt
```

- [ ] Returns agent_prompt.md content (or appropriate message if file doesn't exist)

### Agent Profile (Bundled)

```bash
curl http://localhost:8200/agents/python_developer/profile
```

- [ ] Returns combined response: config + prompt + recent_facts
- [ ] All three sections populated

### Rate Limiting

```bash
# Rapid-fire 5 requests (should all succeed within limit)
for i in 1 2 3 4 5; do curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8200/agents; done
```

- [ ] All return 200 (well within 120/min limit)

---

## Section 5: CLI Commands

These test the file-based transport layer (JSONL format).

### Setup Test Data

```bash
# Create a sample agents.json
cat > qa_agents.json << 'EOF'
{
  "architect": {
    "name": "Architect",
    "triggers": ["design", "architecture", "pattern", "system"],
    "capabilities": ["System design", "Architecture review"],
    "role": "architect"
  },
  "tester": {
    "name": "Tester",
    "triggers": ["test", "qa", "bug", "coverage"],
    "capabilities": ["Test strategy", "Bug finding"],
    "role": "tester"
  }
}
EOF
```

### cohort say

```bash
cohort say --sender architect --channel review --file qa_conv.jsonl \
  --message "We should use a layered architecture for this service"

cohort say --sender tester --channel review --file qa_conv.jsonl \
  --message "What about test coverage for each layer?"

cohort say --sender architect --channel review --file qa_conv.jsonl \
  --message "Good point. Each layer should have unit tests with >80% coverage"
```

- [ ] Each command prints `[OK] msg_xxx -> #review`
- [ ] File `qa_conv.jsonl` created and grows with each message
- [ ] File contains valid JSON-per-line format

### cohort gate

```bash
# Should the tester speak after architect's last message?
cohort gate --agent tester --channel review --file qa_conv.jsonl \
  --agents qa_agents.json

# JSON output
cohort gate --agent tester --channel review --file qa_conv.jsonl \
  --agents qa_agents.json --format json
```

- [ ] Text output shows: Agent, Score, Threshold, Decision (SPEAK or SILENT), Reason
- [ ] JSON output is valid JSON with: agent, score, threshold, speak (boolean), reason
- [ ] Score is a float between 0 and 1
- [ ] Decision makes sense (tester should score well on "test coverage" topic)

### cohort next-speaker

```bash
cohort next-speaker --channel review --file qa_conv.jsonl \
  --agents qa_agents.json --top 2

# JSON output
cohort next-speaker --channel review --file qa_conv.jsonl \
  --agents qa_agents.json --format json
```

- [ ] Text output shows ranked list with agent names, scores, phase
- [ ] JSON output is valid JSON array
- [ ] Scores are in descending order
- [ ] `--top 2` limits output to 2 agents

---

## Section 6: Real-Time (Socket.IO)

These tests require both the server running and a browser open.

### Connection

- [ ] Open `http://localhost:5100/` in browser
- [ ] Browser console (F12) shows Socket.IO connection established
- [ ] No WebSocket errors in console

### Live Message Updates

In a second terminal while the browser is open:

```bash
curl -X POST http://localhost:5100/api/send \
  -H "Content-Type: application/json" \
  -d "{\"channel\": \"qa-test\", \"sender\": \"api-tester\", \"message\": \"This should appear in real-time\"}"
```

- [ ] Message appears in browser chat panel WITHOUT page refresh
- [ ] Sender shows as "api-tester"
- [ ] Timestamp is current

### @Mention Routing

```bash
# Send a message that @mentions an agent
curl -X POST http://localhost:5100/api/send \
  -H "Content-Type: application/json" \
  -d "{\"channel\": \"qa-test\", \"sender\": \"human\", \"message\": \"@python_developer what testing framework do you recommend?\"}"
```

- [ ] Message posts successfully
- [ ] If Claude CLI is configured in settings: agent responds in the channel
- [ ] If Claude CLI is NOT configured: no crash, message still posts, no agent response (expected)

---

## Section 7: Agent Configs (On-Disk Validation)

Verify each shipped agent has valid, complete files.

### Agent Directories

```bash
ls G:\cohort\agents\
```

- [ ] Contains exactly 6 directories: python_developer, web_developer, javascript_developer, security_agent, qa_agent, setup_guide

### Per-Agent Checks

For EACH agent directory, verify:

```bash
# Example for python_developer (repeat for all 6)
python -c "
import json, pathlib
d = pathlib.Path('G:/cohort/agents/python_developer')
# Config loads as valid JSON
cfg = json.loads((d / 'agent_config.json').read_text())
assert cfg['agent_id'] == 'python_developer', f'Bad agent_id: {cfg[\"agent_id\"]}'
assert cfg['name'], 'Missing name'
assert cfg['role'], 'Missing role'
assert cfg['capabilities'], 'Missing capabilities'
assert cfg['triggers'], 'Missing triggers'
assert cfg['status'] == 'active', f'Status: {cfg[\"status\"]}'
print(f'[OK] {cfg[\"agent_id\"]}: {cfg[\"name\"]} -- {cfg[\"role\"]}')
# Persona file exists
assert (d / 'agent_persona.md').exists(), 'Missing agent_persona.md'
persona = (d / 'agent_persona.md').read_text()
assert len(persona) > 100, f'Persona too short: {len(persona)} chars'
print(f'[OK] Persona: {len(persona)} chars')
"
```

- [ ] python_developer: config valid, persona exists
- [ ] web_developer: config valid, persona exists
- [ ] javascript_developer: config valid, persona exists
- [ ] security_agent: config valid, persona exists
- [ ] qa_agent: config valid, persona exists
- [ ] setup_guide: config valid, persona exists

### Batch Validation Script

```bash
python -c "
import json, pathlib, sys
agents_dir = pathlib.Path('G:/cohort/agents')
ok = 0
for d in sorted(agents_dir.iterdir()):
    if not d.is_dir() or d.name.startswith('.'):
        continue
    cfg_path = d / 'agent_config.json'
    if not cfg_path.exists():
        print(f'[X] {d.name}: missing agent_config.json')
        continue
    try:
        cfg = json.loads(cfg_path.read_text())
        assert cfg.get('agent_id'), 'no agent_id'
        assert cfg.get('name'), 'no name'
        assert cfg.get('role'), 'no role'
        assert cfg.get('capabilities'), 'no capabilities'
        persona = d / 'agent_persona.md'
        p_status = f'persona {len(persona.read_text())} chars' if persona.exists() else 'NO PERSONA'
        print(f'[OK] {cfg[\"agent_id\"]:25s} {cfg[\"name\"]:25s} {p_status}')
        ok += 1
    except Exception as e:
        print(f'[X] {d.name}: {e}')
print(f'\n{ok} agents validated')
"
```

- [ ] All 6 agents pass validation
- [ ] No agents have missing or malformed configs

---

## Section 8: Edge Cases

### Empty Data Directory

```bash
mkdir empty_data_test
cohort serve --port 5101 --data-dir empty_data_test
```

- [ ] Server starts without crashing
- [ ] Dashboard loads (empty state -- no channels, no messages)
- [ ] Creating a channel works from scratch
- [ ] Stop server, check `empty_data_test/` has created data files

### Bad API Key (Agent API)

```bash
# Set up env with known keys
set COHORT_AGENT_API_KEYS=test-key-1:free,test-key-2:pro

cohort serve-agents --port 8201 --agents-dir G:\cohort\agents

# In another terminal:
curl -H "X-API-Key: completely-invalid-key" http://localhost:8201/agents
```

- [ ] Returns HTTP 403 or appropriate auth error
- [ ] Does not crash the server
- [ ] Server continues accepting valid requests after bad key

### Missing Ollama (Setup Wizard)

```bash
# Temporarily rename ollama binary or ensure it's not running
# Then run setup
cohort setup
```

- [ ] Step 2 detects Ollama is missing
- [ ] Step 3 provides install instructions (no crash)
- [ ] If user types "n" to skip install: wizard exits gracefully with instructions

### Ctrl+C During Setup

```bash
cohort setup
# Press Ctrl+C during any step
```

- [ ] Prints: "Setup interrupted. Run 'cohort setup' anytime to continue."
- [ ] Exit code is non-zero
- [ ] No traceback printed
- [ ] No corrupted files left behind

---

## Final Checklist

Before publishing to PyPI, ALL of the above must pass.

- [ ] Section 1: Installation (6 checks)
- [ ] Section 2: Setup Wizard (20+ checks)
- [ ] Section 3: Core Server (14 checks)
- [ ] Section 4: Agent API (12 checks)
- [ ] Section 5: CLI Commands (10 checks)
- [ ] Section 6: Real-Time (5 checks)
- [ ] Section 7: Agent Configs (8 checks)
- [ ] Section 8: Edge Cases (8 checks)

**Total: ~83 manual verification points**

---

## Quick Reference: Ports

| Service | Default Port | Command |
|---------|-------------|---------|
| Core Server (UI + API) | 5100 | `cohort serve --port 5100` |
| Agent API | 8200 | `cohort serve-agents --port 8200` |
| Ollama | 11434 | (external, started separately) |
