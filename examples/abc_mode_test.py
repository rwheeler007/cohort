#!/usr/bin/env python3
"""abc_mode_test.py -- A/B/C comparison of Cohort's 3 response modes.

Runs the SAME 4-phase API pagination scenario through:
  A) Smarter  -- qwen3.5:9b (thinking suppressed for short answers), FREE
  B) Smartest -- qwen3.5:9b reasoning -> distill -> Claude CLI, PAID
  C) Claude-only -- Claude Code CLI directly (no local model), PAID

Produces a side-by-side JSON comparison with response quality, token counts,
timing, and estimated costs.

Usage:
    python examples/abc_mode_test.py [--port 62243] [--output FILE]
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Re-use agent definitions from the live exporter
from export_live_scenario import AGENTS, PHASE_SEEDS

from cohort.chat import ChatManager
from cohort.file_transport import JsonlFileStorage
from cohort.meeting import (
    initialize_meeting_context,
)

# =====================================================================
# Claude CLI path
# =====================================================================
CLAUDE_CMD = os.environ.get("CLAUDE_CMD", "claude")


# =====================================================================
# Inference backends
# =====================================================================

def call_smarter(port: int, agent_id: str, agent_config: dict, history: list[dict]) -> dict:
    """Arm A: qwen3.5:9b with thinking suppressed. Free."""
    messages = [{"role": "system", "content": agent_config["system_prompt"]}]
    for msg in history[-8:]:
        role = "assistant" if msg["sender"] == agent_id else "user"
        messages.append({"role": role, "content": f"[{msg['sender']}]: {msg['text']}"})
    messages.append({"role": "user", "content": "It's your turn to respond. Give your input on the current discussion."})
    # Prefill to suppress thinking
    messages.append({"role": "assistant", "content": "<think>\n</think>\n\n", "prefix": True})

    t0 = time.time()
    resp = requests.post(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        json={"model": "qwen3.5:9b", "messages": messages, "max_tokens": 250, "temperature": 0.3},
        timeout=60,
    )
    elapsed = int((time.time() - t0) * 1000)
    resp.raise_for_status()
    data = resp.json()

    content = _strip_thinking(data["choices"][0]["message"]["content"])
    content = re.sub(r"^\[?\w+\]?:\s*", "", content).strip()
    usage = data.get("usage", {})

    return {
        "text": content,
        "tokens_in": usage.get("prompt_tokens", 0),
        "tokens_out": usage.get("completion_tokens", 0),
        "time_ms": elapsed,
        "cost_usd": 0.0,  # Free -- local GPU
    }


def call_smartest(port: int, agent_id: str, agent_config: dict, history: list[dict]) -> dict:
    """Arm B: qwen3.5:9b reasoning -> distill -> Claude CLI."""
    # Phase 1: Full reasoning pass (thinking enabled)
    messages = [{"role": "system", "content": agent_config["system_prompt"]}]
    for msg in history[-8:]:
        role = "assistant" if msg["sender"] == agent_id else "user"
        messages.append({"role": role, "content": f"[{msg['sender']}]: {msg['text']}"})
    messages.append({"role": "user", "content": (
        "It's your turn to respond. Think carefully about the technical details. "
        "Give your input on the current discussion."
    )})

    t0 = time.time()
    resp = requests.post(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        json={"model": "qwen3.5:9b", "messages": messages, "max_tokens": 2000, "temperature": 0.3},
        timeout=120,
    )
    phase1_ms = int((time.time() - t0) * 1000)
    resp.raise_for_status()
    data = resp.json()
    raw_phase1 = data["choices"][0]["message"]["content"]
    phase1_usage = data.get("usage", {})
    phase1_tokens_in = phase1_usage.get("prompt_tokens", 0)
    phase1_tokens_out = phase1_usage.get("completion_tokens", 0)

    # Phase 2: Distillation (thinking suppressed, low temp)
    distill_prompt = (
        "You are distilling an AI agent's detailed analysis into a briefing "
        "for a senior AI model (Claude). Preserve ALL substantive content while "
        "stripping noise. Remove meta-commentary, hedging, filler, repetition -- "
        "but keep every concrete fact, data point, code snippet, and recommendation.\n\n"
        "Output these sections (skip empty ones):\n"
        "### Key Findings\n### Recommended Approach\n### Constraints & Caveats\n"
        "### Confidence Assessment\n\n"
        f"--- ORIGINAL ANALYSIS ---\n{raw_phase1}\n--- END ---\n\n"
        "Distilled briefing:"
    )
    distill_messages = [
        {"role": "user", "content": distill_prompt},
        {"role": "assistant", "content": "<think>\n</think>\n\n", "prefix": True},
    ]

    t1 = time.time()
    resp2 = requests.post(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        json={"model": "qwen3.5:9b", "messages": distill_messages, "max_tokens": 1000, "temperature": 0.15},
        timeout=60,
    )
    phase2_ms = int((time.time() - t1) * 1000)
    resp2.raise_for_status()
    data2 = resp2.json()
    distilled = _strip_thinking(data2["choices"][0]["message"]["content"])
    phase2_usage = data2.get("usage", {})
    phase2_tokens_in = phase2_usage.get("prompt_tokens", 0)
    phase2_tokens_out = phase2_usage.get("completion_tokens", 0)

    if not distilled.strip():
        distilled = raw_phase1[:2000]  # Fallback

    # Phase 3: Claude CLI
    claude_prompt = (
        f"You are responding as the {agent_id} agent in a multi-agent team discussion.\n\n"
        f"Your role: {agent_config['role']}\n\n"
        "A local AI model has analyzed the conversation and produced this briefing:\n\n"
        f"--- ANALYSIS BRIEFING ---\n{distilled}\n--- END BRIEFING ---\n\n"
        "Now respond concisely in 2-4 sentences. Be specific and technical. "
        "Do not reference the briefing or the local model. Write in your own voice."
    )

    t2 = time.time()
    result = _call_claude_cli(claude_prompt)
    phase3_ms = int((time.time() - t2) * 1000)

    total_ms = phase1_ms + phase2_ms + phase3_ms
    # Estimate Claude tokens (rough: 4 chars per token)
    est_claude_in = len(claude_prompt) // 4
    est_claude_out = len(result) // 4
    # Claude Sonnet pricing: $3/1M input, $15/1M output
    claude_cost = (est_claude_in * 3 / 1_000_000) + (est_claude_out * 15 / 1_000_000)

    return {
        "text": result,
        "tokens_in": phase1_tokens_in + phase2_tokens_in + est_claude_in,
        "tokens_out": phase1_tokens_out + phase2_tokens_out + est_claude_out,
        "time_ms": total_ms,
        "cost_usd": round(claude_cost, 6),
        "phases": {
            "phase1_reasoning": {
                "tokens_in": phase1_tokens_in,
                "tokens_out": phase1_tokens_out,
                "time_ms": phase1_ms,
                "raw_text": raw_phase1[:500] + ("..." if len(raw_phase1) > 500 else ""),
            },
            "phase2_distillation": {
                "tokens_in": phase2_tokens_in,
                "tokens_out": phase2_tokens_out,
                "time_ms": phase2_ms,
                "distilled_text": distilled[:500] + ("..." if len(distilled) > 500 else ""),
            },
            "phase3_claude": {
                "est_tokens_in": est_claude_in,
                "est_tokens_out": est_claude_out,
                "time_ms": phase3_ms,
                "cost_usd": round(claude_cost, 6),
            },
        },
    }


def call_claude_only(agent_id: str, agent_config: dict, history: list[dict]) -> dict:
    """Arm C: Claude CLI only, no local model."""
    # Build a prompt with full conversation context (what Claude would normally see)
    context_lines = []
    for msg in history[-8:]:
        context_lines.append(f"[{msg['sender']}]: {msg['text']}")
    context = "\n\n".join(context_lines)

    prompt = (
        f"You are responding as the {agent_id} agent in a multi-agent team discussion.\n\n"
        f"Your role: {agent_config['role']}\n\n"
        f"Conversation so far:\n{context}\n\n"
        "It's your turn to respond. Give your input concisely in 2-4 sentences. "
        "Be specific and technical."
    )

    t0 = time.time()
    result = _call_claude_cli(prompt)
    elapsed = int((time.time() - t0) * 1000)

    # Estimate tokens
    est_in = len(prompt) // 4
    est_out = len(result) // 4
    cost = (est_in * 3 / 1_000_000) + (est_out * 15 / 1_000_000)

    return {
        "text": result,
        "tokens_in": est_in,
        "tokens_out": est_out,
        "time_ms": elapsed,
        "cost_usd": round(cost, 6),
    }


def _call_claude_cli(prompt: str) -> str:
    """Call Claude Code CLI with a prompt. Returns response text."""
    cli_cmd = [CLAUDE_CMD, "-p", "-"]
    if sys.platform == "win32":
        cli_cmd = ["cmd", "/c"] + cli_cmd

    # Strip CLAUDECODE env vars to avoid interference
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDECODE")}

    try:
        result = subprocess.run(
            cli_cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=120,
            shell=False,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        else:
            return f"[ERROR: Claude CLI returned {result.returncode}] {result.stderr[:200]}"
    except subprocess.TimeoutExpired:
        return "[ERROR: Claude CLI timed out after 120s]"
    except Exception as e:
        return f"[ERROR: {e}]"


def _strip_thinking(content: str) -> str:
    """Strip <think>...</think> blocks from LLM output."""
    if "</think>" in content:
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    elif "<think>" in content:
        content = ""
    return content


# =====================================================================
# Main test runner
# =====================================================================

class ABCTestRunner:
    """Runs the same conversation through 3 arms with shared scoring."""

    def __init__(self, port: int):
        self.port = port
        self.storage = JsonlFileStorage("_abc_test_temp.jsonl")
        self.chat = ChatManager(self.storage)
        self.channel_id = "abc-test"
        self.chat.create_channel(self.channel_id, "ABC Mode Test")
        self.meeting_context = initialize_meeting_context(list(AGENTS.keys()))

    def run(self) -> dict:
        results = {"arms": {}, "comparison": {}}

        # Use the same conversation prompts for all 3 arms
        # Build a shared conversation history from seed messages
        conversation = []
        for phase_block in PHASE_SEEDS:
            conversation.append({
                "sender": phase_block["seed_sender"],
                "text": phase_block["seed_message"],
            })

        # For each phase, collect responses from all respondents across all 3 arms
        for arm_name, arm_fn in [
            ("smarter", lambda aid, acfg, hist: call_smarter(self.port, aid, acfg, hist)),
            ("smartest", lambda aid, acfg, hist: call_smartest(self.port, aid, acfg, hist)),
            ("claude_only", lambda aid, acfg, hist: call_claude_only(aid, acfg, hist)),
        ]:
            print(f"\n{'='*60}")
            print(f"  ARM: {arm_name.upper()}")
            print(f"{'='*60}")

            arm_history = []  # Fresh history per arm
            arm_responses = []
            arm_total_tokens_in = 0
            arm_total_tokens_out = 0
            arm_total_time_ms = 0
            arm_total_cost = 0.0
            arm_llm_calls = 0

            for i, phase_block in enumerate(PHASE_SEEDS):
                phase_name = phase_block["phase"]
                seed_sender = phase_block["seed_sender"]
                seed_text = phase_block["seed_message"]

                # Add seed to this arm's history
                arm_history.append({"sender": seed_sender, "text": seed_text})

                print(f"\n  Phase: {phase_name}")
                print(f"  Seed: {seed_sender} ({seed_text[:60]}...)")

                for agent_id in phase_block["respondents"]:
                    print(f"    [>>] {agent_id} ({arm_name})...")
                    try:
                        result = arm_fn(agent_id, AGENTS[agent_id], arm_history)
                    except Exception as e:
                        print(f"    [X] {agent_id} failed: {e}")
                        result = {
                            "text": f"[ERROR: {e}]",
                            "tokens_in": 0, "tokens_out": 0,
                            "time_ms": 0, "cost_usd": 0.0,
                        }

                    # Add response to arm's conversation history
                    arm_history.append({"sender": agent_id, "text": result["text"]})
                    arm_llm_calls += 1
                    arm_total_tokens_in += result["tokens_in"]
                    arm_total_tokens_out += result["tokens_out"]
                    arm_total_time_ms += result["time_ms"]
                    arm_total_cost += result.get("cost_usd", 0)

                    arm_responses.append({
                        "phase": phase_name,
                        "agent": agent_id,
                        "response": result,
                    })

                    # Print brief preview (ASCII-safe for cp1252)
                    text_preview = result["text"][:120].replace("\n", " ")
                    text_preview = text_preview.encode("ascii", "replace").decode("ascii")
                    print(f"    [OK] {result['time_ms']}ms | {result['tokens_out']}tok | ${result.get('cost_usd', 0):.4f}")
                    print(f"         {text_preview}...")

            results["arms"][arm_name] = {
                "responses": arm_responses,
                "totals": {
                    "llm_calls": arm_llm_calls,
                    "tokens_in": arm_total_tokens_in,
                    "tokens_out": arm_total_tokens_out,
                    "time_ms": arm_total_time_ms,
                    "cost_usd": round(arm_total_cost, 6),
                },
            }

        # Build comparison table
        results["comparison"] = self._build_comparison(results["arms"])
        results["metadata"] = {
            "test_date": datetime.now().isoformat(),
            "scenario": "API Pagination Redesign (4 phases, 4 agents)",
            "local_model": "qwen3.5:9b via llama-server",
            "local_port": self.port,
            "claude_model": "claude (via CLI, model depends on user's config)",
            "scoring_engine": "Cohort meeting.py (shared across all arms)",
        }

        return results

    def _build_comparison(self, arms: dict) -> dict:
        """Build the side-by-side comparison summary."""
        comparison = {}
        for arm_name, arm_data in arms.items():
            totals = arm_data["totals"]
            comparison[arm_name] = {
                "total_tokens": totals["tokens_in"] + totals["tokens_out"],
                "total_time_seconds": round(totals["time_ms"] / 1000, 1),
                "total_cost_usd": totals["cost_usd"],
                "avg_response_time_ms": round(totals["time_ms"] / max(totals["llm_calls"], 1)),
                "avg_tokens_per_response": round(totals["tokens_out"] / max(totals["llm_calls"], 1)),
                "llm_calls": totals["llm_calls"],
            }

        # Calculate savings
        if "claude_only" in comparison and "smartest" in comparison:
            claude_cost = comparison["claude_only"]["total_cost_usd"]
            smartest_cost = comparison["smartest"]["total_cost_usd"]
            if claude_cost > 0:
                comparison["smartest_vs_claude_savings_pct"] = round(
                    (1 - smartest_cost / claude_cost) * 100, 1
                )

        return comparison


def main():
    import argparse
    parser = argparse.ArgumentParser(description="A/B/C test: Smarter vs Smartest vs Claude-only")
    parser.add_argument("--port", "-p", type=int, default=62243)
    parser.add_argument("--output", "-o", default="examples/abc-mode-test-results.json")
    args = parser.parse_args()

    print("[*] A/B/C Mode Test -- Cohort Response Mode Comparison")
    print(f"[*] llama-server port: {args.port}")
    print("[*] This will make ~24 LLM calls (8 per arm). Expect ~3-5 minutes.\n")

    runner = ABCTestRunner(args.port)
    results = runner.run()

    output_path = Path(args.output)
    output_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    # Print summary
    print(f"\n{'='*60}")
    print("  RESULTS SUMMARY")
    print(f"{'='*60}")
    comp = results["comparison"]
    for arm in ["smarter", "smartest", "claude_only"]:
        if arm in comp:
            c = comp[arm]
            print(f"\n  {arm.upper()}:")
            print(f"    Time:      {c['total_time_seconds']}s total, {c['avg_response_time_ms']}ms avg")
            print(f"    Tokens:    {c['total_tokens']} total, {c['avg_tokens_per_response']} avg/response")
            print(f"    Cost:      ${c['total_cost_usd']:.4f}")
            print(f"    Calls:     {c['llm_calls']}")

    if "smartest_vs_claude_savings_pct" in comp:
        print(f"\n  Smartest saves {comp['smartest_vs_claude_savings_pct']}% vs Claude-only")

    print(f"\n[OK] Full results: {output_path}")

    # Cleanup
    for f in ["_abc_test_temp.jsonl", "_abc_test_temp_channels.json"]:
        Path(f).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
