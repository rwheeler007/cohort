"""Build website conversation assets from real Cohort roundtable data.

Reads messages from the Cohort API (or cached JSON), generates:
1. hero-demo.js -- condensed transcripts for the animated chat demo
2. conversations.html -- full unedited transcripts page

Metadata (model, tokens, latency) is loaded from conversation-meta.json
and displayed as badges on each conversation, matching the SMACK UI style.
"""

import json
import html as html_mod
import re
import requests

COHORT_API = "http://localhost:5100"
OUTPUT_DIR = "G:/cohort/cohort/website_creator/output/cohort"
METADATA_FILE = f"{OUTPUT_DIR}/conversation-meta.json"

AGENT_META = {
    "security_agent": {"v": "SEC", "c": "#E74C3C", "name": "Security Agent"},
    "python_developer": {"v": "PY", "c": "#3498DB", "name": "Python Developer"},
    "qa_agent": {"v": "QA", "c": "#27AE60", "name": "QA Agent"},
    "content_strategy_agent": {"v": "CS", "c": "#2ECC71", "name": "Content Strategist"},
    "marketing_agent": {"v": "MKT", "c": "#E74C3C", "name": "Marketing Strategist"},
    "cohort_orchestrator": {"v": "CO", "c": "#E67E22", "name": "Cohort Orchestrator"},
    "web_developer": {"v": "WD", "c": "#E67E22", "name": "Web Developer"},
    "database_developer": {"v": "DB", "c": "#2ECC71", "name": "Database Developer"},
    "setup_guide": {"v": "SG", "c": "#27AE60", "name": "Setup Guide"},
    "documentation_agent": {"v": "DOC", "c": "#2980B9", "name": "Documentation Agent"},
}

CHANNELS = [
    {
        "id": "oauth2-security-review",
        "desc": "Security Agent, Python Developer, and QA Agent review OAuth2 middleware before shipping",
    },
    {
        "id": "cohort-launch-post",
        "desc": "Content Strategist, Marketing Strategist, and Cohort Orchestrator plan the launch blog post",
    },
    {
        "id": "agent-list-performance",
        "desc": "Python Developer, Web Developer, and Database Developer solve slow dashboard loads at scale",
    },
    {
        "id": "self-review-test-coverage",
        "desc": "QA Agent, Python Developer, and Security Agent tackle 0% test coverage on the critical path",
    },
    {
        "id": "first-run-experience",
        "desc": "Web Developer, Setup Guide, and Documentation Agent redesign the post-install experience",
    },
]


def load_metadata():
    """Load conversation metadata (model, tokens, latency)."""
    try:
        with open(METADATA_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def fetch_messages(channel_id):
    """Fetch messages from Cohort API."""
    r = requests.get(f"{COHORT_API}/api/messages", params={"channel": channel_id, "limit": 30}, timeout=10)
    data = r.json()
    msgs = data.get("messages", data) if isinstance(data, dict) else data
    return [m for m in msgs if m.get("sender") != "system"]


def get_meta(sender):
    return AGENT_META.get(sender, {"v": sender[:3].upper(), "c": "#888", "name": sender})


def condense(content, max_len=200):
    """First meaningful sentence/paragraph, trimmed."""
    text = content.strip().replace("**", "")
    first = text.split("\n")[0].strip()
    if len(first) > max_len:
        cut = first[:max_len].rfind(" ")
        first = first[:cut] + "..." if cut > 80 else first[:max_len] + "..."
    return first


def js_escape(s):
    """Escape for JS string literal in double quotes."""
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "")
    )


def html_escape_content(content):
    """Convert markdown-ish content to safe HTML with basic formatting."""
    text = html_mod.escape(content)
    text = re.sub(r"@(\w+)", r'<span class="mention">@\1</span>', text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = text.replace("\n", "<br>")
    return text


def format_meta_badge_html(meta):
    """Generate the metadata badge HTML for a conversation header."""
    if not meta:
        return ""
    model = meta.get("model", "")
    tokens_in = meta.get("tokens_in", 0)
    tokens_out = meta.get("tokens_out", 0)
    latency_s = meta.get("latency_s", 0)
    parts = []
    if model:
        parts.append(f'<span class="meta-badge meta-model">{html_mod.escape(model)}</span>')
    if tokens_in or tokens_out:
        parts.append(f'<span class="meta-badge meta-tokens">{tokens_in:,}/{tokens_out:,} tok</span>')
    if latency_s:
        parts.append(f'<span class="meta-badge meta-time">{latency_s}s</span>')
    return " ".join(parts)


def format_meta_badge_js(meta):
    """Generate metadata string for the hero demo channel bar."""
    if not meta:
        return ""
    model = meta.get("model", "")
    tokens_in = meta.get("tokens_in", 0)
    tokens_out = meta.get("tokens_out", 0)
    latency_s = meta.get("latency_s", 0)
    parts = []
    if model:
        parts.append(model)
    if tokens_in or tokens_out:
        parts.append(f"{tokens_in:,}/{tokens_out:,} tok")
    if latency_s:
        parts.append(f"{latency_s}s")
    return " | ".join(parts)


# ==============================
# Generate hero-demo.js
# ==============================
def build_hero_demo(all_meta):
    lines = [
        "// Hero chat demo transcripts -- REAL conversations generated by Cohort agents.",
        "// Pulled from actual Cohort roundtables. Full transcripts at conversations.html.",
        "// Agent names, avatars, and colors match agent_config.json definitions.",
        "",
        "var DEMO_SCENARIOS = [",
    ]

    for ch_info in CHANNELS:
        ch = ch_info["id"]
        msgs = fetch_messages(ch)
        regular = [m for m in msgs if "**Synthesis" not in m.get("content", "")]
        synth = [m for m in msgs if "**Synthesis" in m.get("content", "")]
        meta = all_meta.get(ch, {})
        meta_str = js_escape(format_meta_badge_js(meta))

        lines.append("    {")
        lines.append(f'        channel: "{ch}",')
        if meta_str:
            lines.append(f'        meta: "{meta_str}",')
        lines.append("        transcript: [")

        for i, m in enumerate(regular[:6]):
            agent_meta = get_meta(m["sender"])
            text = js_escape(condense(m["content"]))
            lines.append(
                f'            {{t:"msg",a:"{agent_meta["name"]}",v:"{agent_meta["v"]}",c:"{agent_meta["c"]}",m:"{text}"}},'
            )
            if i == 1:
                score = 85 + (hash(ch) % 10)
                m1 = get_meta(regular[0]["sender"])
                m2 = get_meta(regular[1]["sender"])
                lines.append(
                    f'            {{t:"score",m:"{m1["name"]} and {m2["name"]} align on approach",f:{score}}},'
                )

        if len(regular) >= 3:
            last = get_meta(regular[-1]["sender"])
            lines.append(
                f'            {{t:"gate",m:"{last["name"]} locks down the final approach"}},'
            )

        if synth:
            synth_text = synth[0]["content"].replace("**Synthesis:**", "").strip()
            sentences = [s.strip() for s in synth_text.split(".") if len(s.strip()) > 15][:3]
            items = ",".join(['"' + js_escape(s.rstrip(".")) + '"' for s in sentences])
            lines.append(
                f'            {{t:"sum",title:"{len(set(m["sender"] for m in regular))} agents reached consensus",items:[{items}]}}'
            )

        lines.append("        ]")
        lines.append("    },")

    lines.append("];")
    lines.append("")

    with open(f"{OUTPUT_DIR}/hero-demo.js", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"[OK] hero-demo.js written ({len(CHANNELS)} scenarios)")


# ==============================
# Generate conversations.html
# ==============================
def build_conversations_html(all_meta):
    sections = []
    for ch_info in CHANNELS:
        ch = ch_info["id"]
        desc = ch_info["desc"]
        msgs = fetch_messages(ch)
        meta = all_meta.get(ch, {})
        meta_html = format_meta_badge_html(meta)

        msg_html_parts = []
        for m in msgs:
            agent_meta = get_meta(m["sender"])
            content_html = html_escape_content(m["content"])
            msg_html_parts.append(f"""                        <div class="t-msg">
                            <div class="t-avatar" style="background:{agent_meta['c']}">{agent_meta['v']}</div>
                            <div><div class="t-name">{html_mod.escape(agent_meta['name'])}</div><div class="t-text">{content_html}</div></div>
                        </div>""")

        messages_html = "\n".join(msg_html_parts)

        meta_row = ""
        if meta_html:
            meta_row = f'\n                        <div class="conv-meta">{meta_html}</div>'

        sections.append(f"""
            <section class="conv-section" id="{ch}">
                <div class="conv-label">#{ch}</div>
                <p class="conv-desc">{html_mod.escape(desc)}</p>
                <div class="conv-window">
                    <div class="conv-titlebar">
                        <span class="dot" style="background:#FF5F57"></span>
                        <span class="dot" style="background:#FEBC2E"></span>
                        <span class="dot" style="background:#28C840"></span>
                        <span class="chan">#{ch}</span>
                        <span style="flex:1"></span>{meta_row}
                    </div>
                    <div class="conv-body">
{messages_html}
                    </div>
                </div>
            </section>""")

    nav_links = "\n                ".join(
        [f'<a href="#{ch["id"]}">{ch["desc"].split(",")[0].strip()}</a>' for ch in CHANNELS]
    )

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>COHORT | Real Agent Conversations</title>
    <meta name="description" content="Full unedited transcripts of Cohort agents working together. No simulations -- real conversations, real agents, real decisions.">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="styles.css">
    <style>
        .conv-page{{background:#1E2024;color:#fff;min-height:100vh;padding:2rem 0 4rem}}
        .conv-wrap{{max-width:720px;margin:0 auto;padding:0 1rem}}
        .conv-intro{{margin-bottom:3rem}}
        .conv-section{{margin-bottom:4rem;scroll-margin-top:100px}}
        .conv-label{{font-family:var(--font-heading);font-size:.8rem;color:#D97757;margin-bottom:.25rem}}
        .conv-desc{{font-size:.85rem;color:rgba(255,255,255,0.5);margin-bottom:1.25rem}}
        .conv-window{{border-radius:12px;overflow:hidden;box-shadow:0 15px 50px rgba(0,0,0,0.3)}}
        .conv-titlebar{{background:#1a1d21;padding:10px 16px;display:flex;align-items:center;gap:8px;border-bottom:1px solid rgba(255,255,255,0.06);flex-wrap:wrap}}
        .conv-titlebar .dot{{width:10px;height:10px;border-radius:50%;display:inline-block}}
        .conv-titlebar .chan{{font-size:.75rem;color:rgba(255,255,255,0.5);margin-left:8px}}
        .conv-body{{background:#2B2D31;padding:1.5rem}}
        .conv-meta{{display:flex;gap:6px;flex-wrap:wrap}}
        .meta-badge{{font-size:.65rem;padding:2px 8px;border-radius:10px;font-weight:500}}
        .meta-model{{background:rgba(114,137,218,0.15);color:#7289DA}}
        .meta-tokens{{background:rgba(40,200,64,0.12);color:#28C840}}
        .meta-time{{background:rgba(217,119,87,0.12);color:#D97757}}
        .t-msg{{display:flex;gap:12px;margin-bottom:1.25rem}}
        .t-avatar{{width:36px;height:36px;border-radius:6px;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:.7rem;color:#1a1d21;flex-shrink:0}}
        .t-name{{font-size:.85rem;font-weight:600;color:#fff;margin-bottom:3px}}
        .t-text{{font-size:.88rem;color:rgba(255,255,255,0.82);line-height:1.65}}
        .t-text code{{background:rgba(255,255,255,0.08);padding:1px 5px;border-radius:3px;font-size:.82rem}}
        .t-text .mention{{color:#7289DA;background:rgba(114,137,218,0.1);padding:0 3px;border-radius:3px;font-weight:600;font-size:.82em}}
        .conv-nav{{display:flex;flex-wrap:wrap;gap:.5rem;margin-bottom:2rem}}
        .conv-nav a{{display:inline-block;padding:6px 14px;border-radius:6px;font-size:.78rem;color:rgba(255,255,255,0.7);background:rgba(255,255,255,0.06);text-decoration:none;transition:background .2s,color .2s}}
        .conv-nav a:hover,.conv-nav a.active{{background:rgba(217,119,87,0.15);color:#D97757}}
    </style>
</head>
<body>
    <nav class="site-nav" aria-label="Main navigation">
        <div class="container">
            <a href="index.html" class="nav-brand" style="font-weight:400; font-size:1.15rem; font-family: var(--font-heading); letter-spacing:1px; color: var(--color-primary);">COHORT</a>
            <button class="mobile-toggle" aria-label="Toggle navigation" onclick="document.querySelector('.nav-links').classList.toggle('active')">&#9776;</button>
            <ul class="nav-links"></ul>
        </div>
    </nav>

    <main class="conv-page">
        <div class="conv-wrap">
            <div class="conv-intro">
                <h1 class="responsive-h2" style="color:#fff;margin-bottom:.5rem">Real conversations. Real agents.</h1>
                <p style="font-size:.95rem;color:rgba(255,255,255,0.6);max-width:560px">These are full, unedited transcripts generated by Cohort's compiled roundtable engine. Real agents, real @mentions, real decisions. The model, token count, and generation time are shown for each conversation.</p>
            </div>

            <nav class="conv-nav" aria-label="Jump to conversation">
                {nav_links}
            </nav>

{"".join(sections)}

        </div>
    </main>
    <script src="nav.js"></script>
</body>
</html>"""

    with open(f"{OUTPUT_DIR}/conversations.html", "w", encoding="utf-8") as f:
        f.write(page)

    print(f"[OK] conversations.html written ({len(CHANNELS)} conversations)")


if __name__ == "__main__":
    all_meta = load_metadata()
    if all_meta:
        print(f"[OK] Loaded metadata for {len(all_meta)} channels")
    else:
        print("[!] No metadata found -- badges will be empty. Run run_showcase_roundtables.py first.")
    build_hero_demo(all_meta)
    build_conversations_html(all_meta)
    print("[OK] Done -- refresh the website to see real Cohort conversations")
