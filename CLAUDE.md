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

---

## Key Locations

| What | Where |
|------|-------|
| Package source | `cohort/` |
| Agents | `agents/` |
| Agent personas | `agents/{name}/agent_persona.md`, `cohort/personas/` |
| Tests | `tests/` |
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

## Architecture Notes

- **Pip-installable** with optional extras: `cohort[all]`, `cohort[e2e]`, `cohort[dev]`
- **Local-first inference** via Ollama (qwen3.5:9b) through `cohort/local/router.py`
- **Three response tiers**: Smart (no thinking), Smarter (thinking), Smartest (local reasoning + cloud API)
- **Socket.IO** for real-time UI updates
- **MCP server** with browser automation (Playwright), 40+ actions, 3-tier permissions
- **Task system**: WorkQueue (FIFO) + TaskExecutor (briefing -> execution) + Scheduler (cron)
