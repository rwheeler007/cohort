# Show HN Draft

The single highest-leverage launch moment. Follow HN conventions: factual title, concise comment, let the project speak for itself.

---

## Title

**Show HN: Cohort -- Zero-dependency multi-agent orchestration with contribution scoring**

*Alternatives (pick based on what resonates that week):*
- Show HN: Cohort -- Coordinate AI agents that ship finished work, not drafts
- Show HN: Cohort -- Open-source AI team coordination (zero deps, local-first)

---

## Submission Comment

Hi HN, I'm Ryan. I built Cohort after running a multi-agent system with 60+ AI specialists for 18 months. The patterns that actually worked -- contribution scoring, loop prevention, structured handoffs -- are now packaged as an open-source Python library.

**What it does:** Cohort manages structured discussions between AI agents. Instead of round-robin or letting the LLM decide who speaks, it scores contributions across five dimensions (domain expertise, complementary value, historical success, phase alignment, data ownership) and prevents conversational loops. A security agent participates in every workflow, and nothing ships without human approval.

**What makes it different:**

- Zero dependencies in the core. `pip install cohort` pulls nothing from PyPI.
- Runs locally on Ollama/llama.cpp. No API keys required.
- Protocol-first (`@runtime_checkable`). Bring your own agents, storage, and inference.
- 785+ tests. Python 3.11, 3.12, 3.13.
- Extracted from production, not designed from theory.

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

The orchestrator handles who speaks next, prevents loops, and produces finished output.

GitHub: [link]
Docs: [link]
Apache 2.0 licensed.

Happy to answer questions about the scoring algorithm, loop prevention mechanics, or what we learned running 60+ agents in production.

---

## HN Comment Strategy

**First hour is critical.** Be responsive. Answer every question directly. Don't be defensive.

**Expected questions and prepared responses:**

### "Why not just use CrewAI/LangGraph?"
> Those are pipeline frameworks -- they define what agents do and in what order. Cohort manages the conversation dynamics: who should speak next, whether they're repeating themselves, and when the topic has shifted enough to bring in new experts. They're complementary -- you could use Cohort's scoring layer inside a CrewAI pipeline.

### "Zero dependencies means you reimplemented everything?"
> The core is orchestration logic -- scoring, loop detection, chat management, protocols. Pure algorithms on Python data structures. The web dashboard (`cohort[server]`) adds Starlette + Socket.IO. MCP integration (`cohort[mcp]`) adds the protocol bridge. You opt into deps when you need them.

### "How does this compare to Microsoft Agent Framework / OpenAI Agents SDK?"
> Microsoft Agent Framework (AutoGen + Semantic Kernel merger) is enterprise-focused with A2A/MCP/AG-UI protocols -- heavy and Azure-oriented. OpenAI Agents SDK is good plumbing (handoffs, agent-as-tool) but has no contribution scoring or loop prevention. Cohort is the coordination layer neither provides: who should speak, are they repeating, has the topic shifted.

### "What models does it work with?"
> Anything Ollama or llama.cpp can run. Default is qwen3.5:9b (fits on a 12GB GPU with room to spare). Also works with cloud APIs if you prefer -- protocol-first means any inference backend works.

### "Is this production ready?"
> The patterns are proven -- they've been running in production for 18 months. The packaging as a standalone library is new (v0.3.0). We're transparent about that distinction. 785+ tests, CI on three Python versions, Apache 2.0.

### "AI agents are a security risk."
> Agreed. That's why there's a security agent in every workflow (not a gate at the end, an active reviewer throughout), human approval gates on sensitive actions, and write access controls. We don't promise zero risk -- we promise known risk with active mitigations. We publish what we do and what we don't yet do.

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
