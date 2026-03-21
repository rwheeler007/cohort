---
name: rate
description: Check escalation rate limits and cloud API availability for the smartest tier pipeline.
argument-hint: [status | escalations]
disable-model-invocation: true
allowed-tools: Bash, Read
---

# Cohort Rate Limit CLI

Check rate limiting status for the smartest tier pipeline. Cohort has two rate limit systems:

1. **Agent rate limit** — 5-second cooldown between responses per agent (prevents spam)
2. **Escalation rate limit** — Max escalations per hour to the 35B model (budget protection)

The smartest tier also depends on cloud API availability (Anthropic/OpenAI key configured and not rate-limited).

**Invoked with:** `$ARGUMENTS`

## Commands

Parse `$ARGUMENTS` to determine the action. Default (no args) = `status`.

### `/rate` or `/rate status` - Overall rate limit status

**Steps:**
1. Check if Cohort server is running:
   ```bash
   curl -s --max-time 5 http://localhost:5100/api/health
   ```
2. Check cloud API configuration by reading `data/settings.json`:
   - Look for `service_keys` array entries with `type: "anthropic"` or `type: "openai"`
   - Check if key is non-empty (don't display the actual key)
3. Display:

```
Cohort Rate Limits
==================
Agent cooldown:       5s per agent
Escalation limit:     30/hour (35B model)
Cloud API (Anthropic): configured
Cloud API (OpenAI):    not configured
```

Or if server is running and we can check live state:
```
Cohort Rate Limits
==================
Agent cooldown:       5s per agent
Escalation limit:     30/hour (35B model)
Escalations used:     3/30 this hour
Cloud API (Anthropic): configured
Cloud API (OpenAI):    not configured
Smartest tier:         available
```

If no cloud API keys configured:
```
[!] Smartest tier unavailable -- no cloud API key configured.
    Run: cohort secret set anthropic <your-api-key>
```

### `/rate escalations` - Escalation budget detail

**Steps:**
1. Read `data/tier_settings.json` for budget limits (if exists)
2. Display budget configuration:

```
Escalation Budget
=================
Per hour:    30 escalations (35B model)
Daily cap:   500,000 tokens
Monthly cap: 10,000,000 tokens
```

If no tier_settings.json: show defaults from code (30/hour, no token caps).

## Output Constraints

- Use ASCII only. No Unicode emojis.
- Use `[OK]`, `[X]`, `[!]` for status.
- NEVER display API key values. Only show "configured" or "not configured".
- Keep output compact.
