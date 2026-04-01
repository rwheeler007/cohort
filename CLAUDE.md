# Cohort

> Local-first AI assistant platform -- pip-installable, runs on consumer hardware.

---

## CRITICAL RULES

### Never Modify Production Data for Testing or Display

Tests, demos, benchmarks, and code generation **MUST use isolated data directories** -- never read from, write to, or temporarily modify the user's real data. This includes settings, channels, messages, agent memory, and databases.

**Why:** The `resetToFirstRun()` + `restoreFromBackup()` pattern (modify real settings, restore after) caused data corruption that took significant time to fix. The restore step is fragile -- any crash, timeout, or interruption leaves production data in a test state.

**Required pattern:**
```python
# Correct: isolated temp directory
data_dir = tempfile.mkdtemp(prefix="cohort-test-")
server = CohortServer(data_dir=data_dir)

# Wrong: modify real settings and "restore later"
settings["setup_completed"] = False  # NEVER DO THIS
```

**Applies to:**
- Playwright E2E tests (use `--data-dir` with temp directory)
- Demo recording scripts (create throwaway instance)
- Code generation verification (run against isolated server)
- Benchmarks and load tests
- Any automated process that starts a Cohort server

**No exceptions.** If a test framework doesn't support data isolation, fix the framework first.

### No Unicode Emojis in Console Output

Windows console uses cp1252 -- Unicode emojis cause `UnicodeEncodeError`.
Use ASCII markers: `[OK]` `[X]` `[!]` `[*]` `[>>]` `[...]`

### Post-Implementation E2E Verification

After completing a code task that touches UI-facing code (server routes, templates, static JS/CSS, setup wizard), run the E2E test suite as a second verification layer:

```bash
# Run all E2E tests
python -m cohort test e2e

# Run only tests relevant to what changed
python -m cohort test e2e --tag smoke       # core health
python -m cohort test e2e --tag import      # import preferences wizard
python -m cohort test e2e --tag chat        # message/agent interaction
python -m cohort test e2e --tag channels    # channel CRUD
python -m cohort test e2e --tag settings    # settings modal

# Run a specific spec
python -m cohort test e2e --spec import-preferences

# List available specs
python -m cohort test list

# Run unit + E2E together
python -m cohort test all
```

Unit tests (pytest) pass when backend logic is correct. E2E tests catch **wiring bugs** -- routes not hooked up, WebSocket events misspelled, DOM not updating. **Both layers must pass before a task is considered complete.**

E2E tests auto-create an isolated Cohort server on port 5199 with a temp data directory. Production data is never touched.

---

## Key Locations

| What | Where |
|------|-------|
| Package source | `cohort/` |
| Agents | `agents/` |
| Agent personas | `agents/{name}/agent_persona.md`, `cohort/personas/` |
| Tests (unit) | `tests/` |
| Tests (E2E specs) | `tests/e2e/specs/` |
| Tests (E2E helpers) | `tests/e2e/helpers/test-utils.ts` |
| Test CLI | `cohort/cli/test_cmd.py` |
| CLI commands | `cohort/cli/` (40+ commands) |
| Codegen module | `cohort/codegen/` |
| Demo specs | `demos/specs/` |
| Demo helpers | `demos/helpers/` |
| MCP server | `cohort/mcp/server.py` (50+ tools) |
| MCP lite backend | `cohort/mcp/lite_backend.py` |
| Browser backend | `cohort/mcp/browser_backend.py` |
| Local inference | `cohort/local/` |
| Task execution | `cohort/task_executor.py` |
| Task store | `cohort/task_store.py` |
| Work queue | `cohort/work_queue.py` |
| Scheduler | `cohort/scheduler.py` |
| Approval pipeline | `cohort/approval_store.py`, `cohort/review_pipeline.py` |
| Deliverables | `cohort/deliverables.py` |
| Meeting system | `cohort/meeting.py` |
| Response gating | `cohort/response_gate.py` |
| Channel bridge | `cohort/channel_bridge.py` |
| Briefings | `cohort/briefing.py`, `cohort/executive_briefing.py` |
| Agent routing | `cohort/agent_router.py`, `cohort/capability_router.py` |
| Health monitor | `cohort/health_monitor.py` |
| Desktop automation | `cohort/desktop/` (MCP server, backend, VDD, config, safety) |
| Desktop MCP server | `cohort/desktop/mcp_server.py` (stdio transport) |
| Desktop HTTP endpoints | `cohort/desktop/http_endpoints.py` (mounted in server) |
| Desktop VDD driver | `cohort/desktop/virtual_display.py` (Parsec VDD) |
| Desktop config | `config/desktop_computer_use.yaml` |
| Learning system | `cohort/learning.py` |
| Memory manager | `cohort/memory_manager.py` |
| Web search | `cohort/web_search.py` |
| Socket.IO events | `cohort/socketio_events.py` |
| Website creator | `cohort/website_creator/` (private, not in public builds) |
| CI/CD | `.github/workflows/ci.yml` |
| QA playbook | `QA_PLAYBOOK.md` |

## Claude Code Native Skills

Defined in `.claude/skills/<name>/SKILL.md`. Execute directly in Claude Code sessions.

### /health - System Health Check

- `/health` or `/health all` - Cohort server (:5100) + Ollama (:11434) status
- `/health server` - Cohort server only
- `/health ollama` - Ollama model count
- `/health doctor` - Full diagnostics (Python, Git, data dir, agents)

### /tiers - Response Tier Manager

- `/tiers` or `/tiers show` - Current smart/smarter/smartest model assignments
- `/tiers set <tier> <model>` - Override a tier's model (writes `data/tier_settings.json`)
- `/tiers budget` - Show token budget limits
- `/tiers reset` - Clear overrides, revert to VRAM defaults

### /preheat - Model Warmup

- `/preheat` or `/preheat all` - Warm up the primary model via Ollama
- `/preheat <model_name>` - Warm up a specific model

### /queue - Work Queue Inspector

- `/queue` or `/queue list` - List non-terminal work queue items
- `/queue active` - Show currently active item
- `/queue show <id>` - Full item detail
- `/queue cancel <id>` - Cancel a queued item

### /settings - Runtime Settings

- `/settings` or `/settings show` - Display current config (model, timeout, backend)
- `/settings set <key> <value>` - Update a setting (rejects secret fields)

### /rate - Rate Limit & Cloud API Status

- `/rate` or `/rate status` - Agent cooldown, escalation budget, cloud API availability
- `/rate escalations` - Escalation budget detail

### /decisions - Agent Decision Tracker

- `/decisions` or `/decisions open` - All open decisions across all agents
- `/decisions list <agent>` - All decisions for one agent (shorthand: co, pd, cs, sec, qa, mk)
- `/decisions show <agent> <id>` - Full detail for one decision
- `/decisions add <agent> <description>` - Add a new decision
- `/decisions close <agent> <id> [resolution]` - Close a decision

All write operations are audit-logged to `data/skill_audit.jsonl`.

---

## Architecture Notes

- **Pip-installable** with optional extras: `cohort[all]`, `cohort[e2e]`, `cohort[dev]`
- **Local-first inference** via Ollama (qwen3.5:9b) through `cohort/local/router.py`
- **Three response tiers**: Smart (no thinking), Smarter (thinking), Smartest (local reasoning + cloud API)
- **Socket.IO** for real-time UI updates
- **MCP server** with 50+ tools: channels, agents, tasks, meetings, reviews, browser automation, web search
- **Task system**: WorkQueue (FIFO) + TaskExecutor (briefing -> execution) + Scheduler (cron)
- **Review pipeline**: Task completion -> deliverables check -> approval request -> multi-stakeholder review -> accept/reject/requeue
- **Meeting system**: Structured multi-agent discussions with stakeholder gating, 5-dimension relevance scoring, phase detection, and dynamic participant management
- **Response gate**: 3-tier gating system for channel bridge responses
- **Channel bridge**: Routes @mentions to agents, demand-driven session launch, force flag support
- **Desktop computer use**: Windows desktop automation via Parsec VDD virtual displays or real monitor
- **Health monitor**: Service registry with start/stop/restart, `CREATE_NEW_CONSOLE` on Windows for visible terminals

### Desktop Computer Use

Desktop automation subsystem in `cohort/desktop/`. Agents can screenshot, click, type, and manage windows.

**Two transports:**
- **MCP server** (`mcp_server.py`) — stdio, launched per-channel session by the VS Code extension. Provides `desktop_action` / `desktop_status` tools.
- **HTTP endpoints** (`http_endpoints.py`) — `POST /api/desktop/action`, `GET /api/desktop/status`, mounted inside `cohort serve`. Used by pytest and REST clients.

**Display profiles** (set `profile` key in `config/desktop_computer_use.yaml`):
- `virtual` — Parsec VDD isolated monitor (1024x768), `desktop_advanced` tier, no window restrictions. Safe for autonomous work.
- `main_display` — Real primary monitor, `desktop_interact` tier, window allowlist enforced. For demos and visual verification.

**Key env var:** `COHORT_DESKTOP_CONFIG` — override path to `desktop_computer_use.yaml`. Set automatically by the VS Code extension in `.mcp.json`.

### Health Monitor & Service Management

`cohort/health_monitor.py` tracks services and can start/stop/restart them.

- **Service registry** — `.cohort/data/services/health_monitor/service_registry.json` defines services with `start_command`, `port`, `health_endpoint`, `controllable`.
- **`_launch_service()`** — On Windows uses `subprocess.CREATE_NEW_CONSOLE` to spawn in a visible console window. On Unix, detached subprocess.
- **API** — `POST /api/health-monitor/{start|stop|restart}/{service_key}`
- **VS Code extension** — Dashboard health panel shows Start/Stop/Restart buttons for controllable services.

---

## Exports

Reusable capabilities other projects can import or call from Cohort.

| Capability | Entry Point | What It Does |
|------------|-------------|--------------|
| website-pipeline | `cohort/website_creator/pipeline.py` | End-to-end YAML brief -> static HTML site generation (~60 sec) |
| decision-engine | `cohort/website_creator/decision_engine.py` | Neural flowchart: Tier 1 classification -> Tier 2 generation for website structure |
| block-populator | `cohort/website_creator/block_populator.py` | Taste profile + business info -> block assembly specs (32 block types, 48 templates) |
| form-handler | `cohort/website_creator/form_handler.py` | Drop-in JS for static site form submission (Formspree, CF Workers, any endpoint) |
| image-resolver | `cohort/website_creator/image_resolver.py` | Resolve placeholder images to real Unsplash URLs by category/context |
| website-deploy | `cohort/website_creator/deploy.py` | `wrangler pages deploy` wrapper -> live *.pages.dev URL |
| site-brief | `cohort/website_creator/site_brief.py` | YAML-based website specification schema (Pydantic dataclasses) |
| local-router | `cohort/local/router.py` | Local Ollama inference routing (zero-dependency, never raises) |
| hardware-detect | `cohort/local/detect.py` | GPU/VRAM/CPU detection -> optimal model recommendation |
| cloud-backend | `cohort/local/cloud.py` | Provider-agnostic cloud LLM (Anthropic, OpenAI) with user-supplied key |
| compiled-roundtable | `cohort/compiled_roundtable.py` | Single-call multi-agent discussion (N personas in one context, ~90% token savings) |
| capability-router | `cohort/capability_router.py` | Dynamic agent routing by triggers/capabilities (not hardcoded names) |
| learning-system | `cohort/learning.py` | Extract durable facts from conversations; deduplicate; evolve user profile |
| import-seed | `cohort/import_seed.py` | Parse ChatGPT/Claude exports -> preference extraction via local LLM |
| agent-assessor | `tools/agent_assessor.py` | 100-question agent assessment with multi-step challenges and scoring |
| codegen-pipeline | `cohort/codegen/generator.py` | LLM code generation with planning, verification, and safety checks |
| export-personas | `cohort/export_personas.py` | Export agent definitions as lightweight portable markdown files |
| channel-plugin | `plugins/cohort-channel/src/index.ts` | MCP server: poll/claim/reply lifecycle for Claude Code integration |

## Cohort Integration

This project is managed by [Cohort](https://github.com/anthropics/cohort) — a multi-agent team platform. Cohort provides MCP tools and Claude Code Channel sessions for collaborative work.

### How Cohort Works

- **Channels** are persistent conversation threads. Agents and humans post messages, share context, and coordinate work in channels.
- **Claude Code Channels** launch dedicated Claude Code sessions per channel, each with its own MCP connection back to the Cohort server. The session gets the channel context and can read/post messages.
- **Agents** are team members with defined roles, expertise, and memory. Use `cohort_list_agents` to see who's available.
- **Work queue** tracks tasks that need doing. Items flow through enqueue -> claim -> complete.
- **Review pipeline** routes completed work through approval before delivery.
- **Meeting system** orchestrates multi-agent discussions with stakeholder gating and relevance scoring.

### Key MCP Tools

**Channels:**

| Tool | Purpose |
|------|---------|
| `read_channel` | Read messages from a channel |
| `post_message` | Post a message to a channel |
| `list_channels` | List available channels |
| `cohort_create_channel` | Create a new channel |
| `channel_summary` | Compact summary of recent channel activity |
| `condense_channel` | Condense old messages and archive |
| `get_checklist` | Read a channel's task checklist |
| `update_checklist` | Update checklist items |

**Agents:**

| Tool | Purpose |
|------|---------|
| `cohort_list_agents` | List all agents and their roles |
| `cohort_get_agent` | Get an agent's full config |
| `cohort_get_agent_memory` | Read agent's working memory and facts |
| `cohort_create_agent` | Create a new agent |
| `cohort_add_fact` | Add learned facts to agent memory |
| `cohort_clean_memory` | Trim agent working memory |
| `cohort_adopt_persona` | Load an agent's identity into your session |
| `cohort_find_agents` | Find agents qualified for a topic |
| `cohort_route_task` | Auto-route a task to the best agent |
| `cohort_search_messages` | Search across all channels |
| `cohort_get_mentions` | Get messages where an agent was @mentioned |
| `cohort_partnership_graph` | View partnership graph (who consults whom) |

**Tasks & Work Queue:**

| Tool | Purpose |
|------|---------|
| `cohort_enqueue_item` | Add a work item to the queue |
| `cohort_claim_next` | Claim the next available work item |
| `cohort_get_work_queue` | Read the sequential work queue |
| `cohort_get_tasks` | Read all tasks with status filtering |
| `cohort_get_outputs` | Get completed tasks awaiting review |
| `cohort_assign_task` | Assign a task to an agent |

**Discussions:**

| Tool | Purpose |
|------|---------|
| `cohort_discussion` | Run a multi-agent discussion |
| `cohort_compiled_discussion` | Faster single-call multi-agent discussion |
| `cohort_generate_briefing` | Generate executive briefing from activity |

**Review Pipeline:**

| Tool | Purpose |
|------|---------|
| `cohort_submit_for_review` | Submit completed work for review |
| `cohort_get_pending_reviews` | List approval requests awaiting review |
| `cohort_submit_review` | Submit approve/deny verdict |
| `cohort_set_deliverables` | Set acceptance criteria on a task |
| `cohort_requeue_item` | Requeue a rejected item with feedback |
| `cohort_get_approval_status` | Approval pipeline status overview |

**Meeting System:**

| Tool | Purpose |
|------|---------|
| `cohort_meeting_start` | Start a meeting with stakeholder gating |
| `cohort_meeting_stop` | End meeting and generate summary |
| `cohort_meeting_status` | Current meeting status |
| `cohort_meeting_pause/resume` | Pause or resume a meeting |
| `cohort_meeting_add/remove_participant` | Manage meeting participants |
| `cohort_meeting_promote/demote` | Change stakeholder status |
| `cohort_meeting_next_speaker` | Get recommended next speaker with scores |
| `cohort_meeting_score` | 5-dimension relevance breakdown |
| `cohort_meeting_phase` | Detect current discussion phase |
| `cohort_meeting_extend` | Add more turns to a meeting |
| `cohort_meeting_enable/disable` | Toggle stakeholder gating on a channel |

**Desktop:**

- `desktop_action` — Desktop automation (screenshot, click, type, window management)
- `desktop_status` — Check desktop backend status and VDD state

**Browser & Web:**

| Tool | Purpose |
|------|---------|
| `browser_action` | Browser automation via Playwright |
| `browser_status` | Check browser backend status |
| `internal_web_search` | DuckDuckGo search (no API key) |
| `internal_web_fetch` | Fetch and render web page via Playwright |

### CLI Commands

The `python -m cohort` CLI provides 40+ commands. Key ones:

| Command | Purpose |
|---------|---------|
| `serve` | Start the Cohort server |
| `agents` | Agent CRUD and listing |
| `channels` | Channel operations |
| `queue` | Work queue management |
| `tasks` | Task execution |
| `meet` | CLI-first meeting mode (18 subcommands) |
| `discuss` | Multi-agent discussions |
| `briefing` | Generate executive briefings |
| `test` | E2E test runner with tags |
| `health` | Service health checks |
| `config` | Configuration management |
| `scan` | Project scanner |
| `schedule` | Cron job scheduling |
| `benchmark` | Performance benchmarking |
| `learn` | Learning system management |
| `memory` | Agent memory inspection |
| `search` | Message search |
| `import` | ChatGPT/Claude export import |
| `model` | Model management |
| `web` | Web search CLI |
| `secret` | Secret store management |
| `inventory` | Cross-project ecosystem inventory (list, search, sources, refresh) |

### Project Config

Project settings are in `.cohort/config.json`. Channel registrations are in `.cohort/channels.json`.
