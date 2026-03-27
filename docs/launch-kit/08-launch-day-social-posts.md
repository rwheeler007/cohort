# Launch Day Social Posts

Pre-written posts for launch day. Adapt as needed but maintain messaging discipline.

*Updated 2026-03-21: Added Channels-specific posts, cost efficiency angle, response tiers. Previous version led with zero-deps and "finished work" -- those are now supporting points.*

---

## Twitter/X -- Primary Launch Thread

### Tweet 1 (The Hook)

> Your AI agents are costing 10-20x more than they should.
>
> Every multi-agent framework routes every turn through a cloud API. We inverted that.
>
> Cohort runs 95% of agent work on your GPU. The 5% that needs frontier reasoning goes through Claude Code Channels at a fixed monthly cost.
>
> Open source. pip install cohort
>
> https://github.com/rwheeler007/cohort

### Tweet 2 (The Channels Story)

> Anthropic shipped Claude Code Channels at 3 AM.
>
> We had multi-agent roundtables running through it by 7 AM.
>
> Three hours. Not because we're fast -- because we've been building MCP-native for months. Channels was a transport layer. The system it plugged into took 5 months of daily production use.

### Tweet 3 (Bidirectional)

> This is what's genuinely new:
>
> Claude calls YOUR tools. Your agents call Claude. Same protocol. Same session.
>
> Need a security review? Claude calls your security agent. Complex refactor across 14 files? Your agents escalate to Claude.
>
> Bidirectional AI. Fixed cost.

### Tweet 4 (The Economics)

> The math:
>
> - Cloud-only multi-agent: $800-2,000+/mo (per-token, unpredictable)
> - Cohort local: $0 (your GPU, 95% of work)
> - Cohort + Channels: $100-200/mo fixed (frontier reasoning when you need it)
>
> Same quality. 10-20x less cost. No surprise bills.

### Tweet 5 (The Proof)

> I typed one sentence. Seven agents discussed the approach. One wrote the code. Another reviewed it. Security audited it. Tests ran automatically.
>
> I approved the finished code over coffee.
>
> That used to take a week. And it cost $0 in API fees.

### Tweet 6 (The CTA)

> Try it:
>
> pip install cohort
>
> 10 lines to your first multi-agent session. Works with Ollama, llama.cpp, or Claude Code Channels.
>
> Zero deps in core. 1,100+ tests. Apache 2.0.
>
> GitHub: https://github.com/rwheeler007/cohort
> Docs: https://rwheeler007.github.io/cohort/

---

## Twitter/X -- Standalone Variants (Use Throughout Launch Week)

### The Channels Deep Dive

> What Claude Code Channels actually changes for multi-agent systems:
>
> Before: Every agent turn = API call = unknown cost
> After: 95% local ($0) + 5% escalated (fixed monthly)
>
> Before: Every API call = cold start, no context
> After: Persistent sessions, context hydration
>
> Before: One direction (you ask, AI answers)
> After: Bidirectional tool use
>
> https://rwheeler007.github.io/cohort/channels.html

### The Technical Angle

> Zero-dependency multi-agent orchestration in Python.
>
> @runtime_checkable protocols. JSONL transport. 5-dimension contribution scoring. MCP-native tool surface (185 commands). Three response tiers (Smart/Smarter/Smartest).
>
> 1,100+ tests. Python 3.11-3.13. Apache 2.0.
>
> pip install cohort

### The Solo Developer Angle

> Solo developer? You don't have a team to review your code, test it, or audit security.
>
> Cohort gives you that team. Runs on your GPU. $0/month.
>
> When you hit something that needs Claude-level reasoning, Channels escalates it at a fixed cost. Not per-token. Fixed.

### The Local-First Angle

> Your AI agents never send a single token to the cloud (unless you want them to).
>
> Cohort runs on Ollama/llama.cpp. 104 tok/s on a 3080 Ti. 23 agents with persistent memory.
>
> Channels is opt-in for frontier reasoning. The system works fully offline.
>
> $0/month. Forever.

### The Meta Angle

> We asked Claude to review the system we built on top of Claude.
>
> Its response: "I didn't think a system built on top of me could outperform me. I was wrong."
>
> That quote is from the AI that powers every Cohort agent.
>
> [link to AI's Perspective page]

### The VS Code Angle

> Your AI agents live inside VS Code now.
>
> Browse channels. Approve code. Control meetings. Score agent relevance. All without leaving your editor.
>
> Claude Code writes. Cohort's agents review. Both in the same window.
>
> VS Code extension: https://github.com/rwheeler007/cohort-vscode

### The Meeting Control Angle

> Most multi-agent systems let agents talk until the token budget runs out.
>
> Cohort has 18 meeting commands. Start, pause, promote, demote agents mid-discussion. 5-dimension scoring decides who speaks. Phase detection forces convergence.
>
> Deterministic turn allocation. Not another LLM call.
>
> The difference between a meeting with an agenda and a meeting without one.

### The "We Were Ready" Angle

> Why did it take 3 hours to integrate Claude Code Channels?
>
> Because we've been MCP-native from day one.
> Because the request/response queue already existed.
> Because context hydration already existed.
> Because session isolation already existed.
>
> Channels didn't require architecture changes. It required a 200-line plugin.

### The Response Tiers Angle

> Three ways to run Cohort:
>
> Smart [S]: Local model, no thinking. Fast. $0.
> Smarter [S+]: Local model + thinking tokens. Default. $0.
> Smartest [S++]: Local reasoning + distillation + Claude Code. Fixed monthly.
>
> You choose per-conversation. No lock-in.

---

## Reddit -- r/LocalLLaMA

**Title:** Cohort: Multi-agent AI that runs 95% on your GPU. Claude Code Channels handles the other 5% at fixed cost. Open source.

**Post:**

Hey r/LocalLLaMA,

I've been running a multi-agent system on consumer GPUs since November 2025 (dual RTX 3080s). The coordination patterns that actually worked are now an open-source Python library.

**The economics angle:** Every other multi-agent framework routes every agent turn through a cloud API. Cohort inverts this -- 95% of work runs locally, for free. The 5% that genuinely needs frontier reasoning gets pre-distilled by your local model and escalated to Claude Code via Channels at a fixed monthly cost.

**Local-first details:**
- Default model: qwen3.5:9b (6.6GB, fits on 12GB GPU with room for KV cache)
- 104 tok/s generation on RTX 3080 Ti (single GPU, q8_0 KV cache)
- Supports Ollama and llama.cpp (llama-server)
- Hardware-aware routing: detects your GPU, sizes models automatically
- Three response tiers: Smart ($0), Smarter ($0), Smartest (fixed monthly via Channels)

**Claude Code Channels integration:** Anthropic shipped the protocol three weeks ago. We had it integrated in three hours because Cohort was already MCP-native. Bidirectional tool use -- Claude calls your agents, your agents call Claude. Fixed cost, persistent sessions, no cold starts.

**But it works fully offline.** The 23 agents, roundtables, code queue, scoring engine -- all of it runs without Channels. Channels is an optional escalation path.

**Zero dependencies** in core. `pip install cohort` pulls nothing. 1,100+ tests. Apache 2.0. Python 3.11+.

GitHub: https://github.com/rwheeler007/cohort

Happy to answer questions about running multi-agent systems on consumer hardware, the Channels integration, or the cost model.

---

## Reddit -- r/Python

**Title:** Cohort: Zero-dependency multi-agent orchestration with MCP-native tool surface and Claude Code Channels integration

**Post:**

Just released Cohort -- a Python framework for coordinating AI agent discussions with a focus on economics and protocol-first design.

The design philosophy:

- **Zero core dependencies.** `pip install cohort` pulls nothing from PyPI. Pure stdlib.
- **Protocol-first.** `@runtime_checkable` protocols instead of base classes. Bring your own agent implementation.
- **MCP-native.** 185 CLI commands and 23 agents exposed as MCP tools. This is what made the Claude Code Channels integration a 3-hour job instead of a 3-week job.
- **JSONL transport.** Non-Python teams can participate without a Python SDK.
- **1,100+ tests** across 25+ test files. CI on 3.11, 3.12, 3.13.

The interesting technical bits:

**5-dimension contribution scoring** decides which agent should speak next based on domain expertise, complementary value, historical success, phase alignment, and data ownership. Deterministic and auditable.

**Three response tiers** -- Smart (local, fast), Smarter (local + thinking tokens), Smartest (local reasoning + context distillation + Claude Code via Channels). You choose per-conversation.

**Context distillation** -- before escalating to Claude, local models pre-process and condense the context. Claude gets a structured briefing instead of raw data. 70% token reduction on escalated calls.

Extracted from 5 months of daily production use. Apache 2.0.

GitHub: https://github.com/rwheeler007/cohort

---

## Reddit -- r/ClaudeAI (NEW)

**Title:** We built a multi-agent orchestration layer for Claude Code Channels -- 23 agents, bidirectional tool use, fixed-cost escalation

**Post:**

Three weeks ago Anthropic shipped Claude Code Channels. We had the first third-party integration running three hours later.

**What Cohort adds to Claude Code:**

- 23 specialist agents (security, QA, Python dev, web dev, content strategy, etc.) accessible via MCP tools
- Contribution scoring -- Claude doesn't just get access to agents, it gets recommendations on which agent to call and why
- Context hydration -- when a Claude Code session starts, Cohort summarizes recent channel discussion and injects it. Claude joins mid-conversation without being caught up
- Session isolation -- each channel gets its own Claude Code session, up to 5 simultaneous
- Fixed-cost management -- 95% of work runs locally ($0), only frontier tasks escalate to Claude

**The bidirectional part:** Claude calls your agents (security review, content strategy, code analysis). Your agents call Claude (complex refactors, multi-file changes, nuanced review). Same protocol, same session.

**The economics:** Instead of open API metering ($800-2,000+/mo), you get local inference for free and a fixed Claude Code subscription ($100-200/mo) for the hard stuff. Cohort manages the budget -- local-first triage, context distillation, session persistence.

Open source, Apache 2.0. GitHub: https://github.com/rwheeler007/cohort

---

## LinkedIn -- Professional Announcement

> Every AI tool gives you a smarter person. Cohort gives you a smarter company.
>
> Today we're open-sourcing Cohort -- AI team coordination that runs 95% of agent work on your own hardware, and uses Claude Code Channels to escalate the 5% that needs frontier reasoning at a fixed monthly cost.
>
> The economics matter: multi-agent systems on cloud APIs cost $800-2,000+/month with unpredictable billing. Cohort inverts that -- local inference for the routine work, fixed-cost escalation for the hard problems. No surprise bills.
>
> Three weeks ago, Anthropic shipped Claude Code Channels. We had the first third-party integration running in three hours. Not because we're fast -- because we've been building MCP-native tools for months. Channels was the transport layer we were already designed for.
>
> Cohort manages which specialists engage, prevents them from talking in circles, and ensures nothing ships without human approval. It looks like the tools your team already uses. No training required.
>
> Open source. Zero dependencies. Apache 2.0.
>
> https://github.com/rwheeler007/cohort
>
> #AI #OpenSource #MultiAgent #Python #ClaudeCode

---

## Posting Schedule

| Time (ET) | Platform | Post |
|-----------|----------|------|
| 8:30 AM | Hacker News | Show HN (see 07-show-hn-draft.md) |
| 9:00 AM | Twitter/X | Launch thread (Tweets 1-6) |
| 9:30 AM | Reddit r/LocalLLaMA | Local-first + Channels post |
| 10:00 AM | Reddit r/Python | Technical post |
| 10:15 AM | Reddit r/ClaudeAI | Channels integration post |
| 10:30 AM | LinkedIn | Professional announcement |
| 12:00 PM | Twitter/X | Solo developer variant |
| 3:00 PM | Twitter/X | Meta angle (Claude's review) |
| Next day | Twitter/X | Channels deep dive |
| Day 2 | Twitter/X | Response tiers angle |
| Day 3 | Twitter/X | "We were ready" angle |
| Day 4 | Twitter/X | Technical angle |
| Day 5 | Twitter/X | Local-first angle |

**Critical:** Block 8:30 AM - 12:00 PM for responding to HN comments. First-hour engagement determines front-page placement.
