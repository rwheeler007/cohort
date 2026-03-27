# Channels: The Narrative

This is the story of why Claude Code Channels changes everything -- and why Cohort was ready for it before Anthropic shipped it.

---

## The Big Picture

Channels isn't a feature. It's a paradigm shift in how AI tools work.

Before Channels, there were two worlds:
- **You use AI**: You type into a chat box. It responds. One direction.
- **AI uses your tools**: MCP lets Claude call functions you expose. One direction.

Channels makes it **bidirectional**. Claude can call your tools. Your tools can call Claude. Same protocol, same session, same context. And it runs at a **fixed monthly cost** ($100 or $200/mo) instead of open API metering ($800-2,000+/mo).

This is what Cohort was built for. We just didn't know Anthropic would be the one to ship the transport layer.

---

## Why Cohort Was Ready

We integrated Channels three hours after Anthropic shipped the protocol. That's the headline. But the real story is the months before that morning.

**We've been MCP-native from day one.** Cohort's entire tool surface -- 185 CLI commands, 23 specialist agents, roundtables, code queue, health checks -- is already exposed as MCP tools. Channels are MCP servers. The integration was a thin bridge, not an architecture change.

**We already had the request/response infrastructure.** Cohort's code queue is a full lifecycle state machine: submit, preprocess, claim, execute, self-review, agent review, approve. The Channel plugin just polls it. No new infrastructure.

**We already had context hydration.** When a Claude Code session starts for a channel, Cohort summarizes the channel's recent discussion using your local LLM and injects it as a structured briefing. Claude joins mid-conversation without anyone catching it up. This existed before Channels.

**We already had session isolation.** Each channel gets its own Claude Code session. A security audit in #security-review doesn't share context with a website build in #web-project. Up to 5 simultaneous sessions, with priority eviction.

The integration timeline tells the story:

```
03:00 AM MT  -- Anthropic ships Claude Code Channels (research preview)
06:03 AM MT  -- Cohort integration committed. Agent pipeline operational.
06:45 AM MT  -- Working checkpoint. CLI module, agent enrichment, context hydration.
07:11 AM MT  -- First multi-round agent roundtable via Channels.
05:38 PM MT  -- Per-channel session management with auto-launch and idle reaping.
10:06 PM MT  -- VS Code extension panel for session management shipped.
```

Three hours to working integration. One day to production-grade session management. Because the hard work was already done.

---

## What This Unlocks

### Bidirectional Tool Use

**Claude calls your tools:** Through Cohort's MCP integration, Claude gains access to your entire agent ecosystem. Need a security review? Claude calls the security agent. Need content strategy? Claude calls the content strategist. Each agent brings persistent memory, domain expertise, and contribution scoring -- capabilities Claude doesn't have alone.

**Your agents call Claude:** Cohort's local agents run at 104 tok/s on your GPU for free. But some tasks need frontier reasoning -- complex refactors, multi-file architectural changes, nuanced code review. Your agents escalate those tasks to Claude Code through the Channel, and Claude executes them in a persistent session with your repo fully loaded.

### Fixed Cost Economics

The API model was a blank check. You paid per token, per call, per agent turn. A busy week could cost $500. A runaway loop could cost thousands.

Channels changes the economics:

| Model | Cost | Risk |
|-------|------|------|
| Cohort Local (95% of work) | $0 | None -- your GPU |
| Claude Code Subscription | $100-200/mo | Fixed -- you pick the plan |
| Open API (the old way) | $800-2,000+/mo | Unpredictable -- per-token billing |

Cohort manages your fixed budget intelligently:
1. **Local-first triage** -- 95% of agent work runs on your GPU at zero cost
2. **Context distillation** -- local models pre-process and condense before escalating to Claude (70% token reduction)
3. **Session persistence** -- Claude keeps context across tasks, no cold starts
4. **Safeguards** -- token budgets, rate limiting, loop prevention, graceful degradation back to local

### Three Response Tiers

| Mode | Pipeline | Cost |
|------|----------|------|
| **Smart** [S] | Local model, no thinking, 4K budget | $0 |
| **Smarter** [S+] | Local model, thinking enabled, 16K budget | $0 |
| **Smartest** [S++] | Local reasoning + distillation + Claude Code | Fixed monthly |

The Smartest tier is the Channels play: your local agents do the heavy thinking (free), distill it into a structured briefing, and Claude gets a 2-5K token pre-processed prompt instead of 50K of raw context.

---

## The Bigger Narrative

Every multi-agent framework talks about coordination. None of them solved the economics.

CrewAI, LangGraph, Microsoft Agent Framework -- they all route every agent turn through a cloud API. More agents = more cost. More rounds = more cost. More context = more cost. The bill is unpredictable and scales linearly with usage.

Cohort inverted this:
- 95% of work runs locally, for free
- The 5% that needs frontier reasoning is pre-distilled to cut tokens 70%
- And now that 5% runs on a fixed-cost subscription, not open metering

**Channels didn't just give us a transport layer. It completed the economic model.**

---

## VS Code: Where It All Lives

The entire Cohort experience runs inside VS Code. No browser tabs. No context-switching.

**Claude Code writes your code. Cohort's agent team reviews, tests, and coordinates it. Both live inside VS Code -- working together.**

The extension (v0.3.9) operates in two modes:

- **Lite Mode** (no server): Channel management, agent listing, message posting. Works standalone with local JSON storage.
- **Full Mode** (connected to Cohort server): Real-time agent responses, task queue with approval pipeline, meeting control with scoring and phase detection, model settings, benchmarks.

Auto-upgrades from Lite to Full when your server is detected. Eight commands via the command palette. Zero external dependencies beyond Python and the Cohort package.

This matters for the Channels story because the VS Code extension is where most users will experience bidirectional AI. Claude Code sessions launch inside VS Code. Cohort's agent panels live inside VS Code. The handoff between local agents and Claude happens seamlessly in the same window.

---

## Meeting Control: Structured Agent Discussions

Unstructured multi-agent conversations loop. Agents repeat each other, wrong experts dominate, and there's no convergence mechanism. Meetings fix this.

**18 CLI subcommands** (`cohort meet ...`) manage the full lifecycle:

- **Session lifecycle:** start, stop, pause, resume, extend
- **Participant management:** add, remove, promote, demote agents mid-discussion
- **Scoring & introspection:** next-speaker recommendation, per-agent score breakdown, phase detection
- **Standalone mode:** enable/disable meeting gating on any channel without a session

The scoring engine runs offline -- it's deterministic code, not an LLM call. Five dimensions (domain expertise 30%, complementary value 25%, historical success 20%, phase alignment 15%, data ownership 10%) produce a composite score. If the score beats the agent's threshold, they speak. If not, they wait.

**Phase detection** reads recent messages and classifies the discussion as Discover, Plan, Execute, or Validate. Thresholds auto-adjust: early phases are loose (encourage exploration), late phases are tight (force convergence).

**Stakeholder gating** prevents dominant agents: Active (easy to speak), Silent (stepped back voluntarily), Observer (mostly listening), Dormant (can't speak without @mention). Users promote or demote agents in real-time.

This is what other frameworks don't have. CrewAI uses max-iteration timeouts. LangGraph uses manual conditional edges. Cohort's scoring engine makes deterministic turn-allocation decisions that the LLM can't override.

---

## Key Messaging for Channels

**Hero line:** "Claude uses your tools. Your agents use Claude."

**Subline:** "Bidirectional AI at a fixed monthly cost -- not open API metering."

**The speed story:** "Anthropic shipped the protocol at 3 AM. We had it integrated before most people woke up."

**The preparation story:** "Three hours to integrate. Five months of daily production use built the system it plugged into."

**The economic story:** "The API model was a blank check. Channels gives you a fixed budget. Cohort makes every dollar count."

**The technical story:** "MCP-native from day one. 185 CLI commands, 23 agents, full code queue -- all already exposed as MCP tools. Channels was a transport layer, not an architecture change."

---

## Use in Launch Assets

This narrative feeds into:
- **Show HN (07)** -- the Channels angle is the hook that differentiates from every other multi-agent framework post
- **Social posts (08)** -- dedicated Channels tweet thread and Reddit angles
- **Objection handling (05)** -- new objections around Anthropic dependency, cost model, Channels limitations
- **Landing page** -- the live site already leads with the cost/Channels angle (hero + banner)

The Channels story works at every layer:
- **Billboard**: "Your AI agents cost 10-20x more than they should. We fixed that."
- **Spec Sheet**: "MCP-native, bidirectional tool use, fixed-cost escalation via Channels."
- **Proof Story**: "Anthropic shipped Channels at 3 AM. We had working multi-agent roundtables through it by 7 AM."
