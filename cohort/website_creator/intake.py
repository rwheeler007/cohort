"""Intake Module -- web scraper + adaptive worksheet engine.

Phase 1: Scrape user's current site and competitors to extract
         colors, fonts, structure, copy tone.
Phase 2: LLM-guided adaptive worksheet (20 questions).
Phase 3: Output a populated SiteBrief.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SiteAnalysis:
    """Extracted intelligence from scraping a website."""
    url: str = ""
    title: str = ""
    meta_description: str = ""
    headings: list[str] = field(default_factory=list)
    colors: list[str] = field(default_factory=list)       # Hex values
    fonts: list[str] = field(default_factory=list)
    nav_items: list[str] = field(default_factory=list)
    page_count: int = 0
    has_contact_form: bool = False
    has_pricing: bool = False
    cta_texts: list[str] = field(default_factory=list)
    testimonials_count: int = 0
    images: list[str] = field(default_factory=list)
    copy_tone: str = ""          # professional, casual, technical, etc.
    word_count: int = 0

    def to_dict(self) -> dict:
        import dataclasses
        return dataclasses.asdict(self)


# ----- Scraper -----

async def scrape_site(url: str) -> SiteAnalysis:
    """Scrape a website and extract structural intelligence.

    Uses httpx + basic HTML parsing. No Playwright needed for
    initial version -- we extract from raw HTML.
    """
    import httpx
    from html.parser import HTMLParser

    analysis = SiteAnalysis(url=url)

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=15.0,
            headers={"User-Agent": "CohortWebsiteCreator/1.0"}
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        analysis.title = f"[Scrape failed: {e}]"
        return analysis

    # Extract with regex (lightweight, no lxml dependency)
    # Title
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    if m:
        analysis.title = m.group(1).strip()

    # Meta description
    m = re.search(r'<meta\s+name=["\']description["\']\s+content=["\'](.*?)["\']',
                  html, re.IGNORECASE)
    if m:
        analysis.meta_description = m.group(1).strip()

    # Headings (h1-h3)
    for m in re.finditer(r"<h[1-3][^>]*>(.*?)</h[1-3]>", html, re.DOTALL | re.IGNORECASE):
        text = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        if text and len(text) < 200:
            analysis.headings.append(text)

    # Colors from inline styles and CSS
    for m in re.finditer(r"#([0-9a-fA-F]{3,8})\b", html):
        hex_val = "#" + m.group(1)
        if hex_val not in analysis.colors and len(m.group(1)) in (3, 6):
            analysis.colors.append(hex_val)
    analysis.colors = analysis.colors[:10]  # Top 10

    # Font families
    for m in re.finditer(r"font-family:\s*([^;\"']+)", html, re.IGNORECASE):
        font = m.group(1).strip().split(",")[0].strip().strip("'\"")
        if font and font not in analysis.fonts:
            analysis.fonts.append(font)
    analysis.fonts = analysis.fonts[:5]

    # Nav links
    nav_match = re.search(r"<nav[^>]*>(.*?)</nav>", html, re.DOTALL | re.IGNORECASE)
    if nav_match:
        for m in re.finditer(r"<a[^>]*>(.*?)</a>", nav_match.group(1), re.DOTALL):
            text = re.sub(r"<[^>]+>", "", m.group(1)).strip()
            if text and len(text) < 50:
                analysis.nav_items.append(text)

    # Detect forms
    analysis.has_contact_form = bool(re.search(r"<form", html, re.IGNORECASE))

    # Detect pricing
    analysis.has_pricing = bool(re.search(
        r"pric|/month|\$\d|per\s*month|tier|plan", html, re.IGNORECASE
    ))

    # CTA buttons
    for m in re.finditer(
        r'<(?:a|button)[^>]*class="[^"]*btn[^"]*"[^>]*>(.*?)</(?:a|button)>',
        html, re.DOTALL | re.IGNORECASE
    ):
        text = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        if text and len(text) < 60:
            analysis.cta_texts.append(text)

    # Word count (rough)
    text_content = re.sub(r"<[^>]+>", " ", html)
    text_content = re.sub(r"\s+", " ", text_content).strip()
    analysis.word_count = len(text_content.split())

    return analysis


# ----- Worksheet -----

# The 20 base questions for the adaptive worksheet.
# The LLM will adapt follow-ups based on prior answers and scraped data.
WORKSHEET_QUESTIONS = [
    # Identity (1-4)
    {"id": 1, "category": "identity", "question": "What is your product or company name?"},
    {"id": 2, "category": "identity", "question": "In one sentence, what does your product do?"},
    {"id": 3, "category": "identity", "question": "Who is your target customer? (e.g., small business owners, developers, homeowners)"},
    {"id": 4, "category": "identity", "question": "What is your primary call-to-action? (e.g., sign up, buy now, book a demo, join waitlist)"},

    # Brand (5-8)
    {"id": 5, "category": "brand", "question": "Do you have brand colors? If so, what are they? (hex codes or descriptions)"},
    {"id": 6, "category": "brand", "question": "Do you have a logo file you can upload? (path or URL)"},
    {"id": 7, "category": "brand", "question": "What tone should the website have? (e.g., professional, friendly, technical, bold, minimal)"},
    {"id": 8, "category": "brand", "question": "Are there any brands or websites whose visual style you admire?"},

    # Content (9-14)
    {"id": 9, "category": "content", "question": "What are the top 3 problems your product solves?"},
    {"id": 10, "category": "content", "question": "What are your product's 3-5 key features or benefits?"},
    {"id": 11, "category": "content", "question": "Do you have pricing tiers? If so, list them with prices and key features."},
    {"id": 12, "category": "content", "question": "Do you have any customer testimonials or quotes you'd like to include?"},
    {"id": 13, "category": "content", "question": "What pages do you need? (e.g., Home, Features, Pricing, About, Contact, Docs)"},
    {"id": 14, "category": "content", "question": "Do you have product images or screenshots to include?"},

    # Technical (15-17)
    {"id": 15, "category": "technical", "question": "Do you need a contact form? What fields should it have?"},
    {"id": 16, "category": "technical", "question": "Do you have social media links to include? (GitHub, Twitter/X, LinkedIn, etc.)"},
    {"id": 17, "category": "technical", "question": "What is your website's domain or planned URL?"},

    # Competitive (18-20)
    {"id": 18, "category": "competitive", "question": "What makes you different from your competitors?"},
    {"id": 19, "category": "competitive", "question": "Is there anything on your competitors' sites you specifically want to do better?"},
    {"id": 20, "category": "competitive", "question": "Is there anything else important about your brand or product we should know?"},
]


def get_worksheet_questions() -> list[dict]:
    """Return the base worksheet questions."""
    return WORKSHEET_QUESTIONS.copy()


def build_brief_from_worksheet(
    answers: dict[int, str],
    current_site: SiteAnalysis | None = None,
    competitors: list[SiteAnalysis] | None = None,
) -> dict:
    """Convert worksheet answers + scraped data into a raw dict
    suitable for SiteBrief.from_dict().

    This is the bridge between human input and the template system.
    The LLM roundtables can further enrich this before rendering.
    """
    brief: dict[str, Any] = {}

    # Identity
    brief["product_name"] = answers.get(1, "My Product")
    brief["tagline"] = answers.get(2, "")
    brief["description"] = answers.get(2, "")

    # Brand -- from answers or scraped current site
    brand: dict[str, str] = {}
    color_answer = answers.get(5, "")
    if color_answer:
        hex_matches = re.findall(r"#[0-9a-fA-F]{3,6}", color_answer)
        if hex_matches:
            brand["primary_color"] = hex_matches[0]
            if len(hex_matches) > 1:
                brand["secondary_color"] = hex_matches[1]
            if len(hex_matches) > 2:
                brand["accent_color"] = hex_matches[2]
    elif current_site and current_site.colors:
        brand["primary_color"] = current_site.colors[0]
        if len(current_site.colors) > 1:
            brand["secondary_color"] = current_site.colors[1]
    if brand:
        brief["brand"] = brand

    # Logo
    logo = answers.get(6, "")
    if logo:
        brief["logo"] = logo

    # Pages
    pages_answer = answers.get(13, "Home, Features, Pricing, Contact")
    page_names = [p.strip() for p in pages_answer.split(",") if p.strip()]
    page_template_map = {
        "home": "hero", "homepage": "hero", "landing": "hero",
        "features": "features", "how it works": "features",
        "pricing": "solution", "plans": "solution", "solution": "solution",
        "product": "solution",
        "problem": "problem", "why": "problem", "pain": "problem",
        "contact": "contact", "get in touch": "contact", "support": "contact",
        "about": "content", "legal": "content", "docs": "content",
        "documentation": "content", "privacy": "content", "terms": "content",
    }

    pages = []
    for name in page_names:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "page"
        if slug == "home" or slug == "homepage" or slug == "landing":
            slug = "index"
        template = page_template_map.get(name.lower(), "content")
        pages.append({
            "slug": slug,
            "title": name,
            "template": template,
            "nav_label": name,
            "in_nav": True,
            "meta_description": "",
            "content": {},
        })
    if not pages:
        pages.append({"slug": "index", "title": "Home", "template": "hero",
                       "nav_label": "Home", "in_nav": True, "content": {}})
    brief["pages"] = pages

    # Nav items from pages
    brief["nav_items"] = [
        {"label": p["nav_label"], "href": f"{p['slug']}.html"}
        for p in pages if p.get("in_nav", True)
    ]

    # Pain points
    problems = answers.get(9, "")
    if problems:
        brief["pain_points"] = [
            {"title": p.strip(), "description": "", "cost_range": ""}
            for p in problems.split("\n") if p.strip()
        ]

    # Features
    features_answer = answers.get(10, "")
    if features_answer:
        brief["features"] = [
            {"title": f.strip(), "description": "", "icon": "", "image": ""}
            for f in features_answer.split("\n") if f.strip()
        ]

    # Pricing
    pricing_answer = answers.get(11, "")
    if pricing_answer:
        # Basic parsing -- LLM roundtable will refine this
        brief["pricing_tiers"] = [
            {"name": line.strip(), "price": "", "period": "",
             "description": "", "features": [], "cta_text": "Get Started",
             "cta_href": "#", "badge": "", "highlighted": False}
            for line in pricing_answer.split("\n") if line.strip()
        ]

    # Testimonials
    testimonials = answers.get(12, "")
    if testimonials:
        brief["testimonials"] = [
            {"quote": t.strip(), "name": "", "title": "", "company": ""}
            for t in testimonials.split("\n") if t.strip()
        ]

    # Contact
    social = answers.get(16, "")
    contact_info: dict[str, Any] = {"email": "", "form_fields": [], "social_links": []}
    if social:
        for link in social.split("\n"):
            link = link.strip()
            if link:
                platform = "website"
                for p in ("github", "twitter", "linkedin", "youtube", "instagram", "x.com"):
                    if p in link.lower():
                        platform = p.replace("x.com", "twitter")
                        break
                contact_info["social_links"].append(
                    {"platform": platform, "url": link, "label": platform.title()}
                )
    brief["contact"] = contact_info

    # SEO
    domain = answers.get(17, "")
    brief["seo"] = {
        "site_title": brief["product_name"],
        "site_description": brief.get("tagline", ""),
        "keywords": [],
        "canonical_base": domain if domain.startswith("http") else "",
    }

    # Competitive context (for roundtable enrichment)
    brief["current_site_analysis"] = current_site.to_dict() if current_site else {}
    brief["competitor_analyses"] = [c.to_dict() for c in (competitors or [])]

    # Differentiation
    diff = answers.get(18, "")
    if diff:
        brief.setdefault("roundtable_decisions", {})["differentiation"] = diff

    return brief
