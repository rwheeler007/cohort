---
name: health
description: Cohort system health check -- server, Ollama, agents, channels, queue status. No server required for basic checks.
argument-hint: [all | server | ollama | doctor]
disable-model-invocation: true
allowed-tools: Bash, Read, Glob
---

# Cohort Health CLI

Quick health check across Cohort subsystems.

**Invoked with:** `$ARGUMENTS`

## Commands

Parse `$ARGUMENTS` to determine the scope. Default (no args) = `all`.

### `/health` or `/health all` - Full system health

**Steps:**
1. Run these checks via Bash (each with 5s timeout):
   - `curl -s --max-time 5 http://localhost:5100/api/health` (Cohort server)
   - `curl -s --max-time 5 http://localhost:11434/api/tags` (Ollama)
2. If either fails (connection refused, timeout), mark as `[X] Unreachable`.
3. For Ollama, count loaded models from the response.
4. Display:

```
Cohort Health Check
===================
[OK] Cohort Server     running on :5100
[OK] Ollama            running on :11434 (9 models available)
```

Or with issues:
```
Cohort Health Check
===================
[X]  Cohort Server     not reachable on :5100
[OK] Ollama            running on :11434 (9 models available)
```

### `/health server` - Cohort server only

**Steps:**
1. `curl -s --max-time 5 http://localhost:5100/api/health`
2. Display result

### `/health ollama` - Ollama only

**Steps:**
1. `curl -s --max-time 5 http://localhost:11434/api/tags`
2. Parse and show model count + names of loaded models

### `/health doctor` - Full diagnostics

**Steps:**
1. Check Python version: `python --version` (need >= 3.10)
2. Check Ollama: `curl -s --max-time 5 http://localhost:11434/api/tags`
3. Check Cohort server: `curl -s --max-time 5 http://localhost:5100/api/health`
4. Check git: `git --version`
5. Check data dir writable: test write to `G:/cohort/data/`
6. Check agents directory: count files in `G:/cohort/.claude/agents/`
7. Display diagnostics:

```
Cohort Doctor
=============
[OK] Python           3.13.2 (>= 3.10 required)
[OK] Ollama           11434 reachable, 9 models
[OK] Cohort Server    5100 healthy
[OK] Git              2.47.1
[OK] Data Directory   writable
[OK] Agent Personas   23 found
```

## Output Constraints

- Use ASCII only. No Unicode emojis.
- `[OK]` for healthy, `[!]` for warning, `[X]` for error/down.
- Keep compact -- one line per check.
