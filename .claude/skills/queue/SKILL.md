---
name: queue
description: View and manage the Cohort work queue. Reads data/work_queue.json directly -- no server needed.
argument-hint: <list|active|show|cancel> [options]
disable-model-invocation: true
---

# Cohort Work Queue CLI

Inspect and manage the work queue by reading `data/work_queue.json` directly. No running Cohort server required.

**Invoked with:** `$ARGUMENTS`

## Storage

The work queue lives at: `G:/cohort/data/work_queue.json`

If the file does not exist, the queue is empty — display "Work queue is empty." and stop.

Structure:
```json
{
  "version": "1.0",
  "last_updated": "...",
  "items": [
    {
      "id": "wq-abc123",
      "description": "...",
      "requester": "user",
      "priority": "critical|high|medium|low",
      "status": "queued|active|completed|failed|cancelled",
      "created_at": "...",
      "claimed_at": null,
      "completed_at": null,
      "agent_id": null,
      "depends_on": [],
      "result": null,
      "metadata": {}
    }
  ]
}
```

**Status lifecycle:** `queued` -> `active` -> `completed` | `failed` ; also `queued` -> `cancelled`

## Commands

Parse `$ARGUMENTS` to determine the action. Default (no args) = `list`.

### `/queue` or `/queue list` - List work queue items

**Steps:**
1. Read `G:/cohort/data/work_queue.json`. If file doesn't exist: "Work queue is empty."
2. Filter to non-terminal items (exclude `completed`, `failed`, `cancelled`) by default
3. Sort by priority (critical > high > medium > low), then created_at (oldest first)
4. Display as compact table:

```
Work Queue (2 actionable)
=========================
ID           Status   Pri    Agent       Age    Description (first 60 chars)
wq-abc123    active   high   researcher  2h     Process batch of agent assessments...
wq-def456    queued   medium --          15m    Generate weekly content digest...
```

- If `$ARGUMENTS` contains a status keyword (e.g., `/queue list failed`), filter to that status
- If `$ARGUMENTS` contains `all`, include terminal items (last 20)
- Age = human-readable relative time from created_at

### `/queue active` - Show currently active item

**Steps:**
1. Read `G:/cohort/data/work_queue.json`
2. Find item with `status: "active"`
3. If none: "No item currently active."
4. Show full detail:

```
Active Item: wq-abc123
=======================
Description: Process batch of agent assessments for Q1
Requester:   user
Priority:    high
Agent:       researcher
Claimed:     2h ago
Depends on:  none
```

### `/queue show <id>` - Full item detail

**Steps:**
1. Read `G:/cohort/data/work_queue.json`
2. Find item by ID (accept partial match — if user types `abc123`, match `wq-abc123`)
3. Display ALL fields in readable format:
   - Basic info (description, requester, priority, status)
   - Timeline (created, claimed, completed — full ISO timestamps)
   - Agent assignment
   - Dependencies
   - Result (if completed/failed)
   - Metadata (if any)

### `/queue cancel <id>` - Cancel a queued item

**Steps:**
1. Read `G:/cohort/data/work_queue.json`, find the item
2. Read old status BEFORE modifying
3. Verify status is `queued` — if active/completed/failed, print error with current status
4. Update the item:
   - Set `status` to `"cancelled"`
   - Set `completed_at` to current ISO 8601 UTC timestamp
5. Update `last_updated` in root object
6. Write back to `G:/cohort/data/work_queue.json`
7. Append audit log (see Audit Logging)
8. Print: `[OK] Item wq-xxx cancelled.`

## Audit Logging

**Every write operation (cancel) MUST be audit-logged.** After modifying work_queue.json, append a JSONL entry to `G:/cohort/data/skill_audit.jsonl` via Bash:

```bash
echo '{"timestamp":"<ISO8601_UTC>","skill":"queue","action":"cancel","item_id":"<id>","old_status":"<old>","new_status":"cancelled","requester":"claude_code"}' >> G:/cohort/data/skill_audit.jsonl
```

Read the old status BEFORE writing the update.

## Output Constraints

- Use ASCII only. No Unicode emojis. Use `[OK]`, `[X]`, `[!]` for status indicators.
- Keep table columns aligned with spaces, not tabs.
- Truncate descriptions at 60 chars in list view, show full in detail views.
- All timestamps display as relative ("2h ago", "3d ago") in list/summary views, full ISO in detail view.
- Priority ordering: critical=0, high=1, medium=2, low=3.
