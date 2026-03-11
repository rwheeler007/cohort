# Case Study: Cohort Website — Idea to Shipped Product in 24 Hours

**Date:** March 10-11, 2026
**Project:** Cohort Website Creator + cohort.dev marketing site
**Method:** Multi-agent orchestration via BOSS/SMACK + Claude Code

---

## Timeline

| Time | Milestone |
|------|-----------|
| **Mar 10, 14:51** | First commit: `Add Website Creator pipeline -- multi-agent website generation` |
| **Mar 10, 15:20** | Brand identity applied (terracotta + charcoal palette, Press Start 2P font, Google Fonts) |
| **Mar 10, 16:26** | Briefing history UI, agent narratives, content filters |
| **Mar 10, 17:31** | Site copy revamp, features page, executive briefing page |
| **Mar 10, 18:10** | Benchmarks page, Tools page, nav restructure |
| **Mar 10, ~18:30-20:00** | Compiled roundtable review: 6 agents (marketing, web dev, CEO, content, security, brand) critique site in single LLM call. Hero rewrite, cost comparison, nav/footer cleanup |
| **Mar 10, 20:05** | robots.txt + sitemap.xml generated |
| **Mar 10, 20:11** | Full site v2 regenerated from updated site_brief.yaml |
| **Mar 10, ~21:00** | Comparison page: Cohort vs CrewAI vs LangGraph (3-way) |
| **Mar 10, ~22:00** | OpenClaw added to comparison (4-way), CEO profit-path reframing |
| **Mar 11, 12:15** | Team page with agent cards, footer.js, nav dropdowns |
| **Mar 11, 13:00** | Footer columns, team data, card prototypes |
| **Mar 11, 14:26** | All 15 pages regenerated — final render pass |
| **Mar 11, 14:46** | Product showcase with animated chat demo on hero page |
| **Mar 11, 14:55** | conversation-meta.json — real roundtable transcripts embedded |

**Total elapsed:** ~24 hours from first commit to shipped 15-page marketing site.

---

## What Was Built

### The Pipeline (Reusable Tool)

A **YAML-in, HTML-out website generator** — 1,241 lines of Python across 4 modules:

| Module | Lines | Purpose |
|--------|-------|---------|
| `pipeline.py` | 224 | Orchestrator: scrape → worksheet → roundtables → render → validate |
| `intake.py` | 325 | Async competitor scraping (httpx) + 20-question brief worksheet |
| `renderer.py` | 337 | Jinja2 template engine with design token injection |
| `site_brief.py` | 355 | Typed dataclass schema (15 nested classes) with YAML serialization |

Plus **10 Jinja2 templates** and a **CSS token system** that generates responsive stylesheets from brand configuration.

### The Website (Cohort's Own)

**15 pages, 535KB total HTML+CSS+JS:**

| Page | Purpose |
|------|---------|
| **index.html** (54KB) | Hero with animated chat demo showing a live roundtable |
| **features.html** (26KB) | "Under the Hood" — pipeline, roundtables, scoring, memory |
| **tools.html** (31KB) | Agent ecosystem and tool registry |
| **use-cases.html** (22KB) | Customer scenarios with pain-point framing |
| **pricing.html** (31KB) | Three tiers: Open Source (free) / Pro ($49/mo) / Enterprise ($299+/mo) |
| **marketing.html** (56KB) | Content pipeline deep-dive |
| **benchmarks.html** (49KB) | Performance metrics vs competitors |
| **compare.html** (39KB) | 4-way feature matrix: Cohort vs CrewAI vs LangGraph vs OpenClaw |
| **team.html** (20KB) | Agent profiles as "team member" cards |
| **ai-perspective.html** (13KB) | "What Cohort Thinks About Itself" — agents review their own product |
| **conversations.html** (53KB) | Real roundtable transcripts from SMACK channels |
| **docs.html** (29KB) | Technical documentation hub |
| **contact.html** (7KB) | Contact form |
| **preview-hero-proposal.html** | A/B test variant for hero section |
| **team-card-prototype.html** | Card layout prototype |

### Design System

- **Colors:** Terracotta (#D97757) primary, charcoal (#2B2D31) secondary, warm cream (#F5E6DE) accent
- **Typography:** Press Start 2P (headings, retro/pixel aesthetic) + system fonts (body)
- **Accessibility:** WCAG 2.1 AA contrast, skip-to-main links, semantic HTML, keyboard focus states, `prefers-reduced-motion` support
- **SEO:** Open Graph tags, Twitter cards, canonical URLs, sitemap.xml, robots.txt, meta descriptions per page
- **Responsive:** Mobile hamburger nav, grid breakpoints, responsive typography scales

---

## How It Happened

### Phase 1: Infrastructure Sprint (2 hours)

The website creator pipeline was built first as a **reusable tool**, not a one-off site. The typed YAML schema (`site_brief.py`) acts as a contract between agents and templates — any agent can modify the spec without touching HTML. The Jinja2 renderer consumes design tokens from the same YAML, so brand changes propagate to all pages instantly.

### Phase 2: Content Generation (3 hours)

A 66KB `cohort_site_brief.yaml` was populated with product copy, feature descriptions, pricing tiers, testimonials, and competitive positioning. Content was drafted through SMACK channel discussions with agent input, then structured into the YAML schema.

### Phase 3: Multi-Agent Review (1 hour)

A **compiled roundtable** loaded 6 agent personas (marketing, web developer, CEO, content strategy, security, brand design) into a single LLM context. Key outcomes from that review:

- **Marketing:** "Current copy lacks urgency. Try 'Stop Hiring. Start Deploying.'" → adopted
- **Web Developer:** "Orange on white fails WCAG AA contrast check" → fixed
- **CEO:** "Remove 'without an IT degree' — it's patronizing. Frame as ROI." → rewritten
- **Content Strategy:** "Replace generic feature cards with use-case-specific copy" → done
- **Security:** "XSS risk in form inputs. FTC compliance check on 'zero human' claims" → addressed

The review cost one LLM call (~28K tokens) instead of six sequential agent sessions.

### Phase 4: Polish & Differentiation (remaining hours)

Comparison pages, benchmark data, team profiles, animated hero demo, real conversation transcripts, and A/B test prototypes were added iteratively. Each change followed the same loop: edit YAML → regenerate → review.

---

## Key Metrics

| Metric | Value |
|--------|-------|
| Time to first rendered page | ~30 minutes from project start |
| Time to full 15-page site | ~24 hours |
| Python code written | 1,241 lines (pipeline) |
| Templates created | 10 Jinja2 templates |
| Generated output | 535KB across 15 pages + CSS + JS |
| Site brief size | 66KB YAML |
| Git commits | 19 |
| Agent personas involved | 6 (compiled roundtable) + CEO oversight |
| LLM cost for roundtable review | 1 call (~28K tokens) via local Ollama |
| External API costs | $0 (all local inference) |

---

## What Made It Fast

1. **YAML as the universal contract.** Every agent reads/writes the same schema. No format translation, no hand-off friction. Change the YAML, regenerate the site.

2. **Compiled roundtable.** 6-agent design review in one LLM call instead of six sequential sessions. 90% token reduction. The agents debated WCAG compliance, copy tone, and security implications simultaneously.

3. **Template-first rendering.** Jinja2 templates with CSS custom properties mean brand changes are a YAML edit, not a find-and-replace across 15 files. The `styles.css.j2` token system generates all responsive styles from 8 brand variables.

4. **Page protection.** The renderer skips files that have been hand-edited (unless `overwrite: true`), so manual polish and automated regeneration coexist without conflict.

5. **Eat your own dogfood.** The website creator is a Cohort feature (planned for Pro tier). Building Cohort's own site with it validated the tool while producing a real deliverable. Two outcomes from one effort.

6. **No context switching.** Everything happened in BOSS/SMACK: planning in channels, roundtable review via MCP, code generation via Claude Code, content iteration via YAML edits. One environment, one workflow.

---

## Lessons Learned

- **Start with the schema, not the pages.** The typed dataclass system (`site_brief.py`) took ~1 hour but saved many more by making every downstream step mechanical.
- **Agent review catches things humans miss at speed.** The WCAG contrast failure and FTC compliance flag came from agents, not manual review.
- **66KB of YAML is a feature, not a problem.** The site brief is large because it captures every decision. That's the point — it's a complete, versionable, diffable record of the entire website's content and design.
- **Prototypes in production.** The A/B hero variant and team card prototype shipped alongside the main site. No separate staging environment needed — just extra HTML files.

---

## Architecture Diagram

```
User Request
    |
    v
[intake.py] -----> Scrape competitors (async)
    |                    |
    v                    v
20-Question       SiteAnalysis
Worksheet         (colors, fonts,
    |              nav, CTAs)
    v
[site_brief.py] <--- Merge answers + analysis
    |
    v
site_brief.yaml (66KB contract)
    |
    +---> [Compiled Roundtable] ---> Design/copy/security decisions
    |           (6 agents, 1 call)        |
    |                                     v
    |                              Updated YAML
    |
    v
[renderer.py] + templates/*.j2 + tokens/styles.css.j2
    |
    v
output/cohort/
    15 HTML pages
    styles.css
    nav.js + footer.js
    sitemap.xml + robots.txt
```

---

*This case study documents the creation of the Cohort marketing website as a proof-of-concept for the Website Creator pipeline. The same pipeline is designed to generate websites for any product given a completed site brief.*
