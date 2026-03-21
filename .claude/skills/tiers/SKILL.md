---
name: tiers
description: View and manage Cohort response tier model assignments (smart/smarter/smartest). Reads data/tier_settings.json and cohort/local/config.py.
argument-hint: [show | set <tier> <model> | budget | reset]
disable-model-invocation: true
allowed-tools: Read, Write, Bash, Glob
---

# Cohort Tier Manager

View and manage which models handle each response tier (smart, smarter, smartest).

**Invoked with:** `$ARGUMENTS`

## Storage

Tier settings live at: `data/tier_settings.json` (may not exist yet -- uses VRAM defaults from code).

Structure (when present):
```json
{
  "smart": {"primary": "qwen3.5:2b", "fallback": null},
  "smarter": {"primary": "qwen3.5:9b", "fallback": "smart"},
  "smartest": {"primary": "qwen3.5:9b", "fallback": "cloud_api"},
  "budget": {
    "daily_token_limit": 500000,
    "monthly_token_limit": 10000000,
    "escalation_per_hour": 30
  }
}
```

Defaults are VRAM-aware (defined in `cohort/local/config.py`). User overrides in `tier_settings.json` merge on top.

## Commands

Parse `$ARGUMENTS` to determine the action. Default (no args) = `show`.

### `/tiers` or `/tiers show` - Display current tier assignments

**Steps:**
1. Read `data/tier_settings.json` if it exists. If not, note "using VRAM defaults."
2. Also read `cohort/local/config.py` to find `RESPONSE_MODE_PARAMS` for the thinking/budget settings per tier.
3. Display:

```
Cohort Response Tiers
=====================
Tier       Model               Think   Budget   Fallback
smart      qwen3.5:2b          no      4K       (none)
smarter    qwen3.5:9b          yes     16K      -> smart
smartest   qwen3.5:9b          yes     16K      -> cloud_api

Source: VRAM defaults (no tier_settings.json override)
```

Or if tier_settings.json exists:
```
Source: data/tier_settings.json (user overrides active)
```

### `/tiers set <tier> <model>` - Override a tier's model

**Steps:**
1. Validate tier is one of: `smart`, `smarter`, `smartest`
2. Read `data/tier_settings.json` (create with defaults if missing)
3. Set `<tier>.primary` to the new model name
4. Write back to `data/tier_settings.json`
5. Append audit entry to `data/skill_audit.jsonl`:
   ```bash
   echo '{"timestamp":"<ISO8601_UTC>","skill":"tiers","action":"set","tier":"<tier>","old_model":"<old>","new_model":"<new>","requester":"claude_code"}' >> data/skill_audit.jsonl
   ```
6. Display:
```
[OK] smarter tier set to "qwen3.5:9b"

Note: Takes effect on next response. No restart needed.
```

### `/tiers budget` - Show budget limits

**Steps:**
1. Read `data/tier_settings.json`
2. Display budget section:
```
Token Budget
============
Daily limit:           500,000
Monthly limit:         10,000,000
Escalations per hour:  30
```

If no tier_settings.json: `No budget configured (unlimited).`

### `/tiers reset` - Clear overrides, revert to VRAM defaults

**Steps:**
1. Delete `data/tier_settings.json` if it exists
2. Display: `[OK] Tier overrides cleared. Using VRAM-detected defaults.`

## Output Constraints

- Use ASCII only. No Unicode emojis.
- Use `[OK]`, `[X]`, `[!]` for status.
- Keep tier table aligned and readable.
