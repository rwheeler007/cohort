# Show HN Draft

The single highest-leverage launch moment. Follow HN conventions: factual title, concise comment, let the project speak for itself.

*Updated 2026-03-21: Rewired around Channels integration, cost efficiency angle, and response tiers. Previous version led with zero-deps -- that's now a supporting point, not the hook.*

---

## Title

**Show HN: Cohort -- Multi-agent AI that runs 95% locally and uses Claude Code Channels for the rest**

*Alternatives (pick based on what resonates that week):*
- Show HN: Cohort -- Your AI agents are costing 10-20x more than they should
- Show HN: Cohort -- Bidirectional AI orchestration via Claude Code Channels (open source)

---

## Submission Comment

Hi HN, I'm Ryan. I've been running a production multi-agent system since November 2025 -- 23 specialist agents coordinating daily across code generation, security audits, and strategic planning. The patterns that actually worked are now an open-source Python library.

**The problem Cohort solves:** Every multi-agent framework routes every agent turn through a cloud API. More agents = more cost. More rounds = more cost. The bill is unpredictable and scales linearly with usage. We inverted this.

**How it works:**

- 95% of agent work runs locally on your GPU (qwen3.5:9b, fits on 12GB with room to spare). Zero cost.
- The 5% that needs frontier reasoning gets pre-distilled by local models and escalated to Claude Code via Channels -- at a fixed monthly cost ($100-200), not open API metering.
- Three response tiers: Smart (local, fast, $0), Smarter (local + thinking, $0), Smartest (local reasoning + distillation + Claude, fixed monthly).

**Claude Code Channels** is the part that changed everything. Anthropic shipped the protocol three weeks ago. We had it integrated in three hours -- because Cohort was already MCP-native from day one. Bidirectional tool use: Claude calls your agents (security, QA, content strategy), your agents call Claude (complex refactors, multi-file changes). Same session, persistent context.

**What makes it different:**

- Zero dependencies in core. `pip install cohort` pulls nothing.
- MCP-native architecture -- 185 CLI commands and 23 agents exposed as MCP tools
- 5-dimension contribution scoring (not round-robin)
- Loop prevention, session isolation, context distillation
- Security agent in every workflow + human approval gates
- Meeting control: 18 subcommands for structured discussions with scoring, phase detection, stakeholder gating
- VS Code extension (v0.3.9) -- full agent dashboard in your editor, no browser needed
- 1,100+ tests. Python 3.11-3.13. Apache 2.0.

**Quick start:**

```python
from cohort import JsonFileStorage, Orchestrator
from cohort.chat import ChatManager

chat = ChatManager(JsonFileStorage("my_data"))
chat.create_channel("api-review", "Rate limiting review")
chat.post_message("api-review", sender="user", content="Add rate limiting to the API")

agents = {
    "developer": {"triggers": ["python", "api"], "capabilities": ["backend"]},
    "reviewer":  {"triggers": ["code-review", "testing"], "capabilities": ["qa"]},
    "security":  {"triggers": ["auth", "credentials"], "capabilities": ["appsec"]},
}

orch = Orchestrator(chat, agents=agents)
session = orch.start_session("api-review", "Add rate limiting", initial_agents=list(agents))
rec = orch.get_next_speaker(session.session_id)
print(f"Next: {rec['recommended_speaker']} -- {rec['reason']}")
```

GitHub: [link]
Docs: [link]
Apache 2.0 licensed.

Happy to answer questions about the Channels integration, VS Code extension, meeting control, cost model, scoring algorithm, or what we learned running multi-agent systems in daily production.

---

## HN Comment Strategy

**First hour is critical.** Be responsive. Answer every question directly. Don't be defensive.

**Expected questions and prepared responses:**

### "Why not just use CrewAI/LangGraph?"
> Those are pipeline frameworks -- they define what agents do and in what order. Cohort manages the conversation dynamics: who should speak next, whether they're repeating themselves, and when the topic has shifted enough to bring in new experts. They're complementary -- you could use Cohort's scoring layer inside a CrewAI pipeline. The bigger difference: they route everything through cloud APIs. Cohort runs 95% locally and only escalates the hard stuff via Channels at a fixed cost.

### "Isn't this just an Anthropic lock-in play?"
> Channels is one transport layer, not the product. Cohort runs entirely locally without Channels -- the 23 agents, roundtables, code queue, scoring engine, all of it works on Ollama/llama.cpp. Channels is an optional escalation path for tasks that genuinely need frontier reasoning. If Anthropic disappears tomorrow, you lose the escalation tier, not the system.

### "Zero dependencies means you reimplemented everything?"
> The core is orchestration logic -- scoring, loop detection, chat management, protocols. Pure algorithms on Python data structures. The web dashboard (`cohort[server]`) adds Starlette + Socket.IO. MCP integration (`cohort[mcp]`) adds the protocol bridge. You opt into deps when you need them.

### "How does this compare to Microsoft Agent Framework / OpenAI Agents SDK?"
> Microsoft Agent Framework is enterprise-focused with A2A/MCP/AG-UI protocols -- heavy and Azure-oriented. OpenAI Agents SDK is good plumbing (handoffs, agent-as-tool) but has no contribution scoring, loop prevention, or local-first economics. Neither has a Channels-style fixed-cost escalation model. Cohort is the coordination + economics layer neither provides.

### "What models does it work with?"
> Anything Ollama or llama.cpp can run. Default is qwen3.5:9b (6.6GB, fits on 12GB GPU with room to spare, 104 tok/s on RTX 3080 Ti). Also works with cloud APIs if you prefer -- protocol-first means any inference backend works. The Smartest tier uses Claude Code via Channels for frontier reasoning.

### "Is this production ready?"
> The patterns are proven -- 23 specialist agents have been running in daily production since November 2025. The packaging as a standalone library is v0.3.9. We're transparent about that distinction. 1,100+ tests, CI on three Python versions, Apache 2.0.

### "Fixed cost sounds great, but what happens when you hit the limit?"
> Cohort manages the budget intelligently. Local-first triage (95% free), context distillation (70% token reduction on escalated calls), session persistence (no cold starts), and graceful degradation back to local models when capacity is constrained. You set the ceiling; Cohort makes every interaction count within it.

### "AI agents are a security risk."
> Agreed. That's why there's a security agent in every workflow (not a gate at the end, an active reviewer throughout), human approval gates on sensitive actions, and write access controls. We don't promise zero risk -- we promise known risk with active mitigations. We publish what we do and what we don't yet do.

### "Why would Claude be better through Cohort than directly?"
> Claude alone is one perspective. Through Cohort, Claude gains access to 23 specialist agents with persistent memory, domain expertise, and structured scoring. When Claude calls the security agent through MCP, it gets findings informed by that agent's accumulated knowledge of your codebase. And Cohort manages the session -- context hydration, idle reaping, priority eviction -- so Claude stays efficient within your fixed budget.

---

## Timing

- Post between 8-10am ET on a weekday (Tuesday-Thursday optimal for HN)
- Avoid Mondays (weekend backlog) and Fridays (low engagement)
- Have 2-3 hours blocked for responding to comments immediately after posting

## Anti-Patterns

- Don't astroturf with multiple accounts
- Don't ask friends to upvote (HN detects and penalizes this)
- Don't be defensive in comments -- acknowledge valid criticism
- Don't over-explain -- short, direct responses perform better on HN
- Don't link to the website first -- link to the GitHub repo (HN respects repos over marketing sites)
- Don't oversell Channels as proprietary advantage -- frame it as "we built on open protocols and Anthropic's protocol fit perfectly"
