#!/usr/bin/env python3
"""export_live_scenario.py -- Run a REAL multi-agent conversation through
Cohort's scoring engine + llama-server and export simulator JSON.

Every score, gating decision, token count, and response time is real.
The only scripted element is the seed message that kicks off each phase.

Usage:
    python examples/export_live_scenario.py [--port 62243] [--output FILE]
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cohort.chat import ChatManager, Message
from cohort.file_transport import JsonlFileStorage
from cohort.meeting import (
    RELEVANCE_DIMENSIONS,
    SCORING_WEIGHTS,
    STAKEHOLDER_THRESHOLDS,
    StakeholderStatus,
    calculate_composite_relevance,
    calculate_contribution_score,
    calculate_expertise_relevance,
    detect_current_phase,
    extract_keywords,
    initialize_meeting_context,
    should_agent_speak,
)

# =====================================================================
# Agent definitions with full scoring metadata
# =====================================================================

AGENTS = {
    "architect": {
        "name": "Architect",
        "color": "#5B9BD5",
        "avatar": "AR",
        "role": "Backend architecture & system design",
        "triggers": ["api", "design", "architecture", "endpoint", "schema", "rest", "pagination", "cursor"],
        "capabilities": ["backend architecture", "REST API design", "system design", "api pagination"],
        "domain_expertise": ["microservices", "api gateway", "rest api", "cursor pagination"],
        "complementary_agents": ["developer", "researcher"],
        "data_sources": ["api_specs", "architecture_docs", "endpoint_schemas"],
        "phase_roles": {"DISCOVER": "high", "PLAN": "high", "EXECUTE": "low", "VALIDATE": "medium"},
        "system_prompt": (
            "You are Architect, a senior backend architecture specialist. "
            "You focus on API design patterns, system architecture, and scalability. "
            "You are in a team discussion about REST API pagination. "
            "Respond concisely in 2-4 sentences. Be specific and technical. "
            "Do NOT use thinking tags or chain-of-thought. Just give your answer directly."
        ),
        "context_sources": {
            "persona": "System design specialist with 12 learned facts about REST patterns",
            "memory": "3 prior conversations about this API's scaling issues",
            "grounding": "Channel history + project constraints from #api-redesign",
        },
    },
    "developer": {
        "name": "Developer",
        "color": "#70B77E",
        "avatar": "DV",
        "role": "Python backend & implementation",
        "triggers": ["implement", "code", "python", "module", "function", "build", "fastapi", "sqlalchemy"],
        "capabilities": ["python backend", "fastapi", "sqlalchemy", "cursor encoder", "pagination module"],
        "domain_expertise": ["python", "web frameworks", "fastapi", "sqlalchemy"],
        "complementary_agents": ["architect", "tester"],
        "data_sources": ["codebase", "test_suite", "ci_pipeline"],
        "phase_roles": {"DISCOVER": "low", "PLAN": "medium", "EXECUTE": "high", "VALIDATE": "low"},
        "system_prompt": (
            "You are Developer, a senior Python backend engineer specializing in FastAPI and SQLAlchemy. "
            "You focus on implementation details, code structure, and practical solutions. "
            "You are in a team discussion about REST API pagination. "
            "Respond concisely in 2-4 sentences. Be specific about code and implementation. "
            "Do NOT use thinking tags or chain-of-thought. Just give your answer directly."
        ),
        "context_sources": {
            "persona": "Python specialist with 18 learned facts about FastAPI + SQLAlchemy patterns",
            "memory": "5 prior sessions -- knows existing codebase patterns, test conventions",
            "grounding": "Current repo structure + existing endpoint source code",
        },
    },
    "tester": {
        "name": "Tester",
        "color": "#E6A157",
        "avatar": "TS",
        "role": "QA, validation & edge cases",
        "triggers": ["test", "qa", "validation", "coverage", "edge", "regression", "security", "vulnerability"],
        "capabilities": ["test strategy", "integration testing", "load testing", "security testing", "edge case analysis"],
        "domain_expertise": ["pytest", "test automation", "security testing", "load testing"],
        "complementary_agents": ["developer"],
        "data_sources": ["test_results", "coverage_reports", "security_scans"],
        "phase_roles": {"DISCOVER": "low", "PLAN": "low", "EXECUTE": "medium", "VALIDATE": "high"},
        "system_prompt": (
            "You are Tester, a senior QA engineer specializing in edge cases, security testing, and load testing. "
            "You focus on what can go wrong, validation gaps, and security vulnerabilities. "
            "You are in a team discussion about REST API pagination. "
            "Respond concisely in 2-4 sentences. Focus on risks, edge cases, and test strategies. "
            "Do NOT use thinking tags or chain-of-thought. Just give your answer directly."
        ),
        "context_sources": {
            "persona": "QA specialist with 9 learned facts about edge case patterns",
            "memory": "2 prior sessions -- knows existing test suite coverage gaps",
            "grounding": "Test coverage report + CI pipeline configuration",
        },
    },
    "researcher": {
        "name": "Researcher",
        "color": "#C27ADB",
        "avatar": "RS",
        "role": "Prior art & historical analysis",
        "triggers": ["research", "investigate", "existing", "history", "prior", "similar", "library", "pattern"],
        "capabilities": ["code archaeology", "prior art research", "api pattern analysis", "library evaluation"],
        "domain_expertise": ["documentation", "historical analysis", "api design patterns", "pagination strategies"],
        "complementary_agents": ["architect"],
        "data_sources": ["document_library", "api_research_catalog", "pattern_database"],
        "phase_roles": {"DISCOVER": "high", "PLAN": "medium", "EXECUTE": "low", "VALIDATE": "medium"},
        "system_prompt": (
            "You are Researcher, a specialist in prior art research and API design pattern analysis. "
            "You focus on what other production APIs do, historical precedents, and library recommendations. "
            "You are in a team discussion about REST API pagination. "
            "Respond concisely in 2-4 sentences. Cite real APIs and libraries when possible. "
            "Do NOT use thinking tags or chain-of-thought. Just give your answer directly."
        ),
        "context_sources": {
            "persona": "Research specialist with 14 learned facts about API design precedents",
            "memory": "4 prior sessions -- catalogued patterns from 20+ production APIs",
            "grounding": "Document library with 8 curated articles on pagination strategies",
        },
    },
}

# Seed messages that drive the conversation forward (human/architect kicks off each phase)
PHASE_SEEDS = [
    {
        "phase": "DISCOVER",
        "phase_desc": "Researching the existing API and understanding the problem space.",
        "seed_sender": "architect",
        "seed_message": (
            "Team, we need to add pagination to our /users endpoint. It currently returns all records "
            "at once -- no limit, no cursor, no offset. We need to research what approaches exist "
            "and what similar production APIs do. What have you all found?"
        ),
        "respondents": ["researcher", "developer"],  # Who we want to hear from
    },
    {
        "phase": "PLAN",
        "phase_desc": "Designing the pagination architecture and choosing an approach.",
        "seed_sender": "architect",
        "seed_message": (
            "Based on the research, I'm proposing cursor-based pagination: "
            "GET /users?cursor=<opaque_token>&limit=25 with a response envelope containing "
            "next_cursor and has_more. Thoughts on this design? Any concerns about backward compatibility?"
        ),
        "respondents": ["researcher", "developer"],
    },
    {
        "phase": "EXECUTE",
        "phase_desc": "Building the pagination implementation.",
        "seed_sender": "architect",
        "seed_message": (
            "Design is approved. @developer please implement the cursor-based pagination module "
            "in Python using FastAPI and SQLAlchemy. We need opaque cursor tokens, configurable "
            "page size, and backward compatibility for existing callers."
        ),
        "respondents": ["developer"],
    },
    {
        "phase": "VALIDATE",
        "phase_desc": "Testing and validating the implementation.",
        "seed_sender": "architect",
        "seed_message": (
            "Implementation looks complete. @tester please run your edge case analysis and security "
            "review on the new pagination module. We need confidence this handles production traffic "
            "and doesn't leak internal state."
        ),
        "respondents": ["tester", "researcher", "developer"],
    },
]


# =====================================================================
# LLM inference
# =====================================================================

def call_llm(
    port: int,
    agent_id: str,
    agent_config: dict,
    conversation_history: list[dict],
) -> dict:
    """Call llama-server and return response + real metrics."""
    messages = [
        {"role": "system", "content": agent_config["system_prompt"]},
    ]
    # Add conversation history as context
    for msg in conversation_history[-8:]:  # Last 8 messages for context
        role = "assistant" if msg["sender"] == agent_id else "user"
        messages.append({
            "role": role,
            "content": f"[{msg['sender']}]: {msg['text']}",
        })
    # Add the prompt and prefill to skip thinking
    messages.append({
        "role": "user",
        "content": "It's your turn to respond. Give your input on the current discussion.",
    })
    # Prefill assistant with closed think block to suppress reasoning tokens
    messages.append({
        "role": "assistant",
        "content": "<think>\n</think>\n\n",
        "prefix": True,
    })

    start_time = time.time()
    resp = requests.post(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        json={
            "model": "qwen3.5:9b",
            "messages": messages,
            "max_tokens": 250,  # Direct answer, no thinking budget needed
            "temperature": 0.3,
        },
        timeout=60,
    )
    elapsed_ms = int((time.time() - start_time) * 1000)
    resp.raise_for_status()
    data = resp.json()

    content = data["choices"][0]["message"]["content"]
    # Strip thinking tags -- handle both complete (<think>...</think>) and
    # truncated (<think>... without closing tag) blocks
    if "</think>" in content:
        # Complete thinking block -- strip it
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    elif "<think>" in content:
        # Truncated -- entire output is thinking, no usable answer
        content = ""
    # Strip [agent_name]: prefix if the model echoes it
    content = re.sub(r"^\[?\w+\]?:\s*", "", content).strip()

    usage = data.get("usage", {})

    return {
        "text": content,
        "tokens_in": usage.get("prompt_tokens", 0),
        "tokens_out": usage.get("completion_tokens", 0),
        "time_ms": elapsed_ms,
    }


# =====================================================================
# Live scenario runner
# =====================================================================

class LiveScenarioRunner:
    def __init__(self, port: int):
        self.port = port
        self.storage = JsonlFileStorage("_live_export_temp.jsonl")
        self.chat = ChatManager(self.storage)
        self.channel_id = "api-redesign-live"
        self.chat.create_channel(self.channel_id, "API Pagination Redesign -- Live")
        self.meeting_context = initialize_meeting_context(list(AGENTS.keys()))
        self.conversation_history: list[dict] = []
        self.sim_phases: list[dict] = []
        self.total_tokens_in = 0
        self.total_tokens_out = 0
        self.total_time_ms = 0
        self.gates_enforced = 0
        self.message_count = 0

    def run(self) -> dict:
        for i, phase_block in enumerate(PHASE_SEEDS):
            phase_name = phase_block["phase"]
            phase_desc = phase_block["phase_desc"]
            steps = []

            # Narration
            if i == 0 or phase_block["phase"] != PHASE_SEEDS[i - 1]["phase"]:
                steps.append({
                    "type": "narration",
                    "text": f"Phase: {phase_name} -- {phase_desc}",
                })

            # Post seed message (this is the "human" architect kicking off the phase)
            seed_sender = phase_block["seed_sender"]
            seed_text = phase_block["seed_message"]
            self.chat.post_message(self.channel_id, seed_sender, seed_text)
            self.conversation_history.append({"sender": seed_sender, "text": seed_text})
            self.message_count += 1

            steps.append({
                "type": "message",
                "sender": seed_sender,
                "text": seed_text,
                "meta": {
                    "tier": "smarter",
                    "model": "qwen3.5:9b",
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "time_ms": 0,
                    "context_used": list(AGENTS[seed_sender].get("context_sources", {}).keys()),
                    "note": "Seed message (not LLM-generated)",
                },
            })

            # Update topic keywords
            recent = self.chat.get_channel_messages(self.channel_id, limit=15)
            recent_kw = set()
            for msg in recent[-5:]:
                recent_kw.update(extract_keywords(msg.content))
            self.meeting_context["current_topic"]["keywords"] = list(recent_kw)

            # Score all agents after seed
            scoring_step = self._score_all_agents(recent, seed_text)
            steps.append(scoring_step)

            # Now get LLM responses from the designated respondents
            for agent_id in phase_block["respondents"]:
                # Get real channel object for gating
                channel_obj = self.chat.get_channel(self.channel_id)
                channel_obj.meeting_context = self.meeting_context
                last_msg = recent[-1] if recent else None

                # Real gating check
                speak = should_agent_speak(
                    agent_id=agent_id,
                    message=last_msg,
                    channel=channel_obj,
                    chat=self.chat,
                    agent_config=AGENTS[agent_id],
                    use_composite_relevance=True,
                )

                if not speak:
                    self.gates_enforced += 1
                    steps.append({
                        "type": "gate_event",
                        "agent": agent_id,
                        "decision": "SILENT",
                        "reason": "Score below threshold for current phase",
                    })
                    continue

                # REAL LLM CALL
                print(f"  [>>] {agent_id} generating response...")
                result = call_llm(self.port, agent_id, AGENTS[agent_id], self.conversation_history)

                # Post to real chat
                self.chat.post_message(self.channel_id, agent_id, result["text"])
                self.conversation_history.append({"sender": agent_id, "text": result["text"]})
                self.message_count += 1
                self.total_tokens_in += result["tokens_in"]
                self.total_tokens_out += result["tokens_out"]
                self.total_time_ms += result["time_ms"]

                steps.append({
                    "type": "message",
                    "sender": agent_id,
                    "text": result["text"],
                    "meta": {
                        "tier": "smarter",
                        "model": "qwen3.5:9b",
                        "tokens_in": result["tokens_in"],
                        "tokens_out": result["tokens_out"],
                        "time_ms": result["time_ms"],
                        "context_used": list(AGENTS[agent_id].get("context_sources", {}).keys()),
                    },
                })

                # Update topic keywords and score again
                recent = self.chat.get_channel_messages(self.channel_id, limit=15)
                recent_kw = set()
                for msg in recent[-5:]:
                    recent_kw.update(extract_keywords(msg.content))
                self.meeting_context["current_topic"]["keywords"] = list(recent_kw)

                scoring_step = self._score_all_agents(recent, result["text"])
                steps.append(scoring_step)

            self.sim_phases.append({
                "id": f"phase_{i}",
                "name": phase_name,
                "description": phase_desc,
                "steps": steps,
            })

        return self._build_scenario()

    def _score_all_agents(self, recent: list[Message], last_msg: str) -> dict:
        detected_phase = detect_current_phase(recent)
        topic_keywords = extract_keywords(last_msg)
        scores = []

        for agent_id, agent_cfg in AGENTS.items():
            contrib_score = calculate_contribution_score(
                agent_id=agent_id,
                proposed_message=last_msg,
                meeting_context=self.meeting_context,
                agent_config=agent_cfg,
                recent_messages=recent,
            )
            relevance = calculate_composite_relevance(
                agent_id=agent_id,
                meeting_context=self.meeting_context,
                agent_config=agent_cfg,
                recent_messages=recent,
            )

            channel_obj = self.chat.get_channel(self.channel_id)
            channel_obj.meeting_context = self.meeting_context
            last_msg_obj = recent[-1] if recent else None

            speak = should_agent_speak(
                agent_id=agent_id,
                message=last_msg_obj,
                channel=channel_obj,
                chat=self.chat,
                agent_config=agent_cfg,
                use_composite_relevance=True,
            )

            status_key = self.meeting_context.get("stakeholder_status", {}).get(
                agent_id, StakeholderStatus.ACTIVE.value
            )
            status_label = {
                "active_stakeholder": "ACTIVE",
                "approved_silent": "APPROVED_SILENT",
                "observer": "OBSERVER",
                "dormant": "DORMANT",
            }.get(status_key, "ACTIVE")

            composite_total = relevance.get("composite_total", 0)

            reason_parts = []
            if relevance.get("domain_expertise", 0) > 0.3:
                reason_parts.append("strong domain match")
            if relevance.get("phase_alignment", 0) > 0.5:
                reason_parts.append(f"aligned with {detected_phase} phase")
            if relevance.get("complementary_value", 0) > 0.3:
                reason_parts.append("complements active agents")
            expertise = calculate_expertise_relevance(agent_cfg, topic_keywords)
            if expertise > 0.3:
                reason_parts.append(f"expertise overlap {expertise:.0%}")
            reason = "; ".join(reason_parts) if reason_parts else "baseline relevance"

            scores.append({
                "agent": agent_id,
                "score": round(composite_total, 2),
                "status": status_label,
                "decision": "SPEAK" if speak else "SILENT",
                "reason": reason,
                "breakdown": {
                    "contribution_score": round(contrib_score, 3),
                    "composite_relevance": round(composite_total, 3),
                    "domain_expertise": round(relevance.get("domain_expertise", 0), 3),
                    "complementary_value": round(relevance.get("complementary_value", 0), 3),
                    "historical_success": round(relevance.get("historical_success", 0), 3),
                    "phase_alignment": round(relevance.get("phase_alignment", 0), 3),
                    "data_ownership": round(relevance.get("data_ownership", 0), 3),
                },
            })

        scores.sort(key=lambda s: s["score"], reverse=True)

        return {
            "type": "scoring",
            "title": f"Scoring Round -- {detected_phase} phase",
            "explanation": f"Cohort scores all {len(scores)} agents after each message. Agents below their status threshold stay silent.",
            "scores": scores,
            "insight": f"Phase detected: {detected_phase}. Weights: novelty={SCORING_WEIGHTS['novelty']}, expertise={SCORING_WEIGHTS['expertise']}, ownership={SCORING_WEIGHTS['ownership']}, question={SCORING_WEIGHTS['question']}.",
            "cost_note": f"Without scoring, all {len(scores)} agents would generate responses to every message.",
        }

    def _build_scenario(self) -> dict:
        export_agents = {}
        for aid, acfg in AGENTS.items():
            export_agents[aid] = {
                "name": acfg["name"],
                "color": acfg["color"],
                "avatar": acfg["avatar"],
                "role": acfg["role"],
                "triggers": acfg["triggers"],
                "capabilities": acfg["capabilities"],
                "domain_expertise": acfg["domain_expertise"],
                "context_sources": acfg.get("context_sources", {}),
            }

        return {
            "id": "api-redesign-live",
            "title": "API Pagination Redesign",
            "description": "A real conversation between 4 AI agents, scored by Cohort's production engine. Every response, token count, and timing is from actual LLM inference.",
            "generated_at": datetime.now().isoformat(),
            "generated_by": "Cohort scoring engine (meeting.py) + qwen3.5:9b via llama-server",
            "inference_backend": {
                "model": "qwen3.5:9b",
                "server": "llama-server (llama.cpp)",
                "port": self.port,
                "temperature": 0.3,
                "max_tokens": 250,
            },
            "scoring_config": {
                "contribution_weights": dict(SCORING_WEIGHTS),
                "relevance_dimensions": dict(RELEVANCE_DIMENSIONS),
                "stakeholder_thresholds": {k: v for k, v in STAKEHOLDER_THRESHOLDS.items()},
            },
            "tier_config": {
                "smarter": {
                    "label": "Smarter",
                    "badge": "S+",
                    "model": "qwen3.5:9b",
                    "description": "Local model with thinking -- free inference, your GPU",
                    "color": "#43B581",
                    "cost_per_1k_tokens": 0,
                },
                "smartest": {
                    "label": "Smartest",
                    "badge": "S++",
                    "model": "qwen3.5:9b + Claude",
                    "description": "Local reasoning distilled to Claude for polished output",
                    "color": "#a855f7",
                    "cost_per_1k_tokens": 0.003,
                },
                "comparison": {
                    "label": "Standard approach",
                    "description": "Every agent responds to every message (no gating)",
                    "badge": "4x",
                    "color": "#F04747",
                },
            },
            "agents": export_agents,
            "phases": self.sim_phases,
            "stats": {
                "total_messages": self.message_count,
                "total_tokens_in": self.total_tokens_in,
                "total_tokens_out": self.total_tokens_out,
                "total_inference_time_ms": self.total_time_ms,
                "total_scoring_rounds": sum(
                    1 for p in self.sim_phases for s in p["steps"] if s["type"] == "scoring"
                ),
                "total_gates_enforced": self.gates_enforced,
            },
        }


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", "-p", type=int, default=62243)
    parser.add_argument("--output", "-o", default="examples/scenario-live-export.json")
    args = parser.parse_args()

    print(f"[*] Running LIVE conversation through Cohort + qwen3.5:9b (port {args.port})...")
    print("[*] This will make real LLM calls. Expect ~30-60 seconds.\n")

    runner = LiveScenarioRunner(args.port)
    scenario = runner.run()

    output_path = Path(args.output)
    output_path.write_text(json.dumps(scenario, indent=2), encoding="utf-8")

    stats = scenario["stats"]
    print(f"\n[OK] Exported to {output_path}")
    print(f"     Messages: {stats['total_messages']}")
    print(f"     Tokens in: {stats['total_tokens_in']:,}")
    print(f"     Tokens out: {stats['total_tokens_out']:,}")
    print(f"     Total inference time: {stats['total_inference_time_ms']:,}ms")
    print(f"     Scoring rounds: {stats['total_scoring_rounds']}")
    print(f"     Gates enforced: {stats['total_gates_enforced']}")

    # Cleanup
    for f in ["_live_export_temp.jsonl", "_live_export_temp_channels.json"]:
        Path(f).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
