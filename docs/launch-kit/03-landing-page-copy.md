# Cohort Landing Page Copy

---

## Hero Section

### Headline (A/B test both)

**Option A:**
# Finished work, not drafts.

**Option B:**
# Day One Productive.

### Subheadline
Cohort coordinates AI specialists into a team that writes, reviews, tests, and ships -- so you get finished results, not suggestions to babysit. It looks like the tools your team already lives in. No training required.

### CTA
`pip install cohort` | [View on GitHub] | [Watch the 60-second demo]

---

## Problem Statement Section

### Headline
# AI isn't underperforming. It's being mismanaged.

Every business bought AI tools. Gave everyone seats. Expected transformation.

The result? Single-digit productivity improvements. Studies from McKinsey, BCG, and Deloitte all confirm it.

**The problem isn't AI. The problem is the tool doesn't match the task.**

The industry sold everyone a better hammer -- smarter autocomplete, faster drafts, cleverer suggestions. But the work that moves businesses forward isn't hammering. It's welding. Coordination. Review. Testing. Handoffs between specialists.

You can swing a hammer a million times at a weld joint and nothing will happen.

**Single AI assistants are the hammer. Cohort is the welding rig.**

---

## Value Props Section

### Headline
# What changes with Cohort

### 1. Finished work, not drafts.
Your AI team writes, reviews, tests, and secures code before you ever see it. No babysitting. No "looks good but let me fix these 12 things." Finished.

### 2. A team, not a tool.
Multiple specialists that coordinate. Security catches what development missed. QA tests what security approved. The right expert handles each part of the job.

### 3. Familiar on day one.
Cohort looks and feels like the team collaboration tools your people already live in. Channels, @mentions, message threads, team panels. They open it and they already know where to click. No training program. No onboarding consultant.

### 4. Security-first, human-in-the-loop.
A dedicated security agent participates in every workflow -- not as a gate at the end, but as an active reviewer throughout. Anything sensitive requires human approval before it ships. Nothing happens in the dark.

### 5. You stay in control.
Human approval gates at every critical step. Transparent scoring shows exactly why each agent spoke and what they contributed. Full audit trail. Your people make the decisions that matter.

### 6. Remembers and improves.
Persistent memory means tomorrow's work builds on today's. Agents learn your codebase, your preferences, your standards. They get better, not just faster.

---

## The Proof Section

### Headline
# One sentence in. Working code out.

> I typed one sentence describing a feature I needed.
>
> Seven agents discussed the approach.
> One wrote the code.
> Another reviewed it for bugs.
> The security agent audited it for vulnerabilities.
> Tests ran automatically.
>
> I approved the finished, working code over coffee.
>
> That used to take a week.

[See the full demo ->]

---

## Anti-Disruption Section

### Headline
# We didn't invent a new way to work. We put AI inside the way you already work.

Every AI company wants to disrupt you. Replace your workflows. Retrain your people. Force you onto their platform.

We're the opposite.

Your people open Cohort and they already know where to click. The channels, the @mentions, the team panels -- it's all muscle memory from the tools they use every day.

**Cohort is a force multiplier for the people and processes you've already invested in.**

Your top engineer who runs tight code reviews? Now that process runs 24/7 with AI specialists following the same rigor. Your marketing lead who coordinates campaigns across 5 channels? Now they have AI specialists handling each channel, coordinated the way they'd coordinate a human team.

Same processes. Same standards. Same discipline. Just dramatically more capacity.

---

## Security Section

### Headline
# Security isn't a feature we added. It's how we built.

AI agents making decisions and acting on them -- that's a new surface. We don't pretend otherwise. We designed for it.

**Security agent in every workflow.** Not a gate at the end. An active reviewer throughout. Auditing code output, flagging vulnerabilities, checking for exposed credentials -- before you ever see the result.

**Human approval on anything sensitive.** The orchestrator knows what's risky. Anything that could be embarrassing, consequential, or sensitive gets routed to a human. Nothing ships without sign-off.

**Your infrastructure, your keys.** Cohort runs on your hardware. Zero credentials stored in the product. Your API keys never leave your network.

**Transparent architecture.** We publish what we do and what we don't yet do. Honest security posture beats security theater.

> The question isn't "is this perfectly secure?" Nothing is. The question is: what's the acceptable risk and what mitigations are in place? Cohort is designed for that question.

---

## How It Works Section

### Headline
# Three things happen when you give Cohort a task

**1. The right specialists engage.**
Cohort's contribution scoring evaluates which agents are most relevant to your request across five dimensions -- expertise, novelty, ownership, phase alignment, and data access. Wrong experts stay quiet. Right experts step forward.

**2. They coordinate, not collide.**
Loop prevention stops agents from repeating each other. Stakeholder gating adjusts who speaks as the conversation evolves. Topic shift detection brings in new experts when the discussion changes direction.

**3. You get finished output.**
Code is written, reviewed, tested, and security-audited. Marketing copy is drafted, edited, and formatted per platform. Strategic plans are debated from multiple angles before landing as recommendations. Whatever the task, the output is finished -- and a human approved it before it shipped.

---

## Comparison Section

### Headline
# AI Tool vs. AI Team

| | Single AI Tool | Cohort |
|---|---|---|
| You ask for a feature | You get a code suggestion | You get reviewed, tested, secured code |
| Something has a bug | You find it yourself | The review agent already caught it |
| Security vulnerability | Hope you notice | Security agent flagged it before you saw the code |
| Credentials in the code | Hope you catch it before push | Security agent blocked it automatically |
| Next day, similar task | Starts from scratch | Builds on yesterday's context |
| Your role | Edit, verify, fix, test, repeat | Review and approve |

---

## Technical Credibility Section (Below the Fold)

### For Engineers

- **Zero dependencies** in the core library. Pure Python stdlib.
- **Protocol-first**: `@runtime_checkable` protocols, not base classes. Bring your own agent implementation.
- **Polyglot transport**: JSONL file format lets teams in any language participate without a Python SDK.
- **5-dimension contribution scoring**: Domain expertise, complementary value, historical success, phase alignment, data ownership. Deterministic, auditable, no black box.
- **Security agent architecture**: Participates in orchestration loop, not bolted on as a post-process. Write access controls, credential detection, human approval gates.
- **Hardware-aware local LLM routing**: Detects your GPU, sizes models automatically. Three response tiers: smart (fast), smarter (default), smartest (premium quality).
- **488+ tests** across 26 test files. Python 3.11, 3.12, 3.13.
- **MIT licensed.** Extracted from 18 months of production infrastructure.

```
pip install cohort              # Core (zero deps)
pip install cohort[server]      # HTTP + Socket.IO dashboard
pip install cohort[claude]      # Claude Code MCP bridge
```

---

## CTA Section

### Headline
# Stop giving your team better hammers.

Cohort is open source, free, and ready today. The safe bet that performs.

`pip install cohort`

[GitHub Repository] | [Documentation] | [Join the Community]

---

## Footer Tagline
**Cohort** -- AI team coordination. Your agents, thinking together.
