// Hero chat demo transcripts -- single source of truth.
// Each scenario showcases a different Cohort capability.
// Add/edit/reorder scenarios here; the animation driver picks them up automatically.

var DEMO_SCENARIOS = [
    {
        channel: "q3-planning-review",
        transcript: [
            {t:"msg",a:"Strategist",v:"S",c:"#D97757",m:"We need a launch plan covering messaging, target channels, and a realistic timeline. What do we have so far?"},
            {t:"msg",a:"Writer",v:"W",c:"#5B8DEF",m:"I'll draft the announcement post -- targeting dev blogs, Hacker News, and the Python subreddit. I have competitor positioning data ready."},
            {t:"score",m:"Writer scores highest -- new content to contribute",f:87},
            {t:"msg",a:"Analyst",v:"A",c:"#28C840",m:"Before we publish, the draft needs benchmark numbers. I have comparison data against three competitors -- zero-dep advantage is our strongest angle."},
            {t:"gate",m:"Strategist steps back -- key points already covered"},
            {t:"msg",a:"Writer",v:"W",c:"#5B8DEF",m:"Good call. I'll weave the benchmarks into the post and have a review draft by Thursday."},
            {t:"sum",title:"3 action items assigned",items:["Writer: Draft announcement with benchmarks (due Thu)","Analyst: Finalize comparison data tables","Strategist: Review messaging alignment on Friday"]}
        ]
    },
    {
        channel: "security-audit",
        transcript: [
            {t:"msg",a:"Security",v:"X",c:"#FF5F57",m:"I've flagged two endpoints accepting user input without validation. Both are in the upload handler."},
            {t:"msg",a:"Developer",v:"D",c:"#5B8DEF",m:"I can add input sanitization and file-type checking. Should take about an hour."},
            {t:"score",m:"Developer scores highest -- owns the affected code",f:92},
            {t:"msg",a:"Reviewer",v:"R",c:"#B48EAD",m:"Before we patch, let's add regression tests. We've had upload bugs slip through twice this quarter."},
            {t:"gate",m:"Security steps back -- remediation plan is solid"},
            {t:"msg",a:"Developer",v:"D",c:"#5B8DEF",m:"Tests first, then the fix. I'll open a PR by end of day."},
            {t:"sum",title:"2 issues tracked",items:["Developer: Sanitize upload handler + add tests (EOD)","Reviewer: Review PR before merge","Security: Re-scan after patch lands"]}
        ]
    },
    {
        channel: "blog-content-pipeline",
        transcript: [
            {t:"msg",a:"Editor",v:"E",c:"#D97757",m:"We have three draft posts queued. Which one should we prioritize for this week's publish?"},
            {t:"msg",a:"Analyst",v:"A",c:"#28C840",m:"The comparison piece has the highest search volume. 'Multi-agent framework comparison' gets 2,400 monthly searches."},
            {t:"score",m:"Analyst scores highest -- data-driven recommendation",f:85},
            {t:"msg",a:"Writer",v:"W",c:"#5B8DEF",m:"That one needs benchmark data updated. I can refresh the numbers and have it ready by Wednesday."},
            {t:"msg",a:"Editor",v:"E",c:"#D97757",m:"Let's publish the comparison piece Thursday, move the tutorial to next week."},
            {t:"gate",m:"Analyst steps back -- recommendation accepted"},
            {t:"sum",title:"Content calendar updated",items:["Writer: Refresh benchmark data (due Wed)","Editor: Final review Thursday morning","Analyst: Prepare social snippets with key stats"]}
        ]
    },
    {
        channel: "api-design-review",
        transcript: [
            {t:"msg",a:"Architect",v:"A",c:"#D97757",m:"The new endpoint needs pagination. Current design returns all records -- that won't scale past 10K rows."},
            {t:"msg",a:"Developer",v:"D",c:"#5B8DEF",m:"Cursor-based pagination is cleaner than offset for this use case. I'll model it after our existing /messages endpoint."},
            {t:"score",m:"Developer scores highest -- implementation expertise",f:90},
            {t:"msg",a:"Tester",v:"T",c:"#28C840",m:"We need edge case tests: empty results, single page, exact page boundary, and invalid cursor values."},
            {t:"gate",m:"Architect steps back -- design decision made"},
            {t:"msg",a:"Developer",v:"D",c:"#5B8DEF",m:"I'll implement cursor pagination with a default page size of 50. Tests will cover all four edge cases."},
            {t:"sum",title:"Design approved",items:["Developer: Implement cursor pagination (default 50)","Tester: Write edge case test suite","Architect: Review PR for consistency with existing APIs"]}
        ]
    },
    {
        channel: "onboarding-workflow",
        transcript: [
            {t:"msg",a:"Designer",v:"D",c:"#B48EAD",m:"New user drop-off is 40% at the setup wizard. The third step asks too many questions."},
            {t:"msg",a:"Analyst",v:"A",c:"#28C840",m:"Session recordings confirm it -- users hesitate at the configuration page. Average time on that step is 3x longer than others."},
            {t:"score",m:"Designer scores highest -- UX ownership",f:88},
            {t:"msg",a:"Writer",v:"W",c:"#5B8DEF",m:"The copy on step 3 is too technical. I can rewrite it to focus on outcomes instead of settings."},
            {t:"gate",m:"Analyst steps back -- data already presented"},
            {t:"msg",a:"Designer",v:"D",c:"#B48EAD",m:"Let's split step 3 into two simpler steps and use the new copy. I'll have mockups by tomorrow."},
            {t:"sum",title:"Onboarding fix planned",items:["Designer: Split step 3 + new mockups (due tomorrow)","Writer: Rewrite copy for simplified steps","Analyst: Set up A/B test to measure improvement"]}
        ]
    }
];
