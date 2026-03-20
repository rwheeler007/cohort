#!/usr/bin/env python3
"""export_simulator_scenario.py -- Run a real Cohort conversation and export
simulator-compatible JSON with all scoring data.

Produces a JSON file that can be used directly by the public website simulator.
Every score, gating decision, and phase detection comes from Cohort's actual
scoring engine (meeting.py) -- nothing is hand-authored.

Usage:
    python examples/export_simulator_scenario.py [--output FILE]

Defaults to writing: examples/scenario-export.json
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Add cohort to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cohort.chat import Channel, ChatManager, Message
from cohort.file_transport import JsonlFileStorage
from cohort.meeting import (
    SCORING_WEIGHTS,
    STAKEHOLDER_THRESHOLDS,
    RELEVANCE_DIMENSIONS,
    StakeholderStatus,
    calculate_contribution_score,
    calculate_composite_relevance,
    calculate_novelty,
    calculate_expertise_relevance,
    detect_current_phase,
    detect_topic_shift,
    extract_keywords,
    initialize_meeting_context,
    should_agent_speak,
)


# =====================================================================
# Scenario definition -- the scripted conversation
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
        "context_sources": {
            "persona": "Research specialist with 14 learned facts about API design precedents",
            "memory": "4 prior sessions -- catalogued patterns from 20+ production APIs",
            "grounding": "Document library with 8 curated articles on pagination strategies",
        },
    },
}

# Scripted messages -- the content is pre-written but scoring is computed live
SCRIPT = [
    # Phase 1: DISCOVER
    {
        "phase": "DISCOVER",
        "phase_desc": "Researching the existing API and understanding the problem space.",
        "messages": [
            ("architect", "Let's investigate the existing /users endpoint before we redesign it. We need to research what similar APIs do for pagination -- cursor-based, offset-based, or keyset approaches."),
            ("researcher", "Investigated 5 similar REST APIs (Stripe, GitHub, Slack, Shopify, Twilio). All use cursor-based pagination for large datasets. Offset pagination breaks at scale due to expensive OFFSET queries. The existing endpoint returns max 100 records with no pagination at all -- that's the gap."),
        ],
    },
    # Phase 2: PLAN
    {
        "phase": "PLAN",
        "phase_desc": "Designing the pagination architecture and choosing an approach.",
        "messages": [
            ("architect", "Based on the research, cursor-based pagination is the clear winner. Proposed design: GET /users?cursor=<opaque_token>&limit=25. Response envelope includes next_cursor and has_more. The cursor encodes the sort key + direction, not the raw DB ID."),
            ("researcher", "One design consideration from the prior art: Stripe returns a 'has_more' boolean alongside the cursor. GitHub uses Link headers. I'd recommend the Stripe approach -- it's explicit and doesn't rely on header parsing. Also, we should support both forward and backward cursors for UI pagination controls."),
        ],
    },
    # Phase 3: EXECUTE
    {
        "phase": "EXECUTE",
        "phase_desc": "Building the pagination implementation.",
        "messages": [
            ("architect", "The design is approved. @developer please implement the cursor-based pagination module in Python using FastAPI and SQLAlchemy. Key requirements: opaque cursor tokens, configurable page size with sensible defaults, and backward compatibility for callers not sending a cursor parameter."),
            ("developer", "Implementing now. Architecture: CursorCodec class handles encoding/decoding (base64 + HMAC signature to prevent tampering). New PaginatedResponse schema wraps results with next_cursor, prev_cursor, has_more, and total_count. The existing endpoint gets an optional cursor query param -- no cursor means page 1."),
            ("developer", "Implementation complete. The module handles forward/backward cursors, empty result sets, expired cursor tokens (returns 400 with clear error), and defaults to limit=25 when not specified. Added type hints throughout and docstrings for the public API surface."),
        ],
    },
    # Phase 4: VALIDATE
    {
        "phase": "VALIDATE",
        "phase_desc": "Testing and validating the implementation.",
        "messages": [
            ("architect", "Time to validate. @tester please run your edge case analysis and load testing on the new pagination module. We need confidence this handles production traffic."),
            ("tester", "Running validation suite. Testing edge cases: empty result sets return proper empty page with has_more=false. Expired cursors return 400 with actionable error message. Concurrent inserts during pagination -- cursors remain stable because they're keyset-based, not offset-based. Load test with 100k records: p50=12ms, p99=45ms. All clear."),
        ],
    },
    # Topic shift: security concern
    {
        "phase": "VALIDATE",
        "phase_desc": "Security vulnerability discovered during testing.",
        "messages": [
            ("tester", "CRITICAL FINDING: The cursor token contains the HMAC-signed database primary key. While the signature prevents tampering, it leaks the ID space -- an attacker can enumerate record IDs by observing cursor values across pages. This is an information disclosure vulnerability. We need to encrypt the cursor payload, not just sign it."),
            ("researcher", "Good catch. Researched cursor encryption patterns. Best practice is encrypt-then-sign: Fernet (AES-128-CBC + HMAC-SHA256) wraps the payload, then the outer cursor is base64url-encoded. Found 3 production implementations: itsdangerous TimedSerializer, PyJWT with encrypted claims, and cryptography.fernet. Recommend itsdangerous -- it's already in the FastAPI dependency tree."),
            ("developer", "Agreed. Switching from plain HMAC to itsdangerous TimedSerializer. The cursor interface stays identical -- only the internal encoding changes. Added a 24-hour TTL on cursors so stale pagination sessions get a clean 'cursor expired' error instead of silently returning wrong results. Two-line change in CursorCodec, all tests still pass."),
        ],
    },
]


# =====================================================================
# Export engine -- runs real scoring on scripted messages
# =====================================================================

class ScenarioExporter:
    """Run scripted messages through Cohort's real scoring engine and capture everything."""

    def __init__(self, agents: dict, script: list[dict]):
        self.agents_config = agents
        self.script = script

        # Set up real Cohort infrastructure
        self.storage = JsonlFileStorage("_export_temp.jsonl")
        self.chat = ChatManager(self.storage)
        self.channel_id = "api-redesign-export"
        self.chat.create_channel(self.channel_id, "API Pagination Redesign")

        # Meeting context for scoring
        self.meeting_context = initialize_meeting_context(list(agents.keys()))

        # Collected simulator phases
        self.sim_phases: list[dict] = []
        self.message_count = 0
        self.gated_count = 0

    def run(self) -> dict:
        """Execute the full script and return simulator-format JSON."""
        for i, phase_block in enumerate(self.script):
            phase_name = phase_block["phase"]
            phase_desc = phase_block["phase_desc"]

            steps = []

            # Narration step for phase intro
            if i == 0 or phase_block["phase"] != self.script[i - 1]["phase"]:
                steps.append({
                    "type": "narration",
                    "text": f"Phase: {phase_name} -- {phase_desc}",
                })

            for sender, text in phase_block["messages"]:
                # Post the message to real Cohort chat
                msg_id = self.chat.post_message(
                    channel_id=self.channel_id,
                    sender=sender,
                    content=text,
                )

                # Get real recent messages for scoring context
                recent = self.chat.get_channel_messages(self.channel_id, limit=15)
                # Get the actual Message object we just posted
                last_message = recent[-1] if recent else None

                # Get real channel object and attach meeting context
                channel_obj = self.chat.get_channel(self.channel_id)
                channel_obj.meeting_context = self.meeting_context

                # Update topic keywords from conversation (this drives domain_expertise scoring)
                recent_kw = set()
                for msg in recent[-5:]:
                    recent_kw.update(extract_keywords(msg.content))
                self.meeting_context["current_topic"]["keywords"] = list(recent_kw)

                # Detect real phase
                detected_phase = detect_current_phase(recent)

                # Check for topic shift
                topic_shifted = detect_topic_shift(recent, self.meeting_context)

                # Message step
                self.message_count += 1
                msg_step = {
                    "type": "message",
                    "sender": sender,
                    "text": text,
                    "meta": {
                        "tier": "smarter",
                        "model": "qwen3.5:9b",
                        "tokens_in": len(text.split()) * 4,  # Rough token estimate
                        "tokens_out": len(text.split()) * 3,
                        "time_ms": 1200 + len(text) * 2,
                        "context_used": list(self.agents_config[sender].get("context_sources", {}).keys()),
                    },
                }
                steps.append(msg_step)

                # Run real scoring for all agents after this message
                scoring_step = self._score_all_agents(
                    recent, detected_phase, text, last_message, channel_obj
                )
                if scoring_step:
                    steps.append(scoring_step)

                # Gate events
                gate_events = self._check_gates(recent, detected_phase)
                steps.extend(gate_events)

            self.sim_phases.append({
                "id": f"phase_{i}",
                "name": phase_name,
                "description": phase_desc,
                "steps": steps,
            })

        # Build the full scenario JSON
        return self._build_scenario()

    def _score_all_agents(
        self,
        recent: list[Message],
        phase: str,
        last_msg: str,
        message_obj: Message | None = None,
        channel_obj: Channel | None = None,
    ) -> dict | None:
        """Run Cohort's real scoring on all agents and return a scoring step."""
        scores = []
        topic_keywords = extract_keywords(last_msg)

        for agent_id, agent_cfg in self.agents_config.items():
            # Real contribution score from meeting.py
            contrib_score = calculate_contribution_score(
                agent_id=agent_id,
                proposed_message=last_msg,
                meeting_context=self.meeting_context,
                agent_config=agent_cfg,
                recent_messages=recent,
            )

            # Real composite relevance from meeting.py
            relevance = calculate_composite_relevance(
                agent_id=agent_id,
                meeting_context=self.meeting_context,
                agent_config=agent_cfg,
                recent_messages=recent,
            )

            # Real gating decision from meeting.py
            speak = should_agent_speak(
                agent_id=agent_id,
                message=message_obj,
                channel=channel_obj,
                chat=self.chat,
                agent_config=agent_cfg,
                use_composite_relevance=True,
            )

            # Get stakeholder status
            status_key = self.meeting_context.get("stakeholder_status", {}).get(
                agent_id, StakeholderStatus.ACTIVE.value
            )
            status_label = {
                "active_stakeholder": "ACTIVE",
                "approved_silent": "APPROVED_SILENT",
                "observer": "OBSERVER",
                "dormant": "DORMANT",
            }.get(status_key, "ACTIVE")

            # Build reason from real relevance breakdown
            reason_parts = []
            if relevance.get("domain_expertise", 0) > 0.3:
                reason_parts.append("strong domain match")
            if relevance.get("phase_alignment", 0) > 0.3:
                reason_parts.append(f"aligned with {phase} phase")
            if relevance.get("complementary_value", 0) > 0.3:
                reason_parts.append("complements active agents")
            expertise = calculate_expertise_relevance(agent_cfg, topic_keywords)
            if expertise > 0.3:
                reason_parts.append(f"expertise overlap {expertise:.0%}")
            novelty = calculate_novelty(last_msg, recent[-3:] if len(recent) > 3 else recent)
            if novelty < 0.4:
                reason_parts.append("low novelty vs recent messages")

            reason = "; ".join(reason_parts) if reason_parts else "baseline relevance"

            composite_total = relevance.get("composite_total", 0)
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

            if not speak:
                self.gated_count += 1

        # Sort by score descending
        scores.sort(key=lambda s: s["score"], reverse=True)

        return {
            "type": "scoring",
            "title": f"Scoring Round -- {phase} phase",
            "explanation": f"Cohort scores all {len(scores)} agents after each message. Agents below their status threshold stay silent.",
            "scores": scores,
            "insight": f"Phase detected: {phase}. Scoring weights: novelty={SCORING_WEIGHTS['novelty']}, expertise={SCORING_WEIGHTS['expertise']}, ownership={SCORING_WEIGHTS['ownership']}, question={SCORING_WEIGHTS['question']}.",
            "cost_note": f"Without scoring, all {len(scores)} agents would generate responses. Cohort gated {sum(1 for s in scores if s['decision'] == 'SILENT')} redundant responses here.",
        }

    def _check_gates(self, recent: list[Message], phase: str) -> list[dict]:
        """Generate gate event steps for any status changes."""
        events = []
        # Topic shift detection is handled internally by meeting_context updates
        # We just report current status
        for agent_id in self.agents_config:
            status = self.meeting_context.get("stakeholder_status", {}).get(
                agent_id, StakeholderStatus.ACTIVE.value
            )
            if status == StakeholderStatus.DORMANT.value:
                events.append({
                    "type": "gate_event",
                    "agent": agent_id,
                    "decision": "DORMANT",
                    "reason": f"Below relevance threshold for current {phase} phase topic",
                })
        return events

    def _build_scenario(self) -> dict:
        """Assemble the final simulator-format JSON."""
        # Strip internal keys from agents for the export
        export_agents = {}
        for aid, acfg in self.agents_config.items():
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
            "id": "api-redesign",
            "title": "API Pagination Redesign",
            "description": "A common API scaling problem. Watch how 4 specialists divide the work -- and how Cohort's scoring engine keeps agents silent when they'd add noise, not value. Your choices reshape who leads.",
            "generated_at": datetime.now().isoformat(),
            "generated_by": "Cohort scoring engine (meeting.py)",
            "scoring_config": {
                "contribution_weights": SCORING_WEIGHTS,
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
                "total_scoring_rounds": sum(
                    1 for p in self.sim_phases for s in p["steps"] if s["type"] == "scoring"
                ),
                "total_gates_enforced": self.gated_count,
            },
        }


# =====================================================================
# Main
# =====================================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Export Cohort simulator scenario with real scoring data")
    parser.add_argument("--output", "-o", default="examples/scenario-export.json",
                        help="Output JSON file path")
    args = parser.parse_args()

    print("[*] Running conversation through Cohort's scoring engine...")
    exporter = ScenarioExporter(AGENTS, SCRIPT)
    scenario = exporter.run()

    output_path = Path(args.output)
    output_path.write_text(json.dumps(scenario, indent=2), encoding="utf-8")

    print(f"[OK] Exported to {output_path}")
    print(f"     Messages: {scenario['stats']['total_messages']}")
    print(f"     Scoring rounds: {scenario['stats']['total_scoring_rounds']}")
    print(f"     Gates enforced: {scenario['stats']['total_gates_enforced']}")

    # Cleanup temp files
    for f in ["_export_temp.jsonl", "_export_temp_channels.json"]:
        Path(f).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
