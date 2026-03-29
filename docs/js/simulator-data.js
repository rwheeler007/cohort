(function() {
  "use strict";

  window.SIMULATOR_SCENARIOS = [
    {
      "id": "api-redesign",
      "title": "API Pagination Redesign",
      "description": "A common API scaling problem. Watch how 4 specialists divide the work -- and how Cohort's scoring engine keeps 2 of them silent when they'd add noise, not value. Your choices reshape who leads.",

      "tier_config": {
        "smarter": {
          "label": "Smarter",
          "badge": "S+",
          "model": "qwen3.5:9b",
          "description": "Local model with thinking -- free inference, your GPU",
          "color": "#43B581",
          "cost_per_1k_tokens": 0
        },
        "smartest": {
          "label": "Smartest",
          "badge": "S++",
          "model": "qwen3.5:9b + Claude",
          "description": "Local reasoning distilled to Claude for polished output",
          "color": "#a855f7",
          "cost_per_1k_tokens": 0.003
        },
        "comparison": {
          "label": "Without Cohort",
          "description": "Every agent responds to every message (no gating)",
          "badge": "4x",
          "color": "#F04747"
        }
      },

      "agents": {
        "architect": {
          "name": "Architect",
          "color": "#5B9BD5",
          "avatar": "AR",
          "role": "Backend architecture & system design",
          "triggers": ["api", "design", "architecture", "endpoint", "schema", "rest"],
          "capabilities": ["backend architecture", "REST API design", "system design"],
          "domain_expertise": ["microservices", "api gateway"],
          "context_sources": {
            "persona": "System design specialist with 12 learned facts about REST patterns",
            "memory": "3 prior conversations about this API's scaling issues",
            "grounding": "Channel history + project constraints from #api-redesign"
          }
        },
        "developer": {
          "name": "Developer",
          "color": "#70B77E",
          "avatar": "DV",
          "role": "Python backend & implementation",
          "triggers": ["implement", "code", "python", "module", "function", "build"],
          "capabilities": ["python backend", "fastapi", "sqlalchemy"],
          "domain_expertise": ["python", "web frameworks"],
          "context_sources": {
            "persona": "Python specialist with 18 learned facts about FastAPI + SQLAlchemy patterns",
            "memory": "5 prior sessions -- knows existing codebase patterns, test conventions",
            "grounding": "Current repo structure + existing endpoint source code"
          }
        },
        "tester": {
          "name": "Tester",
          "color": "#E6A157",
          "avatar": "TS",
          "role": "QA, validation & edge cases",
          "triggers": ["test", "qa", "validation", "coverage", "edge", "regression"],
          "capabilities": ["test strategy", "integration testing", "load testing"],
          "domain_expertise": ["pytest", "test automation"],
          "context_sources": {
            "persona": "QA specialist with 9 learned facts about edge case patterns",
            "memory": "2 prior sessions -- knows existing test suite coverage gaps",
            "grounding": "Test coverage report + CI pipeline configuration"
          }
        },
        "researcher": {
          "name": "Researcher",
          "color": "#C27ADB",
          "avatar": "RS",
          "role": "Prior art & historical analysis",
          "triggers": ["research", "investigate", "existing", "history", "prior", "similar"],
          "capabilities": ["code archaeology", "prior art research"],
          "domain_expertise": ["documentation", "historical analysis"],
          "context_sources": {
            "persona": "Research specialist with 14 learned facts about API design precedents",
            "memory": "4 prior sessions -- catalogued patterns from 20+ production APIs",
            "grounding": "Document library with 8 curated articles on pagination strategies"
          }
        }
      },

      "phases": [
        {
          "id": "opening",
          "name": "DISCOVER",
          "description": "Researching the existing API and understanding the problem space.",
          "steps": [
            {
              "type": "narration",
              "text": "The architect opens the discussion. Cohort's scoring engine evaluates all four agents to decide who's most relevant."
            },
            {
              "type": "message",
              "sender": "architect",
              "text": "Let's investigate the existing /users endpoint before we redesign it. We need to research what similar APIs do for pagination.",
              "meta": {
                "tier": "smarter",
                "model": "qwen3.5:9b",
                "tokens_in": 2841,
                "tokens_out": 342,
                "time_ms": 11200,
                "context_used": ["persona", "memory", "grounding"]
              }
            },
            {
              "type": "scoring",
              "title": "Who should respond?",
              "explanation": "The message contains 'investigate', 'research', 'existing' -- all DISCOVER-phase keywords that match the Researcher's triggers.",
              "scores": [
                { "agent": "researcher", "score": 0.61, "status": "ACTIVE", "decision": "SPEAK", "reason": "3 trigger matches (research, investigate, existing) + DISCOVER phase alignment" },
                { "agent": "architect", "score": 0.52, "status": "ACTIVE", "decision": "SPEAK", "reason": "Domain owner (api, endpoint) but just spoke -- novelty penalty" },
                { "agent": "developer", "score": 0.35, "status": "ACTIVE", "decision": "SPEAK", "reason": "Low trigger match, waiting for implementation phase" },
                { "agent": "tester", "score": 0.22, "status": "OBSERVER", "decision": "SILENT", "reason": "No trigger matches. Observer threshold is 0.80 -- gated." }
              ],
              "insight": "The Tester is gated as OBSERVER. Score 0.22 is well below the 0.80 threshold needed for observers to speak. This prevents the tester from jumping in prematurely.",
              "cost_note": "Without Cohort, all 4 agents would respond here. With Cohort, only the Researcher is recommended. That's 3 unnecessary responses avoided."
            },
            {
              "type": "gate_event",
              "agent": "tester",
              "decision": "GATED",
              "reason": "Score 0.22 < observer threshold 0.80"
            },
            {
              "type": "message",
              "sender": "researcher",
              "text": "Investigated 5 similar REST APIs. All use cursor-based pagination for large datasets. The existing endpoint returns max 100 records with no pagination at all -- that's the gap.",
              "meta": {
                "tier": "smarter",
                "model": "qwen3.5:9b",
                "tokens_in": 3456,
                "tokens_out": 487,
                "time_ms": 15800,
                "context_used": ["persona", "memory", "grounding"]
              }
            },
            {
              "type": "narration",
              "text": "Good research. Now the team needs to decide on an approach. This is where YOUR choice changes the conversation."
            }
          ]
        },
        {
          "id": "choice_1",
          "type": "choice",
          "prompt": "The architect asks for your input on the migration strategy. What matters more to your team?",
          "options": [
            {
              "id": "compat",
              "label": "Backward Compatibility",
              "description": "Keep existing callers working. Pagination is opt-in. No breaking changes.",
              "consequence_preview": "Conservative approach -- more research, more constraints on the developer."
            },
            {
              "id": "clean",
              "label": "Clean Break",
              "description": "Redesign the endpoint properly. Existing callers migrate to v2.",
              "consequence_preview": "Aggressive approach -- developer gets the green light faster."
            }
          ]
        },
        {
          "id": "plan_compat",
          "name": "PLAN",
          "branch": "compat",
          "description": "Designing a backward-compatible pagination layer.",
          "steps": [
            {
              "type": "narration",
              "text": "You chose backward compatibility. Watch how this shifts the conversation -- the Researcher stays relevant longer because migration risk needs investigation."
            },
            {
              "type": "message",
              "sender": "architect",
              "text": "OK, backward compatibility is the priority. We need to design this so existing callers that don't pass a cursor parameter get the current behavior. The pagination envelope wraps around the existing response format.",
              "meta": {
                "tier": "smarter",
                "model": "qwen3.5:9b",
                "tokens_in": 3102,
                "tokens_out": 412,
                "time_ms": 13400,
                "context_used": ["persona", "memory", "grounding"]
              }
            },
            {
              "type": "message",
              "sender": "researcher",
              "text": "Important finding: we have 14 internal services and 3 external partners calling /users. Two partners have strict response schema validation. Any envelope change will break them unless we version the endpoint.",
              "meta": {
                "tier": "smartest",
                "model": "qwen3.5:9b + Claude",
                "tokens_in": 4210,
                "tokens_out": 523,
                "tokens_claude": 1850,
                "time_ms": 22400,
                "context_used": ["persona", "memory", "grounding"],
                "smartest_note": "Escalated to Smartest -- constraint analysis benefits from Claude's reasoning on dependency impact."
              }
            },
            {
              "type": "scoring",
              "title": "Constraint discovery changes the scores",
              "explanation": "The Researcher's finding introduces real constraints. The Developer can't just build -- they need to navigate compatibility requirements.",
              "scores": [
                { "agent": "researcher", "score": 0.58, "status": "ACTIVE", "decision": "SPEAK", "reason": "High-value constraint discovery. Novelty remains high because each finding is new information." },
                { "agent": "architect", "score": 0.55, "status": "ACTIVE", "decision": "SPEAK", "reason": "Design authority on versioning strategy. 'design', 'endpoint' triggers active." },
                { "agent": "developer", "score": 0.31, "status": "ACTIVE", "decision": "SPEAK", "reason": "Barely above 0.30 threshold. Can speak but isn't the priority." },
                { "agent": "tester", "score": 0.25, "status": "OBSERVER", "decision": "SILENT", "reason": "Still waiting. Validation phase hasn't started." }
              ],
              "insight": "Compare this to the Clean Break branch: there, the Developer scores 0.54 and the Researcher drops to 0.29. Your choice kept the Researcher relevant and constrained the Developer.",
              "cost_note": "Tester still gated. Developer can speak but Cohort ranked them last. In a naive system, all 4 would respond -- that's 2 wasted responses with ~3K tokens each."
            },
            {
              "type": "message",
              "sender": "architect",
              "text": "Two-phase approach: Phase 1 adds ?cursor support but keeps the flat array response for callers without cursor. Phase 2 migrates to envelope format with a 90-day deprecation window. @developer the implementation needs a response format switch based on whether cursor is present.",
              "meta": {
                "tier": "smartest",
                "model": "qwen3.5:9b + Claude",
                "tokens_in": 4580,
                "tokens_out": 618,
                "tokens_claude": 2100,
                "time_ms": 25100,
                "context_used": ["persona", "memory", "grounding"],
                "smartest_note": "Escalated to Smartest -- migration design benefits from Claude synthesizing backward-compat constraints with the Researcher's findings."
              }
            },
            {
              "type": "message",
              "sender": "developer",
              "text": "Understood. That means two serialization paths in the endpoint. I'll implement a response adapter that detects legacy vs. paginated mode. More complex but doable.",
              "meta": {
                "tier": "smarter",
                "model": "qwen3.5:9b",
                "tokens_in": 3890,
                "tokens_out": 356,
                "time_ms": 11600,
                "context_used": ["persona", "memory", "grounding"]
              }
            },
            {
              "type": "scoring",
              "title": "Developer's complexity acknowledgment",
              "explanation": "The developer accepted a harder implementation. 'implement', 'code' keywords shift toward EXECUTE phase.",
              "scores": [
                { "agent": "developer", "score": 0.52, "status": "ACTIVE", "decision": "SPEAK", "reason": "EXECUTE keywords + direct involvement. Rising." },
                { "agent": "architect", "score": 0.48, "status": "ACTIVE", "decision": "SPEAK", "reason": "Still relevant but dropping -- design decisions are settling." },
                { "agent": "researcher", "score": 0.33, "status": "APPROVED_SILENT", "decision": "SILENT", "reason": "Moved to APPROVED_SILENT. Constraints delivered. Threshold now 0.70 -- score too low to keep talking." },
                { "agent": "tester", "score": 0.28, "status": "OBSERVER", "decision": "SILENT", "reason": "Still gated. Approaching but not yet." }
              ],
              "insight": "The Researcher gets APPROVED_SILENT status -- they contributed valuable constraints and Cohort recognized it. They won't talk again unless something changes.",
              "cost_note": "Researcher gated after contributing 2 high-value messages. Without Cohort, they'd keep talking -- diminishing returns with increasing cost."
            },
            {
              "type": "gate_event",
              "agent": "researcher",
              "decision": "APPROVED_SILENT",
              "reason": "Contributed 2 high-value messages. Novelty exhausted for current topic."
            }
          ]
        },
        {
          "id": "plan_clean",
          "name": "PLAN",
          "branch": "clean",
          "description": "Designing a clean v2 endpoint.",
          "steps": [
            {
              "type": "narration",
              "text": "You chose a clean break. Watch how this ACCELERATES the Developer -- less constraint research needed, faster path to implementation."
            },
            {
              "type": "message",
              "sender": "architect",
              "text": "Clean break it is. We'll create /v2/users with cursor-based pagination as the default. The v1 endpoint stays frozen -- no changes, just a deprecation header. Clean contract from day one.",
              "meta": {
                "tier": "smarter",
                "model": "qwen3.5:9b",
                "tokens_in": 3102,
                "tokens_out": 445,
                "time_ms": 14300,
                "context_used": ["persona", "memory", "grounding"]
              }
            },
            {
              "type": "scoring",
              "title": "Clean break shifts power to the Developer",
              "explanation": "With no backward-compat constraints, the conversation moves straight to implementation. 'create', 'build' are EXECUTE-phase keywords.",
              "scores": [
                { "agent": "developer", "score": 0.54, "status": "ACTIVE", "decision": "SPEAK", "reason": "'create' + 'build' trigger EXECUTE phase. Developer's domain. Fast-tracked." },
                { "agent": "architect", "score": 0.51, "status": "ACTIVE", "decision": "SPEAK", "reason": "Design authority on the v2 contract. Still active." },
                { "agent": "researcher", "score": 0.29, "status": "APPROVED_SILENT", "decision": "SILENT", "reason": "No more research needed. Clean break means fewer unknowns. Threshold 0.70." },
                { "agent": "tester", "score": 0.24, "status": "OBSERVER", "decision": "SILENT", "reason": "Still waiting for something to test." }
              ],
              "insight": "The Researcher is already silenced! In the Compat branch, they're still actively contributing constraints at this point. Your choice gave the Developer a 2-message head start.",
              "cost_note": "2 agents gated immediately. Without Cohort, you'd be paying for Researcher and Tester responses that add nothing to a clean-break discussion."
            },
            {
              "type": "gate_event",
              "agent": "researcher",
              "decision": "APPROVED_SILENT",
              "reason": "No constraint research needed for clean-break approach."
            },
            {
              "type": "message",
              "sender": "developer",
              "text": "Nice, clean slate. I'll implement cursor-based pagination with: opaque cursor tokens, configurable page size (default 25, max 100), and a standard envelope: { data: [], next_cursor: string|null, has_more: boolean }.",
              "meta": {
                "tier": "smarter",
                "model": "qwen3.5:9b",
                "tokens_in": 3650,
                "tokens_out": 512,
                "time_ms": 16500,
                "context_used": ["persona", "memory", "grounding"]
              }
            },
            {
              "type": "message",
              "sender": "architect",
              "text": "Good structure. Add a total_count field to the envelope -- clients need it for progress indicators. And the cursor should encode both the sort field and direction so it's self-describing.",
              "meta": {
                "tier": "smarter",
                "model": "qwen3.5:9b",
                "tokens_in": 3980,
                "tokens_out": 389,
                "time_ms": 12500,
                "context_used": ["persona", "grounding"]
              }
            },
            {
              "type": "message",
              "sender": "developer",
              "text": "Total count is expensive on large tables -- it forces a COUNT(*) on every request. I'll add it as an optional parameter: ?include=total_count. Only computed when explicitly requested.",
              "meta": {
                "tier": "smartest",
                "model": "qwen3.5:9b + Claude",
                "tokens_in": 4120,
                "tokens_out": 467,
                "tokens_claude": 1680,
                "time_ms": 21800,
                "context_used": ["persona", "memory", "grounding"],
                "smartest_note": "Escalated to Smartest -- pushback on architect's suggestion benefits from Claude's nuanced reasoning about database performance trade-offs."
              }
            },
            {
              "type": "scoring",
              "title": "Productive disagreement",
              "explanation": "Developer pushed back on architect's suggestion with a valid technical reason. Both agents remain ACTIVE because this is high-value exchange.",
              "scores": [
                { "agent": "developer", "score": 0.58, "status": "ACTIVE", "decision": "SPEAK", "reason": "Leading the implementation discussion. High novelty -- each message adds new technical detail." },
                { "agent": "architect", "score": 0.45, "status": "ACTIVE", "decision": "SPEAK", "reason": "Still contributing design refinements but developer is leading." },
                { "agent": "tester", "score": 0.31, "status": "ACTIVE", "decision": "SPEAK", "reason": "Promoted from OBSERVER! 'validation', 'test' keywords emerging in context." },
                { "agent": "researcher", "score": 0.22, "status": "DORMANT", "decision": "SILENT", "reason": "Moved to DORMANT. Fully disengaged from this clean-break discussion." }
              ],
              "insight": "In the Compat branch, the Researcher is still scoring 0.33 here. In Clean Break, they've dropped to DORMANT at 0.22. The Developer has had 2 extra productive exchanges. Same agents, completely different dynamics."
            }
          ]
        },
        {
          "id": "execute_shared",
          "name": "EXECUTE",
          "description": "Implementation underway. The developer is building.",
          "steps": [
            {
              "type": "narration",
              "text": "Implementation is underway. The developer builds the pagination module. Now something unexpected happens during testing..."
            },
            {
              "type": "message",
              "sender": "developer",
              "text": "Implementation complete. The module handles forward/backward cursors, empty result sets, and invalid cursor tokens with proper HTTP 400 error codes.",
              "meta": {
                "tier": "smarter",
                "model": "qwen3.5:9b",
                "tokens_in": 4210,
                "tokens_out": 378,
                "time_ms": 12200,
                "context_used": ["persona", "memory", "grounding"]
              }
            },
            {
              "type": "scoring",
              "title": "Developer's novelty is dropping",
              "explanation": "The developer has spoken multiple times. Cohort's novelty scoring penalizes repetition -- even good contributions lose score after 2-3 messages.",
              "scores": [
                { "agent": "tester", "score": 0.48, "status": "ACTIVE", "decision": "SPEAK", "reason": "'test', 'validation', 'edge' keywords. Tester's time is coming." },
                { "agent": "developer", "score": 0.39, "status": "ACTIVE", "decision": "SPEAK", "reason": "Novelty penalty after 2+ messages. Score dropping despite owning the implementation." },
                { "agent": "architect", "score": 0.35, "status": "ACTIVE", "decision": "SPEAK", "reason": "Review authority. Moderate relevance." },
                { "agent": "researcher", "score": 0.18, "status": "DORMANT", "decision": "SILENT", "reason": "Deeply dormant. Threshold 1.00 -- effectively locked out." }
              ],
              "insight": "This is Cohort's loop prevention. Without scoring, the developer would keep talking about implementation details. The novelty penalty naturally creates space for the tester.",
              "cost_note": "Researcher has been gated for multiple rounds. Each round saves ~3.5K tokens of unnecessary inference."
            },
            {
              "type": "message",
              "sender": "tester",
              "text": "Starting validation. Edge cases: empty result sets, expired cursors, concurrent modifications during pagination, and load test with 10k records. Also running regression against existing callers.",
              "meta": {
                "tier": "smarter",
                "model": "qwen3.5:9b",
                "tokens_in": 3780,
                "tokens_out": 445,
                "time_ms": 14300,
                "context_used": ["persona", "memory", "grounding"]
              }
            },
            {
              "type": "narration",
              "text": "During testing, the tester discovers something. Your second choice determines what they find -- and this changes which agents re-engage."
            }
          ]
        },
        {
          "id": "choice_2",
          "type": "choice",
          "prompt": "The tester runs comprehensive validation. What do they discover?",
          "options": [
            {
              "id": "security",
              "label": "Security Vulnerability",
              "description": "The cursor token is a base64-encoded database ID -- information disclosure risk.",
              "consequence_preview": "Watch a DORMANT agent wake up when the topic shifts."
            },
            {
              "id": "performance",
              "label": "Performance Cliff",
              "description": "Pagination works fine until page 400+, then response times spike to 12 seconds.",
              "consequence_preview": "The developer stays hot -- this is an implementation problem."
            }
          ]
        },
        {
          "id": "discovery_security",
          "name": "TOPIC SHIFT",
          "branch": "security",
          "description": "A security vulnerability shifts the conversation back to research mode.",
          "steps": [
            {
              "type": "narration",
              "text": "Security finding. Watch what happens to the DORMANT Researcher..."
            },
            {
              "type": "message",
              "sender": "tester",
              "text": "CRITICAL: The cursor token is a base64-encoded database row ID. Anyone can decode it, enumerate records, and infer data volume. This is an information disclosure vulnerability. We need to investigate encryption options and research how other APIs handle this.",
              "meta": {
                "tier": "smartest",
                "model": "qwen3.5:9b + Claude",
                "tokens_in": 4650,
                "tokens_out": 612,
                "tokens_claude": 2340,
                "time_ms": 28500,
                "context_used": ["persona", "memory", "grounding"],
                "smartest_note": "Escalated to Smartest -- security vulnerability analysis benefits from Claude's broader knowledge of information disclosure patterns."
              }
            },
            {
              "type": "scoring",
              "title": "Topic shift re-engages the Researcher!",
              "explanation": "'investigate', 'research', 'similar' -- the security finding pulled the conversation back to DISCOVER-phase language. The Researcher was DORMANT (threshold 1.00) but topic shift triggers a full re-evaluation.",
              "scores": [
                { "agent": "researcher", "score": 0.61, "status": "ACTIVE", "decision": "SPEAK", "reason": "RE-ENGAGED from DORMANT! Topic shift detected: 'investigate' + 'research' match triggers. Status reset to ACTIVE." },
                { "agent": "tester", "score": 0.55, "status": "ACTIVE", "decision": "SPEAK", "reason": "Owns this finding. High ownership score." },
                { "agent": "architect", "score": 0.48, "status": "ACTIVE", "decision": "SPEAK", "reason": "Security architecture implications. Re-engaged." },
                { "agent": "developer", "score": 0.42, "status": "ACTIVE", "decision": "SPEAK", "reason": "Will need to fix this. Implementation expertise relevant." }
              ],
              "insight": "This is Cohort's topic shift detection in action. The Researcher went from DORMANT (score 0.18, threshold 1.00 -- effectively locked out) to ACTIVE (score 0.61) in a single message. The system recognized that 'investigate encryption' and 'research how other APIs handle this' are the Researcher's exact domain.",
              "cost_note": "The Researcher was gated for 4+ rounds, saving tokens each time. Now they're re-engaged because they're genuinely needed. Cohort doesn't just cut agents -- it brings them back at the right moment."
            },
            {
              "type": "gate_event",
              "agent": "researcher",
              "decision": "RE-ENGAGED",
              "reason": "Topic shift to research/investigation domain. Status: DORMANT -> ACTIVE."
            },
            {
              "type": "message",
              "sender": "researcher",
              "text": "Good catch. Researched cursor encryption in similar APIs. Best practice is HMAC-signed opaque tokens. Three options: itsdangerous (lightweight, Flask ecosystem), PyJWT (standard, more overhead), or Fernet (symmetric encryption, overkill for cursors). Recommendation: itsdangerous.",
              "meta": {
                "tier": "smartest",
                "model": "qwen3.5:9b + Claude",
                "tokens_in": 5120,
                "tokens_out": 534,
                "tokens_claude": 2180,
                "time_ms": 26300,
                "context_used": ["persona", "memory", "grounding"],
                "smartest_note": "Escalated to Smartest -- encryption library comparison benefits from Claude's training on security best practices across ecosystems."
              }
            },
            {
              "type": "message",
              "sender": "developer",
              "text": "Agreed on itsdangerous. The cursor interface won't change -- just the encoding layer. I'll swap base64 for HMAC-signed tokens. Existing tests still pass because they use the cursor value opaquely.",
              "meta": {
                "tier": "smarter",
                "model": "qwen3.5:9b",
                "tokens_in": 4380,
                "tokens_out": 389,
                "time_ms": 12500,
                "context_used": ["persona", "memory", "grounding"]
              }
            },
            {
              "type": "scoring",
              "title": "Resolution -- team converges",
              "explanation": "The security issue is resolved collaboratively. All four agents contributed at exactly the right moments.",
              "scores": [
                { "agent": "developer", "score": 0.51, "status": "ACTIVE", "decision": "SPEAK", "reason": "Owns the fix. Implementation lead again." },
                { "agent": "researcher", "score": 0.44, "status": "APPROVED_SILENT", "decision": "SILENT", "reason": "Research delivered. Stepping back naturally." },
                { "agent": "tester", "score": 0.40, "status": "ACTIVE", "decision": "SPEAK", "reason": "Needs to verify the fix." },
                { "agent": "architect", "score": 0.36, "status": "ACTIVE", "decision": "SPEAK", "reason": "Monitoring but not driving." }
              ],
              "insight": "Compare this to the Performance branch: there, the Researcher NEVER re-engages. Same four agents, completely different conversation because of what the tester found."
            }
          ]
        },
        {
          "id": "discovery_performance",
          "name": "DEEP EXECUTE",
          "branch": "performance",
          "description": "A performance problem keeps the conversation in implementation mode.",
          "steps": [
            {
              "type": "narration",
              "text": "Performance problem. Watch how the Developer STAYS dominant -- no topic shift means the Researcher stays dormant."
            },
            {
              "type": "message",
              "sender": "tester",
              "text": "Performance cliff at scale: pagination works fine through page 400, then response times spike from 45ms to 12,000ms. The cursor-based query is doing a full table scan when the offset exceeds the index buffer. We need to optimize the query implementation.",
              "meta": {
                "tier": "smarter",
                "model": "qwen3.5:9b",
                "tokens_in": 4100,
                "tokens_out": 498,
                "time_ms": 16100,
                "context_used": ["persona", "memory", "grounding"]
              }
            },
            {
              "type": "scoring",
              "title": "No topic shift -- Developer stays hot",
              "explanation": "'optimize', 'implementation', 'query' -- these are EXECUTE-phase keywords. The Researcher's triggers ('research', 'investigate') are NOT present. No re-engagement.",
              "scores": [
                { "agent": "developer", "score": 0.56, "status": "ACTIVE", "decision": "SPEAK", "reason": "'optimize', 'query', 'implementation' -- all Developer triggers. Novelty restored by new problem." },
                { "agent": "tester", "score": 0.49, "status": "ACTIVE", "decision": "SPEAK", "reason": "Owns the finding. Performance testing expertise." },
                { "agent": "architect", "score": 0.38, "status": "ACTIVE", "decision": "SPEAK", "reason": "Index strategy is architectural. Moderate relevance." },
                { "agent": "researcher", "score": 0.18, "status": "DORMANT", "decision": "SILENT", "reason": "STILL DORMANT. No research/investigation keywords. Threshold 1.00 remains." }
              ],
              "insight": "The Researcher stays at 0.18 DORMANT. In the Security branch, they're at 0.61 ACTIVE right now. Same agent, same conversation history up to this point -- the only difference is what the tester found.",
              "cost_note": "Researcher still gated. Without Cohort, they'd generate a ~3.5K token response about 'researching performance optimization patterns' that adds nothing the Developer doesn't already know."
            },
            {
              "type": "message",
              "sender": "developer",
              "text": "Found the issue: keyset pagination, not offset pagination. Instead of WHERE id > cursor_id, the query was using OFFSET which degrades linearly. Switching to keyset: WHERE (sort_col, id) > (cursor_sort_val, cursor_id) with a composite index.",
              "meta": {
                "tier": "smartest",
                "model": "qwen3.5:9b + Claude",
                "tokens_in": 4890,
                "tokens_out": 567,
                "tokens_claude": 2250,
                "time_ms": 27200,
                "context_used": ["persona", "memory", "grounding"],
                "smartest_note": "Escalated to Smartest -- keyset vs offset pagination trade-offs benefit from Claude's deep understanding of database query optimization."
              }
            },
            {
              "type": "message",
              "sender": "architect",
              "text": "Good fix. But this changes the cursor contract -- it now needs to encode both the sort column value AND the row ID. Make sure the cursor token format is versioned so we can evolve it.",
              "meta": {
                "tier": "smarter",
                "model": "qwen3.5:9b",
                "tokens_in": 4320,
                "tokens_out": 401,
                "time_ms": 12900,
                "context_used": ["persona", "grounding"]
              }
            },
            {
              "type": "message",
              "sender": "developer",
              "text": "Already handled. The cursor is a signed JSON blob: {v: 1, sort_val: ..., id: ..., dir: 'asc'}. Version field means we can change the format without breaking existing cursors.",
              "meta": {
                "tier": "smarter",
                "model": "qwen3.5:9b",
                "tokens_in": 4580,
                "tokens_out": 423,
                "time_ms": 13600,
                "context_used": ["persona", "memory"]
              }
            },
            {
              "type": "scoring",
              "title": "Developer-Architect loop resolves the issue",
              "explanation": "This was a technical problem with a technical solution. The right two agents handled it. The Researcher and Tester stayed appropriately quiet.",
              "scores": [
                { "agent": "developer", "score": 0.48, "status": "ACTIVE", "decision": "SPEAK", "reason": "Resolved the issue. Novelty dropping again after 2 messages." },
                { "agent": "architect", "score": 0.44, "status": "ACTIVE", "decision": "SPEAK", "reason": "Design review contribution. Appropriate involvement." },
                { "agent": "tester", "score": 0.41, "status": "ACTIVE", "decision": "SPEAK", "reason": "Needs to re-run performance tests. Relevant." },
                { "agent": "researcher", "score": 0.15, "status": "DORMANT", "decision": "SILENT", "reason": "Never re-engaged. This was the right outcome -- no research was needed." }
              ],
              "insight": "In the Security branch, all 4 agents contributed. Here, only 3 did meaningful work. Cohort correctly determined the Researcher had nothing to add to a performance optimization problem."
            }
          ]
        },
        {
          "id": "outcome",
          "name": "OUTCOME",
          "description": "What your team built.",
          "steps": [
            {
              "type": "outcome_summary"
            }
          ]
        }
      ],

      "outcomes": {
        "compat+security": {
          "title": "Conservative & Secure",
          "summary": "Your team built a backward-compatible pagination layer with HMAC-signed cursor tokens. All 4 agents contributed meaningfully.",
          "stats": {
            "agents_who_spoke": 4,
            "messages_total": 12,
            "researcher_status_changes": 3,
            "topic_shifts_detected": 1,
            "gates_enforced": 4
          },
          "cost_comparison": {
            "cohort_smarter_tokens": 42580,
            "cohort_smartest_tokens": 8470,
            "cohort_total_cost": 0.025,
            "without_cohort_tokens": 78400,
            "without_cohort_cost": 0.235,
            "savings_pct": 89
          },
          "agent_journeys": {
            "architect": ["ACTIVE", "ACTIVE", "ACTIVE", "ACTIVE"],
            "developer": ["ACTIVE", "ACTIVE(constrained)", "ACTIVE", "ACTIVE"],
            "tester": ["OBSERVER", "OBSERVER", "ACTIVE", "ACTIVE"],
            "researcher": ["ACTIVE", "ACTIVE", "DORMANT", "ACTIVE(re-engaged)"]
          },
          "key_moment": "The Researcher's constraint discovery (14 internal callers, 2 with strict schemas) directly shaped the two-phase migration approach. Without backward-compat choice, this constraint never surfaces."
        },
        "compat+performance": {
          "title": "Conservative & Optimized",
          "summary": "Your team built a backward-compatible pagination layer with keyset optimization. The Researcher contributed constraints but never re-engaged after going silent.",
          "stats": {
            "agents_who_spoke": 4,
            "messages_total": 12,
            "researcher_status_changes": 2,
            "topic_shifts_detected": 0,
            "gates_enforced": 3
          },
          "cost_comparison": {
            "cohort_smarter_tokens": 43800,
            "cohort_smartest_tokens": 6030,
            "cohort_total_cost": 0.018,
            "without_cohort_tokens": 82100,
            "without_cohort_cost": 0.246,
            "savings_pct": 93
          },
          "agent_journeys": {
            "architect": ["ACTIVE", "ACTIVE", "ACTIVE", "ACTIVE"],
            "developer": ["ACTIVE", "ACTIVE(constrained)", "ACTIVE", "ACTIVE"],
            "tester": ["OBSERVER", "OBSERVER", "ACTIVE", "ACTIVE"],
            "researcher": ["ACTIVE", "ACTIVE", "APPROVED_SILENT", "DORMANT"]
          },
          "key_moment": "The Developer had to navigate both backward-compatibility constraints AND a performance fix. The hardest path -- but the most realistic for production systems."
        },
        "clean+security": {
          "title": "Fast & Secure",
          "summary": "Your team shipped a clean v2 endpoint with HMAC-signed cursors. The Developer moved fast, then the Researcher re-engaged for the security fix.",
          "stats": {
            "agents_who_spoke": 4,
            "messages_total": 11,
            "researcher_status_changes": 3,
            "topic_shifts_detected": 1,
            "gates_enforced": 3
          },
          "cost_comparison": {
            "cohort_smarter_tokens": 38200,
            "cohort_smartest_tokens": 8450,
            "cohort_total_cost": 0.025,
            "without_cohort_tokens": 71500,
            "without_cohort_cost": 0.215,
            "savings_pct": 88
          },
          "agent_journeys": {
            "architect": ["ACTIVE", "ACTIVE", "ACTIVE", "ACTIVE"],
            "developer": ["ACTIVE", "ACTIVE(leading)", "ACTIVE", "ACTIVE"],
            "tester": ["OBSERVER", "ACTIVE(promoted early)", "ACTIVE", "ACTIVE"],
            "researcher": ["ACTIVE", "DORMANT", "DORMANT", "ACTIVE(re-engaged)"]
          },
          "key_moment": "The Researcher went fully DORMANT in the PLAN phase -- no constraints to research. Then snapped back to ACTIVE when the security finding required investigation. Cohort's topic shift detection at its most dramatic."
        },
        "clean+performance": {
          "title": "Fast & Lean",
          "summary": "Your team shipped a clean v2 endpoint with keyset pagination. Only 3 agents did meaningful work -- the Researcher correctly stayed silent throughout.",
          "stats": {
            "agents_who_spoke": 3,
            "messages_total": 10,
            "researcher_status_changes": 1,
            "topic_shifts_detected": 0,
            "gates_enforced": 4
          },
          "cost_comparison": {
            "cohort_smarter_tokens": 36400,
            "cohort_smartest_tokens": 3930,
            "cohort_total_cost": 0.012,
            "without_cohort_tokens": 68200,
            "without_cohort_cost": 0.205,
            "savings_pct": 94
          },
          "agent_journeys": {
            "architect": ["ACTIVE", "ACTIVE", "ACTIVE", "ACTIVE"],
            "developer": ["ACTIVE", "ACTIVE(leading)", "ACTIVE", "ACTIVE"],
            "tester": ["OBSERVER", "ACTIVE(promoted early)", "ACTIVE", "ACTIVE"],
            "researcher": ["ACTIVE", "DORMANT", "DORMANT", "DORMANT"]
          },
          "key_moment": "The Researcher never spoke after the opening phase. This is Cohort working correctly -- not every agent needs to contribute to every conversation. The system saved tokens and time by keeping irrelevant expertise silent."
        }
      }
    },

    {
      "id": "oauth2-security",
      "title": "OAuth2 Token Security Review",
      "description": "A security-critical auth change where getting it wrong means breached tokens. Watch the scoring engine promote the Security Agent and silence the generalists -- the right expert at the right moment.",

      "tier_config": {
        "smarter": {
          "label": "Smarter",
          "badge": "S+",
          "model": "qwen3.5:9b",
          "description": "Local model with thinking -- free inference, your GPU",
          "color": "#43B581",
          "cost_per_1k_tokens": 0
        },
        "smartest": {
          "label": "Smartest",
          "badge": "S++",
          "model": "qwen3.5:9b + Claude",
          "description": "Local reasoning distilled to Claude for polished output",
          "color": "#a855f7",
          "cost_per_1k_tokens": 0.003
        },
        "comparison": {
          "label": "Without Cohort",
          "description": "Every agent responds to every message (no gating)",
          "badge": "4x",
          "color": "#F04747"
        }
      },

      "agents": {
        "security": {
          "name": "Security Agent",
          "color": "#E74C3C",
          "avatar": "SC",
          "role": "Threat modeling & security review",
          "triggers": ["security", "vulnerability", "auth", "token", "encryption", "oauth", "csrf"],
          "capabilities": ["threat modeling", "OWASP", "token security"],
          "domain_expertise": ["authentication", "authorization"],
          "context_sources": {
            "persona": "Security specialist with 15 learned facts about OAuth2 threat vectors and token lifecycle",
            "memory": "4 prior sessions -- knows existing auth system weaknesses and past incidents",
            "grounding": "Channel history + OWASP guidelines from #security-review"
          }
        },
        "developer": {
          "name": "Python Developer",
          "color": "#3498DB",
          "avatar": "PY",
          "role": "Backend implementation",
          "triggers": ["implement", "code", "python", "function", "build", "endpoint", "redis"],
          "capabilities": ["python backend", "fastapi", "redis", "implementation"],
          "domain_expertise": ["python", "web frameworks"],
          "context_sources": {
            "persona": "Python specialist with 18 learned facts about FastAPI, Redis, and async patterns",
            "memory": "6 prior sessions -- knows existing auth middleware, token store architecture",
            "grounding": "Current repo structure + existing OAuth2 endpoint source code"
          }
        },
        "qa": {
          "name": "QA Agent",
          "color": "#27AE60",
          "avatar": "QA",
          "role": "Testing strategy & edge cases",
          "triggers": ["test", "qa", "validation", "coverage", "regression", "edge"],
          "capabilities": ["test strategy", "integration testing", "security testing"],
          "domain_expertise": ["pytest", "test automation"],
          "context_sources": {
            "persona": "QA specialist with 11 learned facts about auth flow testing and race condition detection",
            "memory": "3 prior sessions -- knows existing test coverage gaps in auth module",
            "grounding": "Test coverage report + CI security scan configuration"
          }
        },
        "architect": {
          "name": "Architect",
          "color": "#5B9BD5",
          "avatar": "AR",
          "role": "System design & integration",
          "triggers": ["api", "design", "architecture", "schema", "integration", "system"],
          "capabilities": ["system architecture", "API design", "distributed systems"],
          "domain_expertise": ["microservices", "OAuth2 standards"],
          "context_sources": {
            "persona": "System design specialist with 13 learned facts about distributed auth and token propagation",
            "memory": "5 prior sessions -- knows service mesh topology and cross-service auth flow",
            "grounding": "Architecture docs + RFC 6749/6819 reference material"
          }
        }
      },

      "phases": [
        {
          "id": "opening",
          "name": "DISCOVER",
          "description": "Analyzing the current token implementation and identifying risks.",
          "steps": [
            {
              "type": "narration",
              "text": "The Security Agent opens by analyzing the current refresh token implementation. Cohort's scoring engine evaluates all four agents to decide who responds."
            },
            {
              "type": "message",
              "sender": "security",
              "text": "Current implementation stores refresh tokens as opaque strings in PostgreSQL with no rotation. A stolen token grants indefinite access until manual revocation. We need to assess the attack surface and determine where token replay vulnerabilities exist.",
              "meta": {
                "tier": "smarter",
                "model": "qwen3.5:9b",
                "tokens_in": 2960,
                "tokens_out": 385,
                "time_ms": 12400,
                "context_used": ["persona", "memory", "grounding"]
              }
            },
            {
              "type": "scoring",
              "title": "Who should respond?",
              "explanation": "The message contains 'token', 'vulnerability', 'auth', 'security' -- heavy DISCOVER-phase language that saturates the Security Agent's own triggers, but also hits the Architect's 'system', 'design' domain.",
              "scores": [
                { "agent": "security", "score": 0.64, "status": "ACTIVE", "decision": "SPEAK", "reason": "4 trigger matches (security, vulnerability, auth, token) + DISCOVER phase alignment. Owns this domain." },
                { "agent": "architect", "score": 0.49, "status": "ACTIVE", "decision": "SPEAK", "reason": "'system' + 'integration' implied. Architecture-level concerns about token propagation." },
                { "agent": "developer", "score": 0.32, "status": "ACTIVE", "decision": "SPEAK", "reason": "Low trigger match -- 'endpoint' barely implied. Waiting for implementation specifics." },
                { "agent": "qa", "score": 0.19, "status": "OBSERVER", "decision": "SILENT", "reason": "No test/validation keywords present. Observer threshold 0.80 -- gated until testing phase." }
              ],
              "insight": "QA is gated as OBSERVER. Score 0.19 is well below the 0.80 threshold. Cohort prevents the tester from jumping in before there's anything to test.",
              "cost_note": "Without Cohort, all 4 agents would respond. With scoring, QA stays silent and Developer is low-priority. That's 2 unnecessary responses avoided."
            },
            {
              "type": "gate_event",
              "agent": "qa",
              "decision": "GATED",
              "reason": "Score 0.19 < observer threshold 0.80"
            },
            {
              "type": "message",
              "sender": "architect",
              "text": "The bigger concern is token propagation. Our system has 6 microservices that accept refresh tokens for service-to-service auth. Rotation means all downstream services need to handle token invalidation atomically, or we get cascading 401s.",
              "meta": {
                "tier": "smarter",
                "model": "qwen3.5:9b",
                "tokens_in": 3280,
                "tokens_out": 412,
                "time_ms": 13200,
                "context_used": ["persona", "memory", "grounding"]
              }
            },
            {
              "type": "narration",
              "text": "The Security Agent and Architect have framed the problem. Your first choice will determine the team's approach to rotation strategy."
            }
          ]
        },
        {
          "id": "choice_1",
          "type": "choice",
          "prompt": "How should the team approach token rotation?",
          "options": [
            {
              "id": "gradual",
              "label": "Gradual Migration",
              "description": "Phase in rotation alongside existing tokens. No breaking changes for active sessions.",
              "consequence_preview": "Security Agent stays dominant -- more attack surface to analyze."
            },
            {
              "id": "strict",
              "label": "Strict Rotation",
              "description": "Enforce rotation immediately. Old tokens are invalid after one use.",
              "consequence_preview": "Developer takes the lead -- simpler implementation, fewer edge cases."
            }
          ]
        },
        {
          "id": "plan_gradual",
          "name": "PLAN",
          "branch": "gradual",
          "description": "Designing a gradual migration path for token rotation.",
          "steps": [
            {
              "type": "narration",
              "text": "You chose gradual migration. The Security Agent stays heavily engaged -- a dual-token window means more attack surface to analyze and more edge cases to cover."
            },
            {
              "type": "message",
              "sender": "security",
              "text": "Gradual migration introduces a token reuse window -- both old and new refresh tokens are valid simultaneously during the grace period. This is a classic replay attack vector. We need to bound the grace window tightly and track token lineage to detect reuse after rotation.",
              "meta": {
                "tier": "smartest",
                "model": "qwen3.5:9b + Claude",
                "tokens_in": 3890,
                "tokens_out": 498,
                "tokens_claude": 1920,
                "time_ms": 23600,
                "context_used": ["persona", "memory", "grounding"],
                "smartest_note": "Escalated to Smartest -- token reuse window analysis benefits from Claude's deep understanding of OAuth2 security trade-offs."
              }
            },
            {
              "type": "message",
              "sender": "architect",
              "text": "RFC 6749 Section 10.4 recommends sender-constrained tokens for this exact scenario. We should bind refresh tokens to the client TLS certificate fingerprint. That way, even if a token is stolen, it can't be replayed from a different client.",
              "meta": {
                "tier": "smarter",
                "model": "qwen3.5:9b",
                "tokens_in": 3650,
                "tokens_out": 445,
                "time_ms": 14100,
                "context_used": ["persona", "memory", "grounding"]
              }
            },
            {
              "type": "scoring",
              "title": "Security complexity keeps Security Agent dominant",
              "explanation": "Gradual migration creates ongoing security concerns. 'token', 'auth', 'vulnerability' keywords remain saturated in the conversation.",
              "scores": [
                { "agent": "security", "score": 0.62, "status": "ACTIVE", "decision": "SPEAK", "reason": "Token reuse, replay attacks, lineage tracking -- all Security triggers. Novelty still high." },
                { "agent": "architect", "score": 0.51, "status": "ACTIVE", "decision": "SPEAK", "reason": "RFC compliance and system-level token propagation. 'design', 'system' triggers active." },
                { "agent": "developer", "score": 0.34, "status": "ACTIVE", "decision": "SPEAK", "reason": "Rising -- 'implement' keywords emerging. But Security still leads the conversation." },
                { "agent": "qa", "score": 0.23, "status": "OBSERVER", "decision": "SILENT", "reason": "Still waiting. No validation keywords yet." }
              ],
              "insight": "Compare this to the Strict branch: there, the Developer scores 0.56 and Security drops to 0.38. Gradual migration keeps Security dominant because there's more attack surface to reason about.",
              "cost_note": "QA still gated. Developer can speak but isn't the priority. Without Cohort, all 4 would respond -- QA would say something generic about 'testing the migration' that adds nothing."
            },
            {
              "type": "gate_event",
              "agent": "qa",
              "decision": "APPROVED_SILENT",
              "reason": "Promoted from OBSERVER but score too low to speak. Threshold 0.70 for APPROVED_SILENT."
            },
            {
              "type": "message",
              "sender": "security",
              "text": "Token lineage is critical. Each refresh token gets a family_id. When rotation happens, the new token inherits the family_id. If we ever see a reuse of an old token in the same family, we invalidate the entire family -- that's the token replay detection pattern from RFC 6819.",
              "meta": {
                "tier": "smartest",
                "model": "qwen3.5:9b + Claude",
                "tokens_in": 4320,
                "tokens_out": 534,
                "tokens_claude": 2050,
                "time_ms": 25800,
                "context_used": ["persona", "memory", "grounding"],
                "smartest_note": "Escalated to Smartest -- family-based token invalidation is a nuanced security pattern that benefits from Claude's RFC-level knowledge."
              }
            },
            {
              "type": "message",
              "sender": "developer",
              "text": "Family-based tracking means a Redis sorted set per family_id, with TTL matching the refresh token lifetime. I can implement the lineage check as middleware -- intercept every /token refresh call, verify the family chain, and reject stale tokens.",
              "meta": {
                "tier": "smarter",
                "model": "qwen3.5:9b",
                "tokens_in": 4010,
                "tokens_out": 378,
                "time_ms": 12100,
                "context_used": ["persona", "memory", "grounding"]
              }
            },
            {
              "type": "scoring",
              "title": "Developer rising as implementation details solidify",
              "explanation": "'implement', 'redis', 'build' keywords shift toward EXECUTE. Developer is catching up to Security.",
              "scores": [
                { "agent": "developer", "score": 0.50, "status": "ACTIVE", "decision": "SPEAK", "reason": "EXECUTE keywords + 'redis' trigger. Implementation is taking shape." },
                { "agent": "security", "score": 0.47, "status": "ACTIVE", "decision": "SPEAK", "reason": "Novelty penalty after 2 detailed messages. Still relevant but dropping." },
                { "agent": "architect", "score": 0.39, "status": "ACTIVE", "decision": "SPEAK", "reason": "Design decisions settling. Monitoring role." },
                { "agent": "qa", "score": 0.30, "status": "APPROVED_SILENT", "decision": "SILENT", "reason": "Approaching but not yet. Threshold 0.70 still too high." }
              ],
              "insight": "Security Agent novelty is dropping after 2 substantive messages. Cohort's loop prevention ensures they don't keep repeating security concerns -- the Developer needs space to implement.",
              "cost_note": "QA has been gated for the entire planning phase. Each round saves ~3.2K tokens of premature testing discussion."
            }
          ]
        },
        {
          "id": "plan_strict",
          "name": "PLAN",
          "branch": "strict",
          "description": "Designing immediate strict token rotation.",
          "steps": [
            {
              "type": "narration",
              "text": "You chose strict rotation. The Developer takes the lead -- single-use tokens are simpler to implement and Security has fewer edge cases to worry about."
            },
            {
              "type": "message",
              "sender": "developer",
              "text": "Strict rotation simplifies everything. Each refresh token is single-use: exchange it for a new access+refresh pair, and the old refresh token is immediately invalidated. I'll use Redis with atomic GETDEL to ensure no race conditions on the token swap.",
              "meta": {
                "tier": "smarter",
                "model": "qwen3.5:9b",
                "tokens_in": 3340,
                "tokens_out": 425,
                "time_ms": 13800,
                "context_used": ["persona", "memory", "grounding"]
              }
            },
            {
              "type": "scoring",
              "title": "Strict rotation puts Developer in the lead",
              "explanation": "With no dual-token window, the conversation jumps to implementation. 'implement', 'redis', 'build', 'code' keywords dominate.",
              "scores": [
                { "agent": "developer", "score": 0.56, "status": "ACTIVE", "decision": "SPEAK", "reason": "'implement' + 'redis' + 'code' -- Developer's domain. Fast-tracked to EXECUTE." },
                { "agent": "architect", "score": 0.44, "status": "ACTIVE", "decision": "SPEAK", "reason": "System integration concerns. Redis architecture implications." },
                { "agent": "security", "score": 0.38, "status": "ACTIVE", "decision": "SPEAK", "reason": "Fewer edge cases to analyze. Single-use tokens reduce attack surface. Score dropping." },
                { "agent": "qa", "score": 0.21, "status": "OBSERVER", "decision": "SILENT", "reason": "Still gated. Nothing to test yet." }
              ],
              "insight": "In the Gradual branch, Security scores 0.62 here. In Strict, they're at 0.38. Your choice removed the dual-token complexity that Security was analyzing.",
              "cost_note": "2 agents effectively sidelined. Without Cohort, Security would write a lengthy analysis of threats that don't exist in a strict-rotation model."
            },
            {
              "type": "gate_event",
              "agent": "architect",
              "decision": "APPROVED_SILENT",
              "reason": "Integration concerns addressed by Developer's Redis atomic approach. Design is straightforward."
            },
            {
              "type": "message",
              "sender": "developer",
              "text": "Token storage schema: Redis hash per user_id with fields {refresh_token, created_at, client_id, scope}. TTL set to refresh_token_lifetime (7 days). On rotation: MULTI/EXEC block deletes old key and sets new key atomically. Zero window for replay.",
              "meta": {
                "tier": "smartest",
                "model": "qwen3.5:9b + Claude",
                "tokens_in": 3980,
                "tokens_out": 489,
                "tokens_claude": 1780,
                "time_ms": 22100,
                "context_used": ["persona", "memory", "grounding"],
                "smartest_note": "Escalated to Smartest -- Redis transaction design for token atomicity benefits from Claude's understanding of distributed consistency patterns."
              }
            },
            {
              "type": "message",
              "sender": "security",
              "text": "The atomic swap looks solid. One addition: log every rotation event with client_id, IP, and user_agent to an append-only audit table. If we ever need to investigate a compromise, the rotation log is the forensic trail.",
              "meta": {
                "tier": "smarter",
                "model": "qwen3.5:9b",
                "tokens_in": 3720,
                "tokens_out": 367,
                "time_ms": 11800,
                "context_used": ["persona", "grounding"]
              }
            },
            {
              "type": "scoring",
              "title": "Security contributes targeted advice, then steps back",
              "explanation": "Security's audit logging suggestion is high-value but narrow. Developer owns the implementation path.",
              "scores": [
                { "agent": "developer", "score": 0.55, "status": "ACTIVE", "decision": "SPEAK", "reason": "Owns implementation. Novelty maintained by new technical details per message." },
                { "agent": "security", "score": 0.35, "status": "APPROVED_SILENT", "decision": "SILENT", "reason": "Contributed audit logging recommendation. Fewer threats to analyze in strict mode. Stepping back." },
                { "agent": "architect", "score": 0.30, "status": "APPROVED_SILENT", "decision": "SILENT", "reason": "Design is settled. Redis architecture approved. Monitoring only." },
                { "agent": "qa", "score": 0.28, "status": "OBSERVER", "decision": "SILENT", "reason": "Approaching activation. Testing keywords emerging in context." }
              ],
              "insight": "In the Gradual branch, Security is still at 0.47 here with 2 Smartest-tier messages. In Strict, they're already APPROVED_SILENT at 0.35. Strict rotation genuinely reduces security complexity.",
              "cost_note": "Security and Architect both gated. Without Cohort, they'd keep discussing theoretical threats that strict rotation already eliminates."
            }
          ]
        },
        {
          "id": "execute_shared",
          "name": "EXECUTE",
          "description": "Implementation underway. The developer builds the rotation system.",
          "steps": [
            {
              "type": "narration",
              "text": "The Developer implements the token rotation system. QA begins validation. A discovery during testing will force the team to adapt."
            },
            {
              "type": "message",
              "sender": "developer",
              "text": "Token rotation endpoint is live. The /oauth/token refresh flow now returns a new refresh_token alongside the access_token. Old refresh tokens are invalidated immediately on use. Error handling covers expired tokens, malformed tokens, and revoked families.",
              "meta": {
                "tier": "smarter",
                "model": "qwen3.5:9b",
                "tokens_in": 4150,
                "tokens_out": 402,
                "time_ms": 12800,
                "context_used": ["persona", "memory", "grounding"]
              }
            },
            {
              "type": "scoring",
              "title": "QA finally activates",
              "explanation": "Implementation is complete. 'test', 'validation', 'edge' keywords are now the dominant context. QA's time has come.",
              "scores": [
                { "agent": "qa", "score": 0.52, "status": "ACTIVE", "decision": "SPEAK", "reason": "Promoted from OBSERVER! 'validation', 'test', 'edge' keywords align perfectly. QA's phase." },
                { "agent": "developer", "score": 0.41, "status": "ACTIVE", "decision": "SPEAK", "reason": "Novelty penalty after 2+ implementation messages. Score dropping naturally." },
                { "agent": "security", "score": 0.33, "status": "APPROVED_SILENT", "decision": "SILENT", "reason": "Monitoring. Will re-engage if security finding surfaces." },
                { "agent": "architect", "score": 0.28, "status": "APPROVED_SILENT", "decision": "SILENT", "reason": "Design phase complete. Low relevance to testing." }
              ],
              "insight": "QA was gated for the entire DISCOVER and PLAN phases. Now they're ACTIVE with the highest score. Cohort's gating saved 3+ rounds of premature QA input and activated them at exactly the right moment.",
              "cost_note": "QA was gated for 4+ rounds. Each saved round is ~3K tokens. Now they speak because testing is genuinely the priority."
            },
            {
              "type": "message",
              "sender": "qa",
              "text": "Starting security-focused test suite: token replay attempts, concurrent rotation from multiple clients, expired token edge cases, and cross-service propagation tests. Also running regression on all existing auth flows to catch backward-compat breaks.",
              "meta": {
                "tier": "smarter",
                "model": "qwen3.5:9b",
                "tokens_in": 3890,
                "tokens_out": 435,
                "time_ms": 14000,
                "context_used": ["persona", "memory", "grounding"]
              }
            },
            {
              "type": "narration",
              "text": "During testing, QA discovers a critical issue. Your second choice determines the nature of the problem -- and which dormant agent snaps back to life."
            }
          ]
        },
        {
          "id": "choice_2",
          "type": "choice",
          "prompt": "During testing, QA discovers:",
          "options": [
            {
              "id": "race",
              "label": "Race Condition",
              "description": "Two concurrent requests with the same refresh token. One gets a new token, the other gets a 401.",
              "consequence_preview": "Watch the Architect re-engage on distributed systems."
            },
            {
              "id": "leak",
              "label": "Token Leak Risk",
              "description": "Old refresh tokens in server logs. A compromised log file means compromised sessions.",
              "consequence_preview": "Security Agent escalates -- this is their domain."
            }
          ]
        },
        {
          "id": "discovery_race",
          "name": "TOPIC SHIFT",
          "branch": "race",
          "description": "A race condition shifts the conversation to distributed systems territory.",
          "steps": [
            {
              "type": "narration",
              "text": "Race condition discovered. This is a distributed systems problem -- watch the Architect re-engage from the sidelines."
            },
            {
              "type": "message",
              "sender": "qa",
              "text": "CRITICAL: Race condition in concurrent token rotation. When two requests hit /oauth/token with the same refresh token within a 50ms window, both pass the validity check before either invalidates the old token. One client gets a new token pair, the other gets a 401 and loses its session.",
              "meta": {
                "tier": "smartest",
                "model": "qwen3.5:9b + Claude",
                "tokens_in": 4480,
                "tokens_out": 567,
                "tokens_claude": 2180,
                "time_ms": 26800,
                "context_used": ["persona", "memory", "grounding"],
                "smartest_note": "Escalated to Smartest -- race condition analysis in distributed token systems benefits from Claude's understanding of concurrency failure modes."
              }
            },
            {
              "type": "scoring",
              "title": "Topic shift re-engages the Architect!",
              "explanation": "'distributed', 'system', 'architecture', 'integration' -- the race condition pulled the conversation into distributed systems territory. The Architect was APPROVED_SILENT but topic shift triggers re-evaluation.",
              "scores": [
                { "agent": "architect", "score": 0.59, "status": "ACTIVE", "decision": "SPEAK", "reason": "RE-ENGAGED from APPROVED_SILENT! Topic shift detected: 'system' + 'integration' + distributed concurrency. Architecture domain." },
                { "agent": "qa", "score": 0.53, "status": "ACTIVE", "decision": "SPEAK", "reason": "Owns the finding. Concurrency testing expertise." },
                { "agent": "developer", "score": 0.45, "status": "ACTIVE", "decision": "SPEAK", "reason": "Needs to fix the implementation. Redis locking is their domain." },
                { "agent": "security", "score": 0.31, "status": "APPROVED_SILENT", "decision": "SILENT", "reason": "Race condition is a systems problem, not a security pattern. Stays silent." }
              ],
              "insight": "The Architect went from APPROVED_SILENT (score 0.28) to ACTIVE (score 0.59) in one message. Topic shift detection recognized that concurrent token handling is an architecture problem, not just a code bug.",
              "cost_note": "The Architect was gated for 3+ rounds, saving tokens. Now they're back because distributed concurrency genuinely needs architectural guidance."
            },
            {
              "type": "gate_event",
              "agent": "architect",
              "decision": "RE-ENGAGED",
              "reason": "Topic shift to distributed systems domain. Status: APPROVED_SILENT -> ACTIVE."
            },
            {
              "type": "message",
              "sender": "architect",
              "text": "This is a classic distributed lock problem. The fix is a Redis-based lock on the token family: SETNX on a lock key before processing the rotation, with a 5-second TTL as a safety net. Second request sees the lock, waits or retries. No more race window.",
              "meta": {
                "tier": "smartest",
                "model": "qwen3.5:9b + Claude",
                "tokens_in": 4920,
                "tokens_out": 512,
                "tokens_claude": 2080,
                "time_ms": 25400,
                "context_used": ["persona", "memory", "grounding"],
                "smartest_note": "Escalated to Smartest -- distributed locking strategy selection benefits from Claude's knowledge of Redis concurrency patterns and their trade-offs."
              }
            },
            {
              "type": "message",
              "sender": "developer",
              "text": "SETNX with TTL works. I'll wrap the entire rotation in a Lua script on Redis -- SETNX the lock, GETDEL the old token, SET the new token, DEL the lock. Atomic at the Redis level. Second client gets a 409 Conflict with a Retry-After header.",
              "meta": {
                "tier": "smarter",
                "model": "qwen3.5:9b",
                "tokens_in": 4560,
                "tokens_out": 423,
                "time_ms": 13500,
                "context_used": ["persona", "memory", "grounding"]
              }
            },
            {
              "type": "scoring",
              "title": "Architect-Developer collaboration resolves the race",
              "explanation": "Architecture guided the pattern, Developer implemented it. Classic handoff that Cohort enabled by re-engaging the right agent.",
              "scores": [
                { "agent": "developer", "score": 0.52, "status": "ACTIVE", "decision": "SPEAK", "reason": "Owns the Lua script implementation. High relevance." },
                { "agent": "architect", "score": 0.43, "status": "APPROVED_SILENT", "decision": "SILENT", "reason": "Pattern delivered. Stepping back after one high-value contribution." },
                { "agent": "qa", "score": 0.40, "status": "ACTIVE", "decision": "SPEAK", "reason": "Needs to re-run concurrency tests. Still relevant." },
                { "agent": "security", "score": 0.29, "status": "APPROVED_SILENT", "decision": "SILENT", "reason": "Race condition resolved without security implications. Correctly stayed silent." }
              ],
              "insight": "The Architect re-engaged for exactly one high-value message, then stepped back. Cohort's gating let them contribute the SETNX pattern without dominating the fix."
            }
          ]
        },
        {
          "id": "discovery_leak",
          "name": "VALIDATE",
          "branch": "leak",
          "description": "A token leak risk in server logs escalates to a security emergency.",
          "steps": [
            {
              "type": "narration",
              "text": "Token leak in logs. This is pure security territory -- watch the Security Agent surge back."
            },
            {
              "type": "message",
              "sender": "qa",
              "text": "CRITICAL: Refresh tokens appear in plaintext in server access logs. The /oauth/token endpoint logs the full request body at INFO level, which includes the refresh_token parameter. Any log aggregation system -- Elasticsearch, Splunk, CloudWatch -- now has a copy of every active refresh token.",
              "meta": {
                "tier": "smartest",
                "model": "qwen3.5:9b + Claude",
                "tokens_in": 4380,
                "tokens_out": 545,
                "tokens_claude": 2250,
                "time_ms": 27200,
                "context_used": ["persona", "memory", "grounding"],
                "smartest_note": "Escalated to Smartest -- log exposure analysis benefits from Claude's understanding of credential leak blast radius across log aggregation pipelines."
              }
            },
            {
              "type": "scoring",
              "title": "Security Agent surges back",
              "explanation": "'vulnerability', 'security', 'token', 'encryption' -- the log leak is a pure security problem. No topic shift to a different domain, but Security's score surges because this is exactly their expertise.",
              "scores": [
                { "agent": "security", "score": 0.66, "status": "ACTIVE", "decision": "SPEAK", "reason": "SURGE from APPROVED_SILENT! 'vulnerability' + 'token' + credential exposure. This is Security's core domain." },
                { "agent": "qa", "score": 0.51, "status": "ACTIVE", "decision": "SPEAK", "reason": "Owns the finding. Log analysis expertise." },
                { "agent": "developer", "score": 0.42, "status": "ACTIVE", "decision": "SPEAK", "reason": "Will need to implement log scrubbing + token hashing. Implementation relevant." },
                { "agent": "architect", "score": 0.27, "status": "APPROVED_SILENT", "decision": "SILENT", "reason": "Not a systems architecture problem. Stays silent." }
              ],
              "insight": "Security went from APPROVED_SILENT (0.33) to ACTIVE (0.66) -- their highest score in the entire scenario. A credential leak is exactly what Security Agent exists to catch and remediate.",
              "cost_note": "Architect correctly stays gated. Without Cohort, they'd write about 'log architecture patterns' that don't address the immediate security concern."
            },
            {
              "type": "message",
              "sender": "security",
              "text": "Immediate remediation: first, rotate every refresh token in the system -- all existing tokens are potentially compromised. Second, add request body scrubbing middleware that redacts token values before logging. Third, hash all stored tokens with SHA-256 so even database dumps don't expose raw tokens. Store the hash, compare on refresh.",
              "meta": {
                "tier": "smartest",
                "model": "qwen3.5:9b + Claude",
                "tokens_in": 5020,
                "tokens_out": 578,
                "tokens_claude": 2340,
                "time_ms": 28100,
                "context_used": ["persona", "memory", "grounding"],
                "smartest_note": "Escalated to Smartest -- multi-layer remediation strategy for credential leaks benefits from Claude's incident response knowledge."
              }
            },
            {
              "type": "message",
              "sender": "developer",
              "text": "On it. Log scrubbing middleware: regex filter on request body for token-shaped strings, replace with [REDACTED]. Token hashing: store SHA-256(token) in Redis, compare hash on refresh. Forces a full token rotation for all active sessions -- I'll add a migration script.",
              "meta": {
                "tier": "smarter",
                "model": "qwen3.5:9b",
                "tokens_in": 4680,
                "tokens_out": 412,
                "time_ms": 13200,
                "context_used": ["persona", "memory", "grounding"]
              }
            },
            {
              "type": "scoring",
              "title": "Security-Developer remediation partnership",
              "explanation": "Security defined the remediation strategy, Developer is implementing it. Both agents are high-value right now.",
              "scores": [
                { "agent": "developer", "score": 0.53, "status": "ACTIVE", "decision": "SPEAK", "reason": "Implementing the remediation. High relevance with 'code', 'build', 'implement' triggers." },
                { "agent": "security", "score": 0.48, "status": "ACTIVE", "decision": "SPEAK", "reason": "Remediation strategy delivered. Novelty dropping but still monitoring." },
                { "agent": "qa", "score": 0.42, "status": "ACTIVE", "decision": "SPEAK", "reason": "Needs to verify the scrubbing works. Regression testing critical." },
                { "agent": "architect", "score": 0.25, "status": "APPROVED_SILENT", "decision": "SILENT", "reason": "Not an architecture problem. Correctly silent throughout." }
              ],
              "insight": "Compare to the Race branch: there, the Architect re-engages and Security stays silent. Here, Security surges and Architect stays silent. Same team, same codebase -- different discoveries activate different experts."
            }
          ]
        },
        {
          "id": "outcome",
          "name": "OUTCOME",
          "description": "What your team built.",
          "steps": [
            {
              "type": "outcome_summary"
            }
          ]
        }
      ],

      "outcomes": {
        "gradual+race": {
          "title": "Gradual & Resilient",
          "summary": "Your team implemented token rotation with a bounded grace period and Redis-based distributed locking. The race condition forced architectural intervention that made the system more robust.",
          "stats": {
            "agents_who_spoke": 4,
            "messages_total": 12,
            "topic_shifts_detected": 1,
            "gates_enforced": 4
          },
          "cost_comparison": {
            "cohort_smarter_tokens": 41200,
            "cohort_smartest_tokens": 10280,
            "cohort_total_cost": 0.031,
            "without_cohort_tokens": 76800,
            "without_cohort_cost": 0.230,
            "savings_pct": 87
          },
          "agent_journeys": {
            "security": ["ACTIVE", "ACTIVE", "APPROVED_SILENT", "APPROVED_SILENT"],
            "developer": ["ACTIVE", "ACTIVE", "ACTIVE", "ACTIVE"],
            "qa": ["OBSERVER", "APPROVED_SILENT", "ACTIVE", "ACTIVE"],
            "architect": ["ACTIVE", "ACTIVE", "APPROVED_SILENT", "ACTIVE(re-engaged)"]
          },
          "key_moment": "The Architect re-engaged from APPROVED_SILENT when the race condition surfaced. Their SETNX + Lua script pattern resolved the concurrency issue in one message -- exactly the kind of targeted, high-value contribution that gating enables."
        },
        "gradual+leak": {
          "title": "Gradual & Hardened",
          "summary": "Your team implemented token rotation with a grace period, then discovered and remediated a critical token leak in server logs. Security Agent drove the incident response.",
          "stats": {
            "agents_who_spoke": 4,
            "messages_total": 12,
            "topic_shifts_detected": 0,
            "gates_enforced": 4
          },
          "cost_comparison": {
            "cohort_smarter_tokens": 40600,
            "cohort_smartest_tokens": 12540,
            "cohort_total_cost": 0.038,
            "without_cohort_tokens": 79200,
            "without_cohort_cost": 0.238,
            "savings_pct": 84
          },
          "agent_journeys": {
            "security": ["ACTIVE", "ACTIVE", "APPROVED_SILENT", "ACTIVE(surged)"],
            "developer": ["ACTIVE", "ACTIVE", "ACTIVE", "ACTIVE"],
            "qa": ["OBSERVER", "APPROVED_SILENT", "ACTIVE", "ACTIVE"],
            "architect": ["ACTIVE", "ACTIVE", "APPROVED_SILENT", "APPROVED_SILENT"]
          },
          "key_moment": "Security Agent surged from APPROVED_SILENT to their highest score (0.66) when the token leak was discovered. Their three-layer remediation plan -- rotate, scrub, hash -- was the most Smartest-tier-heavy response in the scenario. Credential leaks demand Security's full attention."
        },
        "strict+race": {
          "title": "Strict & Resilient",
          "summary": "Your team implemented strict single-use token rotation with Redis atomic operations, then resolved a concurrency race with distributed locking. The Architect's re-engagement was decisive.",
          "stats": {
            "agents_who_spoke": 4,
            "messages_total": 11,
            "topic_shifts_detected": 1,
            "gates_enforced": 3
          },
          "cost_comparison": {
            "cohort_smarter_tokens": 37400,
            "cohort_smartest_tokens": 8040,
            "cohort_total_cost": 0.024,
            "without_cohort_tokens": 72600,
            "without_cohort_cost": 0.218,
            "savings_pct": 89
          },
          "agent_journeys": {
            "security": ["ACTIVE", "APPROVED_SILENT", "APPROVED_SILENT", "APPROVED_SILENT"],
            "developer": ["ACTIVE", "ACTIVE(leading)", "ACTIVE", "ACTIVE"],
            "qa": ["OBSERVER", "OBSERVER", "ACTIVE", "ACTIVE"],
            "architect": ["ACTIVE", "APPROVED_SILENT", "APPROVED_SILENT", "ACTIVE(re-engaged)"]
          },
          "key_moment": "Strict rotation silenced Security early (0.35 by PLAN phase). The Architect then re-engaged for the race condition -- a system-level problem that neither Security nor Developer could solve alone. Cohort routed the right expert at the right time."
        },
        "strict+leak": {
          "title": "Strict & Hardened",
          "summary": "Your team implemented strict single-use rotation, then discovered plaintext tokens in server logs. Security Agent escalated and drove a full incident response.",
          "stats": {
            "agents_who_spoke": 4,
            "messages_total": 11,
            "topic_shifts_detected": 0,
            "gates_enforced": 3
          },
          "cost_comparison": {
            "cohort_smarter_tokens": 36800,
            "cohort_smartest_tokens": 9580,
            "cohort_total_cost": 0.029,
            "without_cohort_tokens": 71400,
            "without_cohort_cost": 0.214,
            "savings_pct": 86
          },
          "agent_journeys": {
            "security": ["ACTIVE", "APPROVED_SILENT", "APPROVED_SILENT", "ACTIVE(surged)"],
            "developer": ["ACTIVE", "ACTIVE(leading)", "ACTIVE", "ACTIVE"],
            "qa": ["OBSERVER", "OBSERVER", "ACTIVE", "ACTIVE"],
            "architect": ["ACTIVE", "APPROVED_SILENT", "APPROVED_SILENT", "APPROVED_SILENT"]
          },
          "key_moment": "Security went from the lowest-scoring active agent (0.35 in PLAN phase) to the highest-scoring agent (0.66 in VALIDATE phase). Strict rotation made their PLAN input less critical, but the token leak made their VALIDATE input essential. Cohort correctly gated then re-activated."
        }
      }
    }
  ];

  document.addEventListener("DOMContentLoaded", function() {
    var container = document.getElementById("simulator");
    if (!container) return;

    function showPicker() {
      var scenarios = window.SIMULATOR_SCENARIOS;
      var html = '<div class="sim-picker"><div class="sim-picker-grid">';

      for (var i = 0; i < scenarios.length; i++) {
        var s = scenarios[i];
        var agentKeys = Object.keys(s.agents);
        var agentCount = agentKeys.length;
        var choiceCount = 0;
        for (var j = 0; j < s.phases.length; j++) {
          if (s.phases[j].type === "choice") {
            choiceCount++;
          }
        }

        html += '<div class="sim-picker-card" data-scenario-index="' + i + '">';
        html += '<h3 class="sim-picker-card-title">' + s.title + '</h3>';
        html += '<p class="sim-picker-card-desc">' + s.description + '</p>';
        html += '<div class="sim-picker-card-meta">';
        html += '<span>' + agentCount + ' agents</span>';
        html += '<span>' + choiceCount + ' choices</span>';
        html += '</div>';
        html += '</div>';
      }

      html += '</div></div>';
      container.innerHTML = html;

      var cards = container.querySelectorAll(".sim-picker-card");
      for (var k = 0; k < cards.length; k++) {
        cards[k].addEventListener("click", function() {
          var idx = parseInt(this.getAttribute("data-scenario-index"), 10);
          var scenario = window.SIMULATOR_SCENARIOS[idx];
          new window.CohortSimulator(container, scenario, showPicker);
        });
      }
    }

    showPicker();
  });
})();
