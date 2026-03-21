---
name: settings
description: View and update Cohort settings (model, timeouts, display, execution backend). Reads data/settings.json directly.
argument-hint: [show | set <key> <value>]
disable-model-invocation: true
allowed-tools: Read, Write, Bash
---

# Cohort Settings CLI

View and update Cohort runtime settings. Reads `data/settings.json` directly -- no server needed.

**Invoked with:** `$ARGUMENTS`

## Storage

Settings live at: `data/settings.json`

Key fields (safe to display):
- `model_name` — Default Ollama model
- `response_timeout` — Timeout in seconds for Claude CLI subprocess (default 300)
- `execution_backend` — `"cli"` (subprocess) or `"api"`
- `admin_mode` — Boolean, enables admin features
- `claude_enabled` — Boolean, enables Claude Code integration
- `force_to_claude_code` — Boolean, forces all routing through Claude Code
- `user_display_name` — Display name in UI
- `user_display_role` — Display role in UI
- `setup_completed` — Boolean, onboarding state

**Sensitive fields (NEVER display):**
- `api_key` — Legacy API key field
- `service_keys` — Array of encrypted API credentials
- Any field containing `key`, `secret`, `token`, or `_enc`

## Commands

Parse `$ARGUMENTS` to determine the action. Default (no args) = `show`.

### `/settings` or `/settings show` - Display current settings

**Steps:**
1. Read `data/settings.json`
2. Display safe fields only:

```
Cohort Settings
===============
model_name:          qwen3.5:2b
response_timeout:    300s
execution_backend:   cli
admin_mode:          true
claude_enabled:      true
force_to_claude_code: false
user_display_name:   Ryan W
setup_completed:     true
```

**IMPORTANT:** NEVER display `service_keys`, `api_key`, or any encrypted values. Show `service_keys: (3 configured)` with just the count.

### `/settings set <key> <value>` - Update a setting

**Steps:**
1. Parse key and value from `$ARGUMENTS`
2. **REJECT writes to sensitive fields:** If key is `api_key`, `service_keys`, or contains `key`/`secret`/`token`, print:
   ```
   [X] Cannot modify secrets via /settings. Use: cohort secret set <type> <key>
   ```
3. Read `data/settings.json`
4. Record old value
5. Update the specified key (parse booleans: `true`/`false`, integers where appropriate)
6. Write back to `data/settings.json`
7. Append audit entry to `data/skill_audit.jsonl`:
   ```bash
   echo '{"timestamp":"<ISO8601_UTC>","skill":"settings","action":"set","key":"<key>","old_value":"<old>","new_value":"<new>","requester":"claude_code"}' >> data/skill_audit.jsonl
   ```
8. Display:
   ```
   [OK] response_timeout set to 600

   Note: Takes effect on next request. No restart needed.
   ```

## Output Constraints

- Use ASCII only. No Unicode emojis. Use `[OK]`, `[X]`, `[!]` for status.
- NEVER display API keys, secrets, or encrypted values.
- Keep output compact.
