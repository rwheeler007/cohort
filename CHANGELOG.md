# Changelog

All notable changes to Cohort will be documented in this file.

## [0.3.0] - 2026-03-11

Open-core release. Apache 2.0 license. First public GitHub release.

### Added
- **Lite MCP mode** -- MCP server works standalone without the web app. File-backed storage for channels, messages, agent profiles, checklists, and search. Auto-detects server availability and upgrades to full mode when running.
- **`cohort.api` stable public interface** -- Single import point (`from cohort.api import ...`) for all 74 exported symbols. Stability contract for downstream consumers.
- **Lite backend** (`cohort/mcp/lite_backend.py`) -- In-process backend using JsonFileStorage + ChatManager directly. Drop-in replacement for CohortClient.
- 20 new tests for lite MCP backend operations

### Changed
- License changed from MIT to Apache 2.0
- Version bumped to 0.3.0
- CLI: removed `serve` and `serve-agents` commands (moved to proprietary web app)
- `pyproject.toml`: removed `server` and `agent-api` optional extras
- MCP server auto-detects backend mode on startup (env: `COHORT_SERVER_URL`, `COHORT_DATA_DIR`)
- README rewritten with MCP-first orientation

### Removed
- `cohort[server]` optional dependency group (web app is separate product)
- `cohort[agent-api]` optional dependency group (agent distribution API is separate product)

## [0.2.0] - 2026-03-02

Internal release (never published publicly).

### Added
- Zero-dependency local LLM router with hardware detection and Ollama client
- File-based JSONL transport for offline/local workflows
- CLI entry point (`cohort` command)
- Comprehensive test suite (488+ tests)

### Changed
- Extracted from BOSS/SMACK monolith into standalone package
- Decontaminated all hardcoded BOSS/SMACK references

## [0.1.0] - 2026-02-28

Internal extraction from BOSS monolith. Not published.

### Added
- Core orchestrator with session management
- Agent registry with JSON file storage
- Chat system with channels, messages, and mention parsing
- Meeting system with stakeholder tracking
- Contribution scoring and loop prevention
