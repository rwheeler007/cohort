# Roundtable Brief: Making Cohort's Orchestrator Smarter

**Date:** 2026-03-22
**Status:** Proposal — ready for discussion
**Scope:** `capability_router.py`, `meeting.py`, `orchestrator.py`, `local/router.py`

---

## Problem Statement

Cohort's routing and orchestration layer is **structurally sound** but **intellectually shallow**. It relies on keyword matching (substring, set intersection) rather than semantic understanding. This causes:

1. **Missed routing** — "expose data over HTTP" doesn't match an agent with trigger "api"
2. **False matches** — keyword collisions across unrelated domains
3. **No learning** — historical success defaults to a neutral 0.5; the system never gets smarter from experience
4. **Rigid phase detection** — hardcoded keyword lists for DISCOVER/PLAN/EXECUTE/VALIDATE
5. **Static gating** — stakeholder thresholds (0.3/0.7/0.8/1.0) are constants, not adaptive

---

## Current Architecture (What We Have)

### Routing (`capability_router.py`)
- `score_agent_for_topic()` — 3-source weighted scoring:
  - Triggers: 50% weight, exact keyword match
  - Capabilities: 30% weight, substring-in-phrase
  - Domain expertise: 20% weight, substring-in-phrase
- Type preference bonus (+0.1), skill level bonus (+0.05)
- Threshold: 0.15 minimum for `route_task()`

### Discussion Scoring (`meeting.py`)
- 5-dimensional composite relevance:
  - Domain expertise: 30%
  - Complementary value: 25%
  - Historical success: 20% (almost always neutral 0.5)
  - Phase alignment: 15%
  - Data ownership: 10%
- Contribution gating: novelty 35%, expertise 30%, ownership 20%, question 15%

### Local Router (`local/router.py`)
- Ollama-backed inference for compiled discussions
- Hardware-aware model selection
- Confidence classification (high/guarded)

---

## Proposed Improvements

### 1. LLM-Assisted Intent Classification for Routing

**What:** Before keyword extraction, run the task description through a fast local model (or a one-shot Claude call) to extract structured intent: domain, action type, complexity, and relevant agent capabilities.

**Why:** "Help me set up a CI pipeline" and "configure GitHub Actions for automated testing" should both route to the same agent, but keyword matching diverges on these.

**Where:** New function in `capability_router.py`, called before `score_agent_for_topic()`.

**Sketch:**
```python
def classify_intent(task: str, agent_summaries: list[str]) -> dict:
    """Use local LLM to extract structured intent from task description.

    Returns: {
        "domain": "devops",
        "action": "configure",
        "keywords_expanded": ["ci", "cd", "pipeline", "github-actions", "testing"],
        "best_agent_ids": ["python_developer", "qa_agent"],
        "confidence": 0.85
    }
    """
```

**Fallback:** If local model unavailable, fall through to existing keyword matching (zero regression).

---

### 2. Embedding-Based Agent Matching

**What:** Pre-compute embeddings for each agent's combined triggers + capabilities + expertise text. At routing time, embed the task description and use cosine similarity instead of keyword overlap.

**Why:** Embeddings capture semantic similarity. "database migration" and "schema evolution" would score high even with zero keyword overlap.

**Where:** New module `cohort/embeddings.py`, integrated into `score_agent_for_topic()` as a weighted dimension.

**Options:**
- **Local:** Use Ollama's embedding endpoint (e.g., `nomic-embed-text`)
- **Cached:** Pre-compute agent embeddings at startup, only embed queries at runtime
- **Hybrid:** Use embedding score as a 4th dimension (e.g., 30% weight) alongside existing keyword scores, rebalancing the others

**Sketch:**
```python
# New scoring with embedding dimension
WEIGHTS = {
    "triggers": 0.30,      # was 0.50
    "capabilities": 0.15,  # was 0.30
    "expertise": 0.10,     # was 0.20
    "semantic": 0.45,      # NEW — embedding cosine similarity
}
```

---

### 3. Routing Feedback Loop

**What:** After a task completes (or a discussion concludes), record which agent was routed to and whether the outcome was successful. Use this history to adjust routing scores over time.

**Why:** The `historical_success` dimension in `meeting.py` currently defaults to 0.5. With actual data, agents that consistently succeed at certain task types get boosted, and those that struggle get deprioritized.

**Where:** New file `cohort/routing_history.py` + updates to `meeting.py` and `capability_router.py`.

**Data model:**
```python
@dataclass
class RoutingOutcome:
    task_keywords: list[str]
    agent_id: str
    score_at_routing: float
    outcome: str  # "success", "partial", "failed", "reassigned"
    timestamp: str

    # Optional: who it was reassigned to, if applicable
    reassigned_to: str | None = None
```

**Scoring adjustment:**
```python
def adjusted_score(base_score: float, agent_id: str, keywords: list[str]) -> float:
    history = get_outcomes_for_agent(agent_id, keywords, lookback=50)
    if not history:
        return base_score  # no data, no adjustment
    success_rate = sum(1 for h in history if h.outcome == "success") / len(history)
    # Dampen: max ±0.15 adjustment
    adjustment = (success_rate - 0.5) * 0.3
    return max(0.0, min(1.0, base_score + adjustment))
```

---

### 4. Semantic Phase Detection

**What:** Replace the hardcoded `PHASE_KEYWORDS` dict in `meeting.py` with an LLM classifier or at minimum a richer pattern-matching system that understands conversation flow, not just individual keywords.

**Why:** Current phase detection checks if "implement" appears in a message. But a message saying "don't implement this yet, let's plan first" gets classified as EXECUTE because of the keyword hit.

**Options (escalating complexity):**
1. **Negation-aware patterns** — check for "don't/not/shouldn't" before phase keywords (quick win)
2. **Sliding window context** — classify phase based on the last N messages, not just the current one
3. **LLM classification** — "Given this conversation excerpt, what phase are we in?"

---

### 5. Adaptive Stakeholder Thresholds

**What:** Instead of fixed thresholds (ACTIVE=0.3, APPROVED_SILENT=0.7, etc.), adjust them based on conversation dynamics:
- Fewer agents in the discussion → lower thresholds (encourage participation)
- Many agents already contributing → raise thresholds (reduce noise)
- Long-running discussion → progressively raise thresholds (converge)

**Where:** `meeting.py`, modify `STAKEHOLDER_THRESHOLDS` to be a function of session state rather than constants.

**Sketch:**
```python
def get_dynamic_thresholds(
    num_participants: int,
    turn_number: int,
    max_turns: int,
) -> dict[str, float]:
    progress = turn_number / max(max_turns, 1)

    # Base thresholds
    active = 0.3
    approved_silent = 0.7

    # Fewer participants → lower bar
    if num_participants < 3:
        active -= 0.1
        approved_silent -= 0.1

    # Late in discussion → raise bar (converge)
    if progress > 0.6:
        convergence_bump = (progress - 0.6) * 0.5
        active += convergence_bump
        approved_silent += convergence_bump

    return {
        "active_stakeholder": max(0.1, active),
        "approved_silent": min(0.9, approved_silent),
        "observer": min(0.95, approved_silent + 0.1),
        "dormant": 1.0,
    }
```

---

### 6. Query Expansion for Keyword Extraction

**What:** Before matching, expand the extracted keywords with synonyms/related terms. This is cheaper than full embeddings and works offline.

**Where:** `capability_router.py`, enhance `_extract_keywords()`.

**Approach:** Maintain a lightweight synonym map (can be generated once by an LLM, stored as JSON):
```python
SYNONYM_MAP = {
    "api": ["endpoint", "rest", "http", "route", "server"],
    "database": ["db", "sql", "schema", "migration", "query"],
    "frontend": ["ui", "react", "css", "html", "component"],
    "test": ["spec", "unittest", "pytest", "coverage", "assert"],
    "deploy": ["ci", "cd", "pipeline", "release", "ship"],
    # ...
}
```

This is the **lowest-effort, highest-impact** change — can be done in an afternoon.

---

## Priority & Sequencing

| # | Improvement | Effort | Impact | Dependencies |
|---|------------|--------|--------|-------------|
| 1 | Query expansion / synonym map | Low | High | None |
| 2 | Embedding-based matching | Medium | High | Ollama embeddings |
| 3 | Routing feedback loop | Medium | High | Outcome tracking |
| 4 | LLM intent classification | Medium | Medium | Local router |
| 5 | Adaptive thresholds | Low | Medium | None |
| 6 | Semantic phase detection | Medium | Medium | None |

**Recommended order:** 1 → 5 → 3 → 2 → 6 → 4

Start with synonym expansion (immediate win, no infrastructure), then adaptive thresholds (small code change), then the feedback loop (needs data pipeline but high payoff). Embeddings and LLM classification are the heavier lifts — save them for when the simpler improvements hit their ceiling.

---

## Open Questions

1. **Where do embeddings live?** In-memory cache? SQLite? Agent config JSON?
2. **How do we measure "success" for the feedback loop?** User satisfaction signal? Task completion? Reassignment rate?
3. **Should we A/B test routing strategies?** Route the same task through keyword vs. embedding and compare?
4. **Token budget for LLM-based routing** — how much latency is acceptable before it defeats the purpose of fast routing?
5. **Synonym map maintenance** — hand-curated vs. auto-generated from agent configs?

---

## Discussion Prompt

> Given the current keyword-matching architecture in `capability_router.py` and `meeting.py`, what's the smartest path to make Cohort's orchestrator genuinely intelligent at routing and discussion management — without overengineering it or adding infrastructure we don't need yet?

Relevant agents for this discussion: `coding_orchestrator`, `python_developer`, `system_coder`, `qa_agent`, `cohort_orchestrator`

---

## Roundtable Results (2026-03-22)

**Channel:** `ds-smarter-orchestration`
**Participants:** cohort_orchestrator, python_developer, code_archaeologist, qa_agent
**Model:** qwen3.5:9b | **Rounds:** 2 | **Latency:** 16.3s

### cohort_orchestrator

The current keyword-based routing is a brittle heuristic, not an intelligence. We are treating agents like static tools rather than dynamic collaborators. The biggest gap is the lack of **semantic intent modeling**. When a user asks "How do I optimize my Python script?", we currently check for keywords like "optimize" and "Python". We miss the nuance if the user says "My script is too slow, help me refactor." We need a semantic layer that understands the *goal* of the request, not just the vocabulary.

Furthermore, our discussion management relies on recency penalties, which artificially suppresses deep thinking. A truly smart orchestrator should recognize when a thread is stalling and proactively inject a counter-perspective or a synthesis step, rather than just waiting for the next random turn. **We must move from "who matches the keywords best" to "who is the right cognitive partner for this specific problem state."**

### python_developer

The current `capability_router.py` is essentially a giant if/elif chain disguised as a scoring system. The 50/30/20 split is arbitrary and ignores the reality of code complexity. The most critical gap is the inability to handle **contextual dependencies**. If Agent A suggests a specific library, Agent B shouldn't just be selected because they know libraries; they should be selected because they understand the *specific* library Agent A mentioned and can critique its implementation.

Currently, we compile all personas into a single Ollama call, which flattens the conversation. We lose the granular state of "what has been decided" vs "what is being debated." We need a routing mechanism that maintains a **persistent, evolving context window** for each agent, allowing them to reference previous technical decisions made in the session, rather than just reacting to the immediate prompt.

### code_archaeologist

The "compiled roundtable" approach is the biggest architectural concern. It forces a single context window to hold the entire conversation, leading to rapid context dilution. A smart orchestrator needs **dynamic context pruning** and **agent-specific memory**. When we route, we aren't just matching capabilities; we are managing cognitive load.

The current weighted scoring is static; it doesn't learn that Agent X is better at async Python than Agent Y, even if both have the "Python" capability tag. **We need a feedback loop where quality of generated code retroactively updates routing weights**, moving from static rules to a living, learning graph of agent competence.

### qa_agent

The current turn management is the weakest link. Using recency penalties and first-contribution bonuses is a crude way to simulate "fairness," not intelligence. A truly intelligent orchestrator should understand **task complexity** and dynamically adjust the number of turns or the depth of synthesis required.

The biggest gap is the lack of **proactive synthesis**. We wait for the conversation to end to compile a summary. Instead, the orchestrator should detect when a consensus is forming or when a contradiction exists and trigger an immediate "resolution turn" to resolve it. **This transforms the system from a passive recorder to an active mediator** that ensures high-quality output before the session concludes.

---

## Consolidated Themes from Roundtable

| Theme | Raised By | Maps To Proposal |
|-------|-----------|-----------------|
| Semantic intent modeling over keyword matching | orchestrator, python_dev | #1 LLM Intent Classification, #2 Embeddings |
| Contextual dependencies (mid-session routing) | python_developer | New: session-aware routing |
| Feedback loop / learning from outcomes | code_archaeologist | #3 Routing Feedback Loop |
| Dynamic context pruning / agent-specific memory | code_archaeologist | New: per-agent context windows |
| Proactive synthesis / resolution turns | qa_agent | New: mid-discussion synthesis |
| Adaptive turn management based on complexity | qa_agent | #5 Adaptive Thresholds |
| Stall detection + counter-perspective injection | orchestrator | New: orchestrator interventions |

### New Items Not in Original Proposal

1. **Session-Aware Routing** — routing decisions mid-discussion should factor in what's already been said, not just the original topic
2. **Proactive Synthesis Turns** — orchestrator detects contradictions or consensus and triggers resolution before session end
3. **Orchestrator Interventions** — detect stalls, inject counter-perspectives, re-engage dormant experts
4. **Per-Agent Context Windows** — instead of dumping all agents into one compiled call, maintain separate context with shared decision state
