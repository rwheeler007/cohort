---
name: preheat
description: Warm up Ollama models so the first real prompt gets accurate results, not cold-model garbage.
argument-hint: [all | <model_name>]
disable-model-invocation: true
allowed-tools: Bash
---

# Cohort Preheat

Send a short warmup request to Ollama to prime GPU kernels and KV cache. Run this after a model loads cold (timeout expiry, model swap) so the first real question gets a quality response.

**Invoked with:** `$ARGUMENTS`

## Ollama Endpoint

Ollama runs on `localhost:11434`. The generate API is at `/api/generate`.

## Commands

Parse `$ARGUMENTS` to determine the target. Default (no args) = `all`.

### `/preheat` or `/preheat all` - Warm up the default model

**Steps:**
1. Discover which models are available:
   ```bash
   curl -s --max-time 5 http://localhost:11434/api/tags
   ```
   - If connection refused: `[X] Ollama not reachable on :11434`
   - Parse the JSON response. The `models` array contains `name` and `size` for each model.

2. Find the primary model. Check `data/tier_settings.json` for the smarter tier primary model. If no file, default to `qwen3.5:9b`. If that's not in the model list, use the first available model.

3. Send warmup request:
   ```bash
   curl -s --max-time 60 -X POST http://localhost:11434/api/generate \
     -d '{"model":"<model_name>","prompt":"Say OK.","stream":false,"options":{"num_predict":4,"temperature":0}}'
   ```
   - Measure wall-clock time
   - Parse response: check for `response` field

4. Display:
```
[OK] qwen3.5:9b warmed up in 2.3s
```

### `/preheat <model_name>` - Warm up a specific model

**Steps:**
1. Send warmup to the specified model directly
2. Display result with timing

## Timing Guide

- **< 2s** = model was already loaded in VRAM
- **2-15s** = cold load from disk into VRAM
- **15-60s** = very large model loading (30B+)

## Output Constraints

- Use ASCII only. No Unicode emojis.
- `[OK]` = warmed up successfully
- `[X]` = error (Ollama reachable but generation failed)
- `[--]` = Ollama not running
- One line per model. Include timing.
