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
| Codegen module | `cohort/codegen/` |
| Demo specs | `demos/specs/` |
| Demo helpers | `demos/helpers/` |
| MCP server | `cohort/mcp/server.py` |
| Browser backend | `cohort/mcp/browser_backend.py` |
| Local inference | `cohort/local/` |
| Task execution | `cohort/task_executor.py` |
| Work queue | `cohort/work_queue.py` |
| Scheduler | `cohort/scheduler.py` |
| Website creator | `cohort/website_creator/` |
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

All write operations are audit-logged to `data/skill_audit.jsonl`.

---

## Architecture Notes

- **Pip-installable** with optional extras: `cohort[all]`, `cohort[e2e]`, `cohort[dev]`
- **Local-first inference** via Ollama (qwen3.5:9b) through `cohort/local/router.py`
- **Three response tiers**: Smart (no thinking), Smarter (thinking), Smartest (local reasoning + cloud API)
- **Socket.IO** for real-time UI updates
- **MCP server** with browser automation (Playwright), 40+ actions, 3-tier permissions
- **Task system**: WorkQueue (FIFO) + TaskExecutor (briefing -> execution) + Scheduler (cron)
