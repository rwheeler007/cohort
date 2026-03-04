# Changelog

All notable changes to Cohort will be documented in this file.

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
