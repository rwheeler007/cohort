# Changelog

All notable changes to Cohort will be documented in this file.

## [Unreleased]

### Added

- **Briefing tasks in Review panel** -- Tasks in `briefing` status now surface in the Review tab with an amber-bordered card showing the brief fields (Goal, Approach, Scope, Acceptance). Each card has three actions: Chat (opens the task conversation channel), Confirm (starts execution), and Decline (fails the task). The Review badge count includes pending briefings.
- **cancel_task socketio event** -- New server event allows declining a briefing from the Review panel. Marks the task as `failed` with reason "Declined by user from review panel" and broadcasts `cohort:task_updated` for real-time UI sync.
- **cohort:tasks_sync event** -- Full task list is now sent to the client on initial connection via the `join` handler. Previously, only tasks created during the active session appeared in state; existing tasks from before a page refresh were invisible.
- **Auto-filing system channels** -- Task channels (`task-*`) and setup channels (`session-setup`) are automatically sorted into a "Task Conversations" system folder in the sidebar. System folders are collapsed by default, cannot be renamed or deleted, and don't accept drag-drop. Task channels display their description (e.g., "Task briefing: Scan all Python files...") instead of the raw `task-{id}` identifier. The rule engine is extensible via `SYSTEM_FOLDER_RULES` in `cohort.js`.
- **Stale briefing reaper** -- `TaskStore.reap_stale_briefings(max_age_hours=4)` auto-fails tasks stuck in `briefing` status for longer than the threshold. Called from `CohortDataLayer.get_team_snapshot()` on every dashboard load, preventing agents from showing as permanently BUSY due to abandoned briefings.

### Fixed

- **Python Developer stuck as BUSY** -- Two tasks from Mar 10 were stuck in `briefing` status (never confirmed via the task channel UI), causing the Python Developer to show as permanently BUSY on the Team Dashboard. Root cause: `create_task()` sets initial status to `briefing`, which requires user confirmation via `confirm_task` socketio event. If the user navigated away from the task channel without confirming, there was no way to discover or act on the pending briefing. The stale reaper and Review panel changes prevent this class of bug going forward.

## [0.2.0] - 2026-03-02

First public release on PyPI.

### Added
- Zero-dependency local LLM router with hardware detection and Ollama client
- Agent API with FastAPI integration (`cohort[agent-api]`)
- HTTP server with Starlette (`cohort[server]`)
- Socket.IO real-time transport
- File-based JSONL transport for offline/local workflows
- Agent router with model-aware routing and briefing system
- Setup Guide onboarding agent for new user walkthroughs
- Token optimization: model tag and token count display in chat
- CLI entry point (`cohort` command)
- Docker and docker-compose support
- GitHub Actions CI with lint, type-check, and test across Python 3.11-3.13
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
