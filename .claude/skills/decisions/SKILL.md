---
name: decisions
description: View and manage agent active decisions. Reads agents/*/memory.json directly -- no server needed.
argument-hint: [list [agent] | show <agent> <id> | add <agent> <description> | close <agent> <id> [resolution] | open]
disable-model-invocation: true
---

# Agent Decisions CLI

View and manage active decisions tracked in agent memory. Decisions are multi-day items agents are tracking (e.g., "evaluate content strategy", "wait for API key approval", "decide on model tier for production").

**Invoked with:** `$ARGUMENTS`

## Storage

Decisions live inside each agent's memory file: `agents/<agent_id>/memory.json`

The `active_decisions` key holds an array:
```json
{
  "active_decisions": [
    {
      "id": "ad-3885ced0",
      "decision": "Evaluate whether smartest tier should use Claude or GPT-4 for code tasks",
      "status": "open",
      "scope": "model-selection",
      "opened": "2026-03-20T12:00:00+00:00",
      "updated": "2026-03-20T12:00:00+00:00",
      "deadline": null,
      "priority": "high",
      "notes": "Benchmark results pending. Compare cost vs quality.",
      "resolution": null
    }
  ]
}
```

**Status values:** `open`, `timed`, `deferred`, `closed`

## Commands

Parse `$ARGUMENTS` to determine the action. Default (no args) = `open` (show all open decisions across all agents).

### `/decisions` or `/decisions open` - All open decisions across all agents

**Steps:**
1. Glob for `agents/*/memory.json`
2. Read each file, extract `active_decisions` array (skip if key missing or empty)
3. Filter to `status: "open"` or `status: "timed"`
4. Sort by priority (high > medium > low), then by opened date (oldest first)
5. Display:

```
Open Decisions (3 across 2 agents)
===================================
Agent                  Pri    Scope            Opened   Decision (first 60 chars)
cohort_orchestrator    high   model-selection  2d ago   Evaluate whether smartest tier should use...
content_strategy_agent medium content-plan     5d ago   Decide on Q2 content calendar theme and f...
marketing_agent        low    budget           12d ago  Defer paid ads until organic baseline esta...
```

If no open decisions: `No open decisions across any agents.`

### `/decisions list <agent>` - All decisions for one agent

**Steps:**
1. Read `agents/<agent>/memory.json`
   - Accept shorthand matches (see Output Constraints for full map)
   - For inputs not in the shorthand map, match by substring against agent directory names
2. Extract `active_decisions`
3. Display all (any status), grouped by status:

```
Decisions for cohort_orchestrator (1 open, 0 closed)
=====================================================
[OPEN]
  ad-3885ced0  high  model-selection  2d ago
  Evaluate whether smartest tier should use Claude or GPT-4 for code tasks
  Notes: Benchmark results pending. Compare cost vs quality.

[CLOSED]
  (none)
```

### `/decisions show <agent> <id>` - Full detail for one decision

**Steps:**
1. Read the agent's memory.json, find the decision by ID (accept partial: `3885` matches `ad-3885ced0`)
2. Display all fields:

```
Decision: ad-3885ced0
=====================
Agent:      cohort_orchestrator
Status:     open
Priority:   high
Scope:      model-selection
Opened:     2026-03-20T12:00:00Z (2d ago)
Updated:    2026-03-20T12:00:00Z
Deadline:   (none)
Resolution: (none)

Decision:
  Evaluate whether smartest tier should use Claude or GPT-4 for code
  tasks based on upcoming benchmark results.

Notes:
  Benchmark results pending. Compare cost vs quality. Check assessment
  scores from data/assessment_results/.
```

### `/decisions add <agent> <description>` - Add a new decision

**Steps:**
1. Parse agent name (with shorthand matching)
2. Parse optional flags from description text:
   - `--scope <value>` or `scope:<value>`
   - `--priority <high|medium|low>` (default: medium)
   - `--deadline <YYYY-MM-DD>`
3. Generate ID: `ad-` + 8 random hex chars
4. Read agent's `memory.json`
5. Append new decision to `active_decisions` (create array if missing)
6. Write back
7. Append audit log (see Audit Logging)
8. Display: `[OK] Decision ad-xxxx added to <agent>. Status: open, priority: <pri>`

### `/decisions close <agent> <id> [resolution]` - Close a decision

**Steps:**
1. Read agent's `memory.json`, find decision by ID (partial match)
2. Set `status` to `"closed"`
3. Set `resolution` to the provided text (or `"Closed without resolution"` if none)
4. Set `updated` to current ISO 8601 UTC timestamp
5. Write back
6. Append audit log (see Audit Logging)
7. Display: `[OK] Decision ad-xxxx closed. Resolution: <text>`

## Audit Logging

**Every write operation (add, close) MUST be audit-logged.** After modifying an agent's memory.json, append a JSONL entry to `data/skill_audit.jsonl` via Bash:

For `add`:
```bash
echo '{"timestamp":"<ISO8601_UTC>","skill":"decisions","action":"add","agent":"<agent_id>","decision_id":"<id>","priority":"<pri>","scope":"<scope>","requester":"claude_code"}' >> data/skill_audit.jsonl
```

For `close`:
```bash
echo '{"timestamp":"<ISO8601_UTC>","skill":"decisions","action":"close","agent":"<agent_id>","decision_id":"<id>","resolution":"<text>","requester":"claude_code"}' >> data/skill_audit.jsonl
```

## Output Constraints

- Use ASCII only. No Unicode emojis. Use `[OK]`, `[X]`, `[!]`, `[OPEN]`, `[CLOSED]`, `[TIMED]`, `[DEFERRED]` for status.
- Keep list view compact. Full detail only in `show` command.
- Agent name shorthand mapping: `an`->analytics_agent, `bd`->brand_design_agent, `ca`->code_archaeologist, `camp`->campaign_orchestrator, `co`->cohort_orchestrator, `cod`->coding_orchestrator, `cs`->content_strategy_agent, `db`->database_developer, `doc`->documentation_agent, `em`->email_agent, `hw`->hardware_agent, `jd`->javascript_developer, `li`->linkedin, `mk`->marketing_agent, `mp`->media_production_agent, `pd`->python_developer, `qa`->qa_agent, `rd`->reddit, `sec`->security_agent, `sg`->setup_guide, `sup`->supervisor_agent, `sys`->system_coder, `wd`->web_developer. For others, match by substring.
