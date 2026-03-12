# Launch Day Social Posts

Pre-written posts for launch day. Adapt as needed but maintain messaging discipline.

---

## Twitter/X -- Primary Launch Thread

### Tweet 1 (The Hook)
> ChatGPT is a person. Cohort is a company.
>
> We just open-sourced an AI team coordination framework. Zero dependencies. Runs locally. Ships finished work, not drafts.
>
> pip install cohort
>
> [GitHub link]

### Tweet 2 (The Problem)
> The AI industry sold everyone a better hammer.
>
> But the work that moves businesses forward -- coordination, review, testing, handoffs -- that's welding.
>
> You can swing a hammer a million times at a weld joint and nothing happens.

### Tweet 3 (The Proof)
> I typed one sentence. Seven agents discussed the approach. One wrote the code. Another reviewed it. Security audited it. Tests ran automatically.
>
> I approved the finished code over coffee.
>
> That used to take a week.

### Tweet 4 (The Differentiators)
> What makes Cohort different:
>
> - Zero deps in core (CrewAI has 25+)
> - Runs on your GPU, no API keys
> - 5-dimension contribution scoring (not round-robin)
> - Loop prevention (agents stop talking in circles)
> - Security agent in every workflow
> - 785+ tests
>
> Apache 2.0

### Tweet 5 (The CTA)
> Try it:
>
> pip install cohort
>
> 10 lines to your first multi-agent session. Works with Ollama, llama.cpp, or any cloud API.
>
> GitHub: [link]
> Docs: [link]
>
> Built from 18 months of production use. Not theory.

---

## Twitter/X -- Standalone Variants (Use Throughout Launch Week)

### The Technical Angle
> Zero-dependency multi-agent orchestration in Python.
>
> @runtime_checkable protocols. JSONL transport. 5-dimension contribution scoring. No base classes, no vendor lock-in.
>
> 785+ tests. Python 3.11-3.13. Apache 2.0.
>
> pip install cohort

### The Solo Developer Angle
> Solo developer? You don't have a team to review your code, test it, or audit security.
>
> Cohort gives you that team. One sentence in, finished code out. Security agent catches what you'd miss at 2am.
>
> Free. Local. Zero API costs.

### The Local-First Angle
> Your AI agents never send a single token to the cloud.
>
> Cohort runs on Ollama/llama.cpp. Your GPU, your data, your agents. Default model fits on a 12GB GPU with room to spare.
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

---

## Reddit -- r/LocalLLaMA

**Title:** Cohort: Open-source multi-agent orchestration that runs entirely on local models (Ollama/llama.cpp). Zero dependencies, zero API costs.

**Post:**

Hey r/LocalLLaMA,

I've been running a multi-agent system on consumer GPUs for 18 months (dual RTX 3080s). The coordination patterns that actually worked are now packaged as an open-source Python library.

**What it does:** Manages structured discussions between AI agents with contribution scoring and loop prevention. A security agent reviews everything. You approve the final output.

**Local-first details:**
- Default model: qwen3.5:9b (6.6GB, fits on 12GB GPU with room for KV cache)
- Supports Ollama and llama.cpp (llama-server)
- Hardware-aware routing: detects your GPU, sizes models automatically
- 31 tok/s generation on RTX 3080

**Zero dependencies** in core. `pip install cohort` pulls nothing. Server dashboard and Claude integration are optional extras.

785+ tests. Apache 2.0. Python 3.11+.

GitHub: [link]

Happy to answer questions about running multi-agent systems on consumer hardware.

---

## Reddit -- r/Python

**Title:** Cohort: Zero-dependency multi-agent orchestration framework. Protocol-first, 785+ tests, Python 3.11-3.13.

**Post:**

Just released Cohort -- a Python framework for coordinating AI agent discussions.

The design philosophy:

- **Zero core dependencies.** `pip install cohort` pulls nothing from PyPI. Pure stdlib.
- **Protocol-first.** `@runtime_checkable` protocols instead of base classes. Bring your own agent implementation.
- **JSONL transport.** Non-Python teams can participate without a Python SDK.
- **785+ tests** across 25 test files. CI on 3.11, 3.12, 3.13.

The interesting technical bit is the 5-dimension contribution scoring engine -- it decides which agent should speak next based on domain expertise, complementary value, historical success, phase alignment, and data ownership. Deterministic and auditable.

Extracted from 18 months of production use. Apache 2.0.

GitHub: [link]

---

## LinkedIn -- Professional Announcement

> Every AI tool gives you a smarter person. Cohort gives you a smarter company.
>
> Today we're open-sourcing Cohort -- AI team coordination that turns individual AI tools into a coordinated team of specialists.
>
> The insight is simple: AI productivity gains come from integration, not intelligence. Businesses that gave everyone a smarter assistant got single-digit improvements. Businesses that coordinated AI specialists into teams -- with review, testing, security, and human approval -- got transformation.
>
> Cohort is the coordination layer the industry skipped. It manages which specialists engage, prevents them from talking in circles, and ensures nothing ships without human approval. It looks like the tools your team already uses. No training required.
>
> Open source. Zero dependencies. Runs on your hardware.
>
> [GitHub link]
>
> #AI #OpenSource #MultiAgent #Python

---

## Posting Schedule

| Time (ET) | Platform | Post |
|-----------|----------|------|
| 8:30 AM | Hacker News | Show HN (see 07-show-hn-draft.md) |
| 9:00 AM | Twitter/X | Launch thread (Tweets 1-5) |
| 9:30 AM | Reddit r/LocalLLaMA | Local-first post |
| 10:00 AM | Reddit r/Python | Technical post |
| 10:30 AM | LinkedIn | Professional announcement |
| 12:00 PM | Twitter/X | Solo developer variant |
| 3:00 PM | Twitter/X | Meta angle (Claude's review) |
| Next day | Twitter/X | Technical angle |
| Day 3 | Twitter/X | Local-first angle |

**Critical:** Block 8:30 AM - 12:00 PM for responding to HN comments. First-hour engagement determines front-page placement.
