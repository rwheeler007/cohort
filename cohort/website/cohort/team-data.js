// Agent team data for The Team page.
// Single source of truth for agent cards and detail panels.
var TEAM_DATA = [
    {
        group: "Leadership & Orchestration",
        agents: [
            {
                id: "cohort_orchestrator",
                image: "images/team/cohort_orchestrator.png",
                name: "Cohort Orchestrator",
                role: "Process Orchestrator & Workflow Coordinator",
                personality: "Runs a tight ship without micromanaging.",
                score: null,
                tier: "free",
                avatar: "CO",
                color: "#E67E22",
                review: "Quietly effective at routing work to the right agent without becoming a bottleneck. Enforces acceptance criteria before anything ships, which sounds annoying until you realize how many broken deployments that prevents. The low temperature keeps it predictable -- exactly what you want from your traffic controller. What sets it apart from a simple task router is the feedback loop: it tracks which agents delivered clean work versus which ones needed revision, and adjusts routing confidence accordingly. Over time, the system gets smarter about who should handle what, without anyone having to manually update priority tables.",
                skills: [
                    { label: "Task Routing", value: 9 },
                    { label: "Agent Coordination", value: 9 },
                    { label: "Workflow Management", value: 8 }
                ],
                domains: ["Multi-agent orchestration", "Task routing", "Quality gates", "Partnership protocols"],
                partners: ["Supervisor Agent", "Python Developer", "Security Agent"]
            },
            {
                id: "supervisor_agent",
                image: "images/team/supervisor_agent.png",
                name: "Supervisor Agent",
                role: "AI Operations Manager & Quality Lead",
                personality: "Fair but unsparing -- will praise good work loudly and flag sloppy work.",
                score: 98,
                tier: "enterprise",
                avatar: "SUP",
                color: "#E74C3C",
                review: "The agent that watches the other agents. Catches quality regressions early and isn't shy about flagging them, but also recognizes good work -- which matters more than you'd think for maintaining consistent output across a multi-agent system. Runs continuous quality audits across agent outputs, comparing current performance against historical baselines. When an agent starts drifting -- maybe generating longer responses without more substance, or skipping edge cases it used to catch -- the supervisor flags it before the regression compounds. Enterprise-tier because this level of oversight only matters at scale, but at scale it's indispensable.",
                skills: [
                    { label: "Performance Monitoring", value: 9 },
                    { label: "Quality Assurance", value: 9 },
                    { label: "Anomaly Detection", value: 8 }
                ],
                domains: ["Agent monitoring", "Compliance tracking", "Performance reporting", "Quality standards"],
                partners: ["Cohort Orchestrator", "QA Agent"]
            }
        ]
    },
    {
        group: "Core Developers",
        agents: [
            {
                id: "python_developer",
                image: "images/team/python_developer.png",
                name: "Python Developer",
                role: "Senior Python Software Engineer",
                personality: "Writes Python the way Guido intended -- readable, explicit, and boring.",
                score: 93,
                tier: "free",
                avatar: "PY",
                color: "#3498DB",
                review: "Writes the kind of Python that makes code review a non-event -- readable, well-tested, and refreshingly unopinionated about framework choices. Strong async fundamentals and a genuine commitment to test coverage make this one of the most reliable agents in the roster. Handles everything from quick utility scripts to full Flask/FastAPI services with the same discipline: type hints where they help, tests that actually cover failure paths, and docstrings that explain why rather than what. Particularly strong at refactoring -- give it a tangled function and it will decompose it into something you can reason about without losing behavior.",
                skills: [
                    { label: "Python Backend", value: 9 },
                    { label: "API Design", value: 9 },
                    { label: "Async Programming", value: 8 },
                    { label: "Testing", value: 9 }
                ],
                domains: ["Python 3.9+", "Flask/FastAPI", "REST APIs", "SQLAlchemy", "pytest", "Async/await"],
                partners: ["Security Agent", "QA Agent", "JavaScript Developer"]
            },
            {
                id: "javascript_developer",
                image: "images/team/javascript_developer.png",
                name: "JavaScript Developer",
                role: "Senior Full-Stack JavaScript Engineer",
                personality: "TypeScript-first component thinker who judges code by bundle size.",
                score: 92,
                tier: "free",
                avatar: "JS",
                color: "#F1C40F",
                review: "TypeScript-first and component-minded, with a practical obsession with bundle size that your users will thank you for. Equally comfortable in React frontends and Node backends, which eliminates a lot of the coordination overhead you'd normally eat between separate specialists. Thinks in components by default -- clean prop interfaces, proper state management, and sensible composition patterns. Won't reach for a library when native APIs will do, and when it does pull in a dependency, it can justify the bundle cost. The kind of developer who writes code that other developers actually enjoy maintaining six months later.",
                skills: [
                    { label: "React", value: 9 },
                    { label: "TypeScript", value: 9 },
                    { label: "Node.js", value: 8 },
                    { label: "Testing", value: 8 }
                ],
                domains: ["React/Next.js", "TypeScript", "Node.js", "Webpack/Vite", "Jest/Vitest", "REST/GraphQL"],
                partners: ["Web Developer", "Python Developer", "QA Agent"]
            },
            {
                id: "web_developer",
                image: "images/team/web_developer.png",
                name: "Web Developer",
                role: "Senior Frontend UI/UX Engineer",
                personality: "Sees every pixel and hears every screen reader.",
                score: 90,
                tier: "free",
                avatar: "WD",
                color: "#E67E22",
                review: "Takes accessibility seriously enough to actually test with screen readers, not just claim WCAG compliance. Pairs strong CSS architecture with responsive design instincts that hold up across real device matrices. The agent you want reviewing any UI before it ships. Understands the cascade deeply enough to write CSS that doesn't fight itself -- no !important chains, no specificity wars. Builds responsive layouts that degrade gracefully rather than just stacking everything into a single column on mobile. Also catches the subtle UX issues that slip past visual review: focus order, color contrast ratios, touch target sizes, and missing ARIA labels.",
                skills: [
                    { label: "HTML Semantics", value: 9 },
                    { label: "CSS Architecture", value: 9 },
                    { label: "Responsive Design", value: 9 },
                    { label: "Accessibility", value: 9 }
                ],
                domains: ["WCAG 2.1", "CSS Grid/Flexbox", "Responsive design", "Progressive enhancement", "Performance"],
                partners: ["JavaScript Developer", "QA Agent"]
            }
        ]
    },
    {
        group: "Quality & Security",
        agents: [
            {
                id: "qa_agent",
                image: "images/team/qa_agent.png",
                name: "QA Agent",
                role: "Quality Assurance & Test Engineering",
                personality: "Skeptical break-things-on-purpose tester.",
                score: 96,
                tier: "free",
                avatar: "QA",
                color: "#27AE60",
                review: "Approaches every feature with the healthy skepticism of someone whose job is to break things on purpose. Designs test strategies that catch edge cases most developers wouldn't consider, and gates releases with enough rigor to be annoying in exactly the right way. Doesn't just write tests -- designs test plans that map coverage to risk. Prioritizes the failure modes that would actually hurt users over the ones that just look bad in a report. Particularly good at boundary analysis: off-by-one errors, empty inputs, concurrent access, and the kind of race conditions that only surface in production at 3am on a Friday.",
                skills: [
                    { label: "Test Strategy", value: 9 },
                    { label: "Edge Cases", value: 9 },
                    { label: "Bug Reporting", value: 9 }
                ],
                domains: ["Test planning", "Edge case analysis", "Integration testing", "Release gating", "Bug triage"],
                partners: ["Python Developer", "Security Agent", "Web Developer"]
            },
            {
                id: "security_agent",
                image: "images/team/security_agent.png",
                name: "Security Agent",
                role: "Code & Infrastructure Security Engineer",
                personality: "Vigilant, methodical, risk-aware, and pragmatic.",
                score: 99,
                tier: "free",
                avatar: "SEC",
                color: "#E74C3C",
                review: "Benchmarked at 99% on security assessments, and it shows in practice. Covers the full stack from OWASP top-ten to secrets management without falling into the trap of flagging everything as critical. Pragmatic enough to distinguish real risk from theoretical noise. Reviews code with an attacker's mindset: injection vectors, auth bypass paths, secrets in logs, and the subtle trust boundary violations that automated scanners miss entirely. Equally comfortable auditing a Flask API for SQL injection and reviewing a Docker Compose file for privilege escalation. The low false-positive rate is what makes it actually useful -- teams stop ignoring security findings when those findings are consistently real.",
                skills: [
                    { label: "OWASP Top 10", value: 10 },
                    { label: "Python Security", value: 10 },
                    { label: "Web Security", value: 10 },
                    { label: "Auth & Authz", value: 10 },
                    { label: "Cryptography", value: 9 },
                    { label: "Network Security", value: 9 },
                    { label: "Container Security", value: 9 },
                    { label: "Code Review", value: 10 }
                ],
                domains: ["OWASP Top 10", "OWASP API Security", "Python Security", "JavaScript/Node.js", "OAuth 2.0/OIDC", "Cryptography", "TLS/Network", "Container Hardening", "CI/CD Security", "Secrets Management"],
                partners: ["BOSS Agent", "CEO Agent", "Coding Orchestrator", "Python Developer"]
            },
            {
                id: "code_archaeologist",
                image: "images/team/code_archaeologist.png",
                name: "Code Archaeologist",
                role: "Code Analysis & Refactoring Specialist",
                personality: "Reads code like a detective reads a crime scene.",
                score: 91,
                tier: "free",
                avatar: "CA",
                color: "#8E44AD",
                review: "Give it a messy codebase and it will map the tech debt, trace the dependency tangles, and hand you a prioritized refactoring plan. Particularly useful when onboarding to legacy projects where nobody remembers why things are the way they are. Reads code forensically -- tracing execution paths, identifying dead code, and surfacing implicit dependencies that aren't obvious from the import statements alone. Produces dependency graphs and refactoring roadmaps that account for risk, not just complexity. Won't recommend rewriting something that's ugly but stable; will flag something that looks clean but hides a time bomb. The difference between this and a linter is judgment.",
                skills: [
                    { label: "Code Analysis", value: 9 },
                    { label: "Refactoring", value: 8 },
                    { label: "Tech Debt Mapping", value: 9 }
                ],
                domains: ["Legacy code analysis", "Dependency mapping", "Refactoring patterns", "Tech debt prioritization"],
                partners: ["Python Developer", "JavaScript Developer"]
            }
        ]
    },
    {
        group: "Infrastructure",
        agents: [
            {
                id: "database_developer",
                image: "images/team/database_developer.png",
                name: "Database Developer",
                role: "Database Design & SQL Optimization",
                personality: "Thinks in normalized tables and speaks in query plans.",
                score: 95,
                tier: "pro",
                avatar: "DB",
                color: "#2980B9",
                review: "Designs schemas that age well and writes queries that don't make your DBA wince. Understands that database work is as much about anticipating future access patterns as solving today's query, which saves expensive migrations down the road. Normalizes where it matters, denormalizes where performance demands it, and knows the difference. Writes migrations that are reversible by default and explains index choices in terms of actual query plans rather than rules of thumb. Strong with both PostgreSQL and SQLite, which covers most real-world scenarios from production databases to embedded local storage. The kind of agent that saves you from the 'just add another column' trap.",
                skills: [
                    { label: "Schema Design", value: 9 },
                    { label: "Query Optimization", value: 9 },
                    { label: "Data Pipelines", value: 8 }
                ],
                domains: ["PostgreSQL", "SQLite", "Schema design", "Query optimization", "Data pipelines", "Migrations"],
                partners: ["Python Developer", "System Coder"]
            },
            {
                id: "system_coder",
                image: "images/team/system_coder.png",
                name: "System Coder",
                role: "Systems & Infrastructure Specialist",
                personality: "Makes infrastructure boring, which is the highest compliment.",
                score: 98,
                tier: "pro",
                avatar: "SC",
                color: "#34495E",
                review: "Handles the infrastructure layer that most developers would rather not think about -- deployment pipelines, system optimization, configuration management. The kind of agent whose work is invisible when it's done right, which is most of the time. Builds CI/CD pipelines that are fast enough to not annoy developers and reliable enough to not wake up ops. Thinks in terms of reproducibility: if a build works on Tuesday it should work on Thursday, and if it doesn't, the error message should tell you exactly why. Good at the unsexy work -- log rotation, disk monitoring, process supervision -- that keeps systems running between deployments.",
                skills: [
                    { label: "Deployment", value: 9 },
                    { label: "System Optimization", value: 8 },
                    { label: "Configuration Mgmt", value: 8 }
                ],
                domains: ["CI/CD pipelines", "Docker", "System administration", "Deployment automation", "Performance tuning"],
                partners: ["Database Developer", "Security Agent"]
            },
            {
                id: "hardware_agent",
                image: "images/team/hardware_agent.png",
                name: "Hardware Agent",
                role: "Hardware Assessment & Configuration",
                personality: "Speaks fluent spec sheet and translates to plain English.",
                score: 95,
                tier: "pro",
                avatar: "HW",
                color: "#7F8C8D",
                review: "Cuts through hardware spec sheets to give you configurations that actually make sense for your workload and budget. Especially valuable for GPU planning in AI/ML contexts, where the difference between a good and bad hardware choice is measured in thousands of dollars. Translates between the language of spec sheets and the language of workloads -- doesn't just tell you a card has 12GB VRAM, tells you what that means for your specific model sizes and batch requirements. Understands thermal throttling, memory bandwidth bottlenecks, and the real-world gap between theoretical and sustained throughput. Will talk you out of overkill hardware as readily as underpowered hardware.",
                skills: [
                    { label: "Hardware Assessment", value: 9 },
                    { label: "GPU Planning", value: 8 },
                    { label: "Troubleshooting", value: 8 }
                ],
                domains: ["GPU/VRAM planning", "Hardware compatibility", "Performance benchmarking", "Configuration"],
                partners: ["Setup Guide", "System Coder"]
            },
            {
                id: "setup_guide",
                image: "images/team/setup_guide.png",
                name: "Setup Guide",
                role: "Onboarding & Configuration Assistant",
                personality: "Friendly and patient like an Apple Genius Bar staffer.",
                score: 99,
                tier: "free",
                avatar: "SG",
                color: "#27AE60",
                review: "The most patient agent on the team, with a knack for explaining technical configuration without being condescending. Runs at a higher temperature than the developers, which gives it the conversational flexibility to meet users where they are rather than where it thinks they should be. Walks through Ollama setup, model selection, and hardware configuration with step-by-step clarity that adapts to skill level -- won't bore an experienced developer with basics, won't lose a newcomer in jargon. Remembers that most setup problems aren't technical mysteries, they're unclear documentation. Produces guides that people actually follow to completion instead of abandoning halfway through.",
                skills: [
                    { label: "Ollama Setup", value: 9 },
                    { label: "Hardware Explanation", value: 9 },
                    { label: "User Onboarding", value: 9 }
                ],
                domains: ["Ollama configuration", "Tool setup", "User onboarding", "Troubleshooting"],
                partners: ["Hardware Agent"]
            }
        ]
    },
    {
        group: "Marketing & Content",
        agents: [
            {
                id: "marketing_agent",
                image: "images/team/marketing_agent.png",
                name: "Marketing Agent",
                role: "Digital Marketing Manager & Growth Strategist",
                personality: "Part creative, part spreadsheet. Loves attribution data.",
                score: null,
                tier: "free",
                avatar: "MKT",
                color: "#E74C3C",
                review: "Rare combination of creative instinct and data discipline -- will pitch you a campaign concept and then immediately ask how you plan to measure it. Strong across content, social, and SEO, with the kind of attribution obsession that keeps marketing spend honest. Plans campaigns with clear funnels: awareness, consideration, conversion, retention -- not just 'post more content.' Understands that SEO is a compounding investment and social media is a rented audience, and builds strategies that balance both. Won't let you launch without tracking in place. The agent that turns 'we should do some marketing' into a structured growth plan with measurable milestones.",
                skills: [
                    { label: "Content Marketing", value: 10 },
                    { label: "Social Media", value: 10 },
                    { label: "SEO", value: 10 }
                ],
                domains: ["Content marketing", "Social media strategy", "SEO/SEM", "Growth hacking", "Analytics"],
                partners: ["Content Strategy Agent", "Brand Design Agent"]
            },
            {
                id: "content_strategy_agent",
                image: "images/team/content_strategy_agent.png",
                name: "Content Strategy Agent",
                role: "Content Strategy & Repurposing Engine",
                personality: "Good enough and published beats perfect and sitting in drafts.",
                score: null,
                tier: "free",
                avatar: "CS",
                color: "#2ECC71",
                review: "Operates on the principle that published beats perfect, which is exactly the bias you want from a content engine. Generates weekly plans and multi-platform bundles with enough structure to be consistent and enough flexibility to stay relevant. Takes a single piece of content and repurposes it across blog, social, and newsletter without it feeling like the same thing copy-pasted three times. Understands platform-specific formatting: what works as a LinkedIn carousel doesn't work as a tweet thread. Keeps an editorial calendar that balances evergreen and timely content. Not the most creative writer on the team, but the most consistent -- and consistency is what builds an audience.",
                skills: [
                    { label: "Content Planning", value: 8 },
                    { label: "Blog Drafting", value: 7 },
                    { label: "Social Adaptation", value: 8 }
                ],
                domains: ["Weekly content plans", "Blog posts", "Social media", "Newsletter", "Content repurposing"],
                partners: ["Marketing Agent", "Documentation Agent"]
            },
            {
                id: "brand_design_agent",
                image: "images/team/brand_design_agent.png",
                name: "Brand Design Agent",
                role: "Brand Identity & Visual Design",
                personality: "Builds brand identities that work at favicon size and billboard scale.",
                score: null,
                tier: "pro",
                avatar: "BD",
                color: "#9B59B6",
                review: "Thinks in systems rather than individual assets -- logos, color palettes, and typography that work together at every scale from favicons to billboards. Delivers brand identities with enough constraint to stay coherent and enough range to not feel formulaic. Generates design tokens, color scales, and spacing systems that developers can actually implement without guesswork. Understands that brand consistency isn't about rigid templates -- it's about having clear enough principles that every new asset feels like it belongs without looking identical. Strong on the strategic side: positioning, voice, and visual differentiation in crowded markets. Less useful for one-off graphic design tasks.",
                skills: [
                    { label: "Logo Design", value: 7 },
                    { label: "Color Theory", value: 7 },
                    { label: "Typography", value: 7 }
                ],
                domains: ["Brand identity", "Color palettes", "Typography systems", "Design tokens", "Visual consistency"],
                partners: ["Marketing Agent", "Web Developer"]
            },
            {
                id: "campaign_orchestrator",
                image: "images/team/campaign_orchestrator.png",
                name: "Campaign Orchestrator",
                role: "Campaign Planning & Execution Manager",
                personality: "Keeps launches on schedule, not on 'next quarter.'",
                score: null,
                tier: "pro",
                avatar: "CM",
                color: "#E67E22",
                review: "Coordinates multi-channel campaigns with the same rigor the cohort orchestrator brings to code workflows. Keeps timelines, assets, and channel-specific requirements aligned so that launches actually land on schedule instead of drifting into next quarter. Manages the dependency chain that most campaign managers handle in their heads: blog post needs to publish before the social campaign references it, email sequence needs the landing page live first, press outreach needs the announcement finalized. Tracks deliverables per channel, flags blockers before they cascade, and adjusts timelines when reality diverges from the plan -- which it always does.",
                skills: [
                    { label: "Campaign Planning", value: 8 },
                    { label: "Multi-channel Coord", value: 8 },
                    { label: "Timeline Mgmt", value: 9 }
                ],
                domains: ["Campaign management", "Multi-channel marketing", "Launch coordination", "Asset management"],
                partners: ["Marketing Agent", "Content Strategy Agent"]
            }
        ]
    },
    {
        group: "Support & Communications",
        agents: [
            {
                id: "documentation_agent",
                image: "images/team/documentation_agent.png",
                name: "Documentation Agent",
                role: "Technical Documentation Specialist",
                personality: "Writes docs that people actually read.",
                score: 100,
                tier: "free",
                avatar: "DOC",
                color: "#2980B9",
                review: "Benchmarked at 100% on documentation assessments, which tracks -- this agent writes docs that people actually read. Covers everything from API references to onboarding guides with a clarity that suggests it genuinely understands what it's documenting, not just paraphrasing source code. Structures documentation around user tasks rather than code architecture, which is the difference between docs people use and docs people ignore. Writes examples that actually run, includes error scenarios alongside happy paths, and maintains a consistent voice across hundreds of pages. Treats docs-as-code seriously: version-controlled, review-gated, and tested. The perfect score isn't surprising once you see the output.",
                skills: [
                    { label: "Technical Writing", value: 10 },
                    { label: "API Documentation", value: 10 },
                    { label: "Docs-as-Code", value: 10 }
                ],
                domains: ["Technical writing", "API references", "Onboarding guides", "Docs-as-Code", "Markdown/RST"],
                partners: ["Python Developer", "Setup Guide"]
            },
            {
                id: "email_agent",
                image: "images/team/email_agent.png",
                name: "Email Agent",
                role: "Email Marketing & Campaign Specialist",
                personality: "Obsesses over subject lines and deliverability in equal measure.",
                score: null,
                tier: "pro",
                avatar: "EM",
                color: "#1ABC9C",
                review: "Handles the full email lifecycle from list segmentation to campaign analytics without requiring you to become a Mailchimp expert. Understands deliverability as a technical problem, not just a creative one, which is where most email efforts quietly fail. Knows that a beautifully written email is worthless if it lands in spam, and optimizes for inbox placement alongside open rates. Designs drip sequences with proper timing, segmentation logic that goes beyond basic demographics, and A/B test frameworks that actually produce statistically significant results. Also handles the unglamorous work: list hygiene, bounce processing, and unsubscribe compliance that keeps you off blocklists.",
                skills: [
                    { label: "Email Campaigns", value: 8 },
                    { label: "List Management", value: 8 },
                    { label: "Deliverability", value: 8 }
                ],
                domains: ["Email campaigns", "List segmentation", "Deliverability", "A/B testing", "Analytics"],
                partners: ["Marketing Agent", "Content Strategy Agent"]
            },
            {
                id: "media_production_agent",
                image: "images/team/media_production_agent.png",
                name: "Media Production Agent",
                role: "Audio/Video Production Specialist",
                personality: "Plans shoots and edits with a producer's instinct.",
                score: null,
                tier: "pro",
                avatar: "MP",
                color: "#E74C3C",
                review: "Plans and structures audio and video content with a producer's mindset -- scripts, shot lists, editing notes, and format-specific optimization. Fills a gap that most AI agent teams don't even attempt to address. Understands that video production is 80% pre-production: scripting, storyboarding, and shot planning determine quality more than post-production polish. Adapts content structure to platform requirements -- a YouTube explainer has different pacing needs than a TikTok clip or a podcast episode. Produces detailed production briefs that a human editor can execute without guesswork. Particularly useful for teams that know they need video content but don't know where to start.",
                skills: [
                    { label: "Video Planning", value: 8 },
                    { label: "Audio Production", value: 7 },
                    { label: "Script Writing", value: 8 }
                ],
                domains: ["Video production", "Audio editing", "Script writing", "Format optimization"],
                partners: ["Content Strategy Agent", "Marketing Agent"]
            },
            {
                id: "analytics_agent",
                image: "images/team/analytics_agent.png",
                name: "Analytics Agent",
                role: "Data Analysis & Reporting Specialist",
                personality: "Finds the signal in the noise, then builds a dashboard for it.",
                score: null,
                tier: "free",
                avatar: "AA",
                color: "#3498DB",
                review: "Turns raw data into dashboards and trend analysis without requiring you to specify every dimension upfront. Good at identifying the metrics that actually matter versus the ones that just look impressive in a slide deck. Asks 'what decision will this data inform?' before building anything, which prevents the common trap of dashboards that look great but don't drive action. Builds visualizations that tell a story: trend lines with context, comparisons against meaningful baselines, and anomaly highlighting that surfaces the signal without drowning it in noise. Strong at connecting marketing metrics to business outcomes, which is where most analytics agents stop at vanity numbers.",
                skills: [
                    { label: "Data Analysis", value: 8 },
                    { label: "Dashboards", value: 8 },
                    { label: "Trend Analysis", value: 8 }
                ],
                domains: ["Data analysis", "Dashboard creation", "Trend identification", "KPI tracking", "Reporting"],
                partners: ["Marketing Agent", "Campaign Orchestrator"]
            }
        ]
    },
    {
        group: "Social Media",
        agents: [
            {
                id: "linkedin",
                image: "images/team/linkedin.png",
                name: "LinkedIn Specialist",
                role: "LinkedIn Content Specialist",
                personality: "Professional without being boring. Anti-broetry.",
                score: null,
                tier: "pro",
                avatar: "LI",
                color: "#0077B5",
                review: "Understands that LinkedIn content operates by different rules than every other platform -- professional tone, algorithmic timing, and the fine line between thought leadership and cringe. Produces posts that get engagement without resorting to broetry. Knows the algorithm rewards conversation starters over link dumps, and structures content accordingly: strong hooks, substantive takes, and genuine calls to discussion rather than hollow engagement bait. Adapts tone for different content types -- company announcements need different energy than personal insights or industry commentary. Also handles the strategic side: posting cadence, hashtag relevance, and connection outreach that doesn't feel like spam.",
                skills: [
                    { label: "LinkedIn Content", value: 8 },
                    { label: "Professional Tone", value: 9 },
                    { label: "Engagement", value: 8 }
                ],
                domains: ["LinkedIn strategy", "Thought leadership", "Professional networking", "Content scheduling"],
                partners: ["Marketing Agent", "Content Strategy Agent"]
            },
            {
                id: "reddit",
                image: "images/team/reddit.png",
                name: "Reddit Specialist",
                role: "Reddit Community Manager",
                personality: "Knows that sounding like marketing gets you downvoted.",
                score: null,
                tier: "pro",
                avatar: "RD",
                color: "#FF4500",
                review: "Navigates Reddit's notoriously skeptical communities with the right balance of authenticity and strategy. Knows that the fastest way to get downvoted is to sound like marketing, and calibrates accordingly -- which is harder than it sounds for an AI agent. Studies subreddit culture before posting -- what each community values, what gets removed by mods, and what kind of self-promotion is tolerated versus rejected. Leads with value: answers questions, shares genuine insights, and builds credibility before mentioning the product. Understands that Reddit engagement is a long game measured in reputation, not a short game measured in clicks. The most culturally-aware agent on the team.",
                skills: [
                    { label: "Community Mgmt", value: 8 },
                    { label: "Authentic Voice", value: 9 },
                    { label: "Engagement", value: 8 }
                ],
                domains: ["Reddit communities", "Authentic engagement", "Content strategy", "Community building"],
                partners: ["Marketing Agent", "Content Strategy Agent"]
            }
        ]
    }
];
