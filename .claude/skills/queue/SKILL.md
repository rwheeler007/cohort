---
name: queue
description: View and manage the Cohort work queue. Reads data/work_queue.json directly -- no server needed.
argument-hint: [list | show <id> | active]
disable-model-invocation: true
allowed-tools: Read, Glob, Bash
---

# Cohort Work Queue CLI

Inspect the work queue by reading `data/work_queue.json` directly. No running Cohort server required.

**Invoked with:** `$ARGUMENTS`

## Storage

The work queue lives at: `data/work_queue.json`

Structure:
```json
{
  "version": "1.0",
  "last_updated": "...",
  "items": [
    {
      "id": "wq-abc123",
      "title": "...",
      "description": "...",
      "status": "queued|active|completed|failed",
      "priority": "high|medium|low",
      "created_at": "...",
      "claimed_at": null,
      "completed_at": null,
      "requester": "...",
      "assignee": null,
      "result": null
    }
  ]
}
```

**Status lifecycle:** `queued` -> `active` -> `completed` | `failed`

## Commands

Parse `$ARGUMENTS` to determine the action. Default (no args) = `list`.

### `/queue` or `/queue list` - List work queue items

**Steps:**
1. Read `data/work_queue.json`. If file doesn't exist: `Work queue is empty (no data/work_queue.json).`
2. Filter to non-terminal items (exclude `completed` and `failed`) by default
3. Sort by priority (high > medium > low), then created_at (oldest first)
4. Display as compact table:

```
Work Queue (2 actionable)
=========================
ID           Status   Pri    Age    Title (first 60 chars)
wq-abc123    active   high   2h     Process batch of agent assessments...
wq-def456    queued   medium 15m    Generate weekly content digest...
```

- If `$ARGUMENTS` contains `all`, include completed and failed items (last 20)
- Age = human-readable relative time from created_at

### `/queue active` - Show currently active item

**Steps:**
1. Read `data/work_queue.json`
2. Find item with `status: "active"`
3. If none: `No item currently active.`
4. Show full detail (all fields)

### `/queue show <id>` - Full item detail

**Steps:**
1. Read `data/work_queue.json`
2. Find item by ID (accept partial match)
3. Display all fields in readable format

## Output Constraints

- Use ASCII only. No Unicode emojis. Use `[OK]`, `[X]`, `[!]` for status.
- Keep table columns aligned with spaces.
- Truncate titles at 60 chars in list view.
- All timestamps display as relative in list view, full ISO in detail view.
