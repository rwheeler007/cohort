"""Decision Engine -- Neural flowchart for website generation.

Tier 1 (1B model): atomic yes/no/idk classification at temp 0
Tier 2 (9B model): creative generation at adaptive temperature

All branching logic lives in Python. Models are classifiers, not reasoners.

Usage:
    engine = DecisionEngine()
    result = engine.run("Power Plumbing Co. is a Portland plumber since 1979...")
    # result is a BlockSiteSpec ready for BlockRenderer.render()
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx
import yaml

from .block_populator import (
    BlockPopulator,
    BlockSiteSpec,
    BusinessInfo,
    CompetitorProfile,
    PopulatorInput,
    TasteProfile,
)

# Backward-compatible aliases used throughout this module
RoundtableInput = PopulatorInput
RoundtableBridge = BlockPopulator

log = logging.getLogger(__name__)

DECISION_ENGINE_YAML = Path(__file__).parent / "decision_engine.yaml"
TAXONOMY_PATH = Path(__file__).parent / "taxonomy.yaml"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class EngineConfig:
    """Runtime config for the decision engine."""
    ollama_url: str = "http://localhost:11434"
    tier1_model: str = "qwen3.5:2b"
    tier2_model: str = "qwen3.5:9b"
    tier1_timeout: float = 30.0   # seconds per call
    tier2_timeout: float = 180.0  # seconds per call (allows for cold start)
    dry_run: bool = False         # skip LLM calls, use defaults
    verbose: bool = False


# ---------------------------------------------------------------------------
# LLM call primitives
# ---------------------------------------------------------------------------

@dataclass
class Tier1Result:
    """Result of a tier 1 yes/no/idk classification."""
    answer: Literal["yes", "no", "idk"]
    question_id: str = ""
    latency_ms: int = 0
    raw: str = ""


@dataclass
class Tier2Result:
    """Result of a tier 2 generation call."""
    text: str = ""
    latency_ms: int = 0
    error: str = ""


def _call_tier1(prompt: str, config: EngineConfig,
                question_id: str = "") -> Tier1Result:
    """Call tier 1 model for yes/no/idk classification.

    Returns one of: yes, no, idk. Never raises -- returns idk on error.
    """
    if config.dry_run:
        return Tier1Result(answer="idk", question_id=question_id)

    system = "Answer with one word: yes or no."

    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=config.tier1_timeout) as client:
            resp = client.post(
                f"{config.ollama_url}/api/generate",
                json={
                    "model": config.tier1_model,
                    "prompt": prompt,
                    "system": system,
                    "stream": False,
                    "options": {
                        "temperature": 0.0,
                        "num_predict": 5,
                        "top_k": 1,
                    },
                    "keep_alive": "0",
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        log.warning("Tier 1 call failed for %s: %s", question_id, e)
        return Tier1Result(answer="idk", question_id=question_id,
                           latency_ms=int((time.monotonic() - t0) * 1000))

    latency = int((time.monotonic() - t0) * 1000)
    raw = data.get("response", "").strip().lower()

    # Parse: extract first word that matches yes/no/idk
    cleaned = raw.strip(".,!?\"' \n\t").lower()
    for token in cleaned.split():
        token = token.strip(".,!?\"'")
        if token in ("yes", "no", "idk"):
            return Tier1Result(answer=token, question_id=question_id,
                               latency_ms=latency, raw=raw)

    if config.verbose:
        log.info("Tier 1 unparseable for %s: %r", question_id, raw)
    return Tier1Result(answer="idk", question_id=question_id,
                       latency_ms=latency, raw=raw)


def _call_tier2(prompt: str, config: EngineConfig,
                temperature: float = 0.3,
                system: str | None = None,
                max_tokens: int = 2000) -> Tier2Result:
    """Call tier 2 model for creative generation.

    Returns generated text. Never raises -- returns empty on error.
    """
    if config.dry_run:
        return Tier2Result(text="", error="dry_run")

    sys_prompt = system or "You are a professional website copywriter."

    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=config.tier2_timeout) as client:
            resp = client.post(
                f"{config.ollama_url}/api/generate",
                json={
                    "model": config.tier2_model,
                    "prompt": prompt,
                    "system": sys_prompt,
                    "stream": False,
                    "think": False,  # Skip reasoning tokens, output only
                    "options": {
                        "temperature": temperature,
                        "num_predict": max_tokens,
                    },
                    "keep_alive": "0",
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        log.warning("Tier 2 call failed: %s", e)
        return Tier2Result(error=str(e),
                           latency_ms=int((time.monotonic() - t0) * 1000))

    latency = int((time.monotonic() - t0) * 1000)
    text = data.get("response", "").strip()
    return Tier2Result(text=text, latency_ms=latency)


# ---------------------------------------------------------------------------
# Phase 1: Category Classification
# ---------------------------------------------------------------------------

CATEGORY_QUESTIONS = [
    {
        "id": "is_food_drink",
        "question": "Is this a restaurant, cafe, bar, or food service?",
        "yes_category": "restaurant",
    },
    {
        "id": "is_software",
        "question": "Is this a software company, app, or digital platform?",
        "yes_category": "saas_product",
    },
    {
        "id": "is_service",
        "question": "Is this a service business that sells expertise or skilled labor?",
        "yes_category": "service_business",
    },
]


def classify_category(description: str,
                      config: EngineConfig) -> str:
    """Classify a business into a category using tier 1 questions.

    Returns: "service_business", "saas_product", or "restaurant"
    """
    for q in CATEGORY_QUESTIONS:
        prompt = (
            f"Business: {description}\n\n"
            f"{q['question']} Answer yes or no."
        )
        result = _call_tier1(prompt, config, question_id=q["id"])

        if config.verbose:
            log.info("Phase 1 [%s]: %s (%dms)", q["id"], result.answer, result.latency_ms)

        if result.answer == "yes":
            return q["yes_category"]

    # All returned no/idk -- escalate to tier 2
    log.info("Phase 1: tier 1 inconclusive, escalating to tier 2")
    t2 = _call_tier2(
        f"Given this business description, which category fits best? "
        f"Reply with EXACTLY one of: service_business, saas_product, restaurant\n\n"
        f"Description: {description}",
        config,
        temperature=0.0,
        system="You are a business classifier. Reply with exactly one category name.",
        max_tokens=20,
    )

    text = t2.text.strip().lower()
    for cat in ("service_business", "saas_product", "restaurant"):
        if cat in text:
            return cat

    # Final fallback
    log.warning("Phase 1: could not classify, defaulting to service_business")
    return "service_business"


# ---------------------------------------------------------------------------
# Phase 2: Taste Profiling from site analysis
# ---------------------------------------------------------------------------

STRUCTURE_QUESTIONS = [
    ("has_hero", "Does this website have a large hero section at the top?"),
    ("has_pricing", "Does this website display pricing information?"),
    ("has_testimonials", "Does this website show customer reviews or testimonials?"),
    ("has_booking", "Does this website have a booking or reservation feature?"),
    ("has_blog", "Does this website have a blog or articles section?"),
    ("has_faq", "Does this website have a FAQ section?"),
    ("has_gallery", "Does this website have a photo gallery?"),
    ("has_menu", "Does this website display a food or drink menu with prices?"),
    ("has_team", "Does this website show team member profiles?"),
    ("has_contact_form", "Does this website have a contact form?"),
]

TASTE_QUESTIONS = [
    ("formal", "formality", 0.3, "Does this website feel formal and corporate rather than casual?"),
    ("dense", "density", 0.3, "Does this website have a lot of content with minimal whitespace?"),
    ("warm_colors", "warmth", 0.3, "Does this website primarily use warm colors like reds, oranges, earth tones?"),
    ("warm_imagery", "warmth", 0.2, "Does this website use natural photography rather than illustrations?"),
    ("creative_layout", "creativity", 0.3, "Does this website use an unusual or asymmetric layout?"),
    ("creative_type", "creativity", 0.2, "Does this website use decorative fonts rather than standard sans-serif?"),
    ("bold_colors", "boldness", 0.3, "Does this website use high-contrast, saturated colors?"),
    ("bold_cta", "boldness", 0.2, "Does this website have large, prominent call-to-action buttons?"),
]


def analyze_site_html(html: str, config: EngineConfig,
                      site_label: str = "site") -> CompetitorProfile:
    """Analyze scraped HTML to build a taste profile and block inventory.

    Uses tier 1 questions against the HTML content.
    """
    # Trim HTML to a reasonable context window for 1B model
    html_excerpt = html[:3000] if len(html) > 3000 else html

    # Structure questions -> blocks_present
    blocks_present = []
    for qid, question in STRUCTURE_QUESTIONS:
        prompt = f"Website HTML excerpt:\n{html_excerpt}\n\nQuestion: {question}"
        result = _call_tier1(prompt, config, question_id=f"{site_label}_{qid}")
        if result.answer == "yes":
            blocks_present.append(qid)
        if config.verbose:
            log.info("Site analysis [%s_%s]: %s (%dms)",
                     site_label, qid, result.answer, result.latency_ms)

    # Taste questions -> profile dimensions
    dimensions: dict[str, float] = {
        "formality": 0.5, "density": 0.5, "warmth": 0.5,
        "creativity": 0.3, "boldness": 0.5,
    }

    for qid, dimension, weight, question in TASTE_QUESTIONS:
        prompt = f"Website HTML excerpt:\n{html_excerpt}\n\nQuestion: {question}"
        result = _call_tier1(prompt, config, question_id=f"{site_label}_{qid}")
        if result.answer == "yes":
            dimensions[dimension] = min(1.0, dimensions[dimension] + weight)
        elif result.answer == "no":
            dimensions[dimension] = max(0.0, dimensions[dimension] - weight * 0.5)
        if config.verbose:
            log.info("Taste [%s_%s]: %s -> %s=%.2f (%dms)",
                     site_label, qid, result.answer, dimension,
                     dimensions[dimension], result.latency_ms)

    return CompetitorProfile(
        taste_profile=TasteProfile(**dimensions),
        blocks_present=blocks_present,
    )


def merge_taste_profiles(category_bias: dict[str, float],
                         competitors: list[CompetitorProfile],
                         user_site: CompetitorProfile | None = None) -> TasteProfile:
    """Merge category defaults + competitor analysis into a starting taste profile.

    Priority: category bias as base, shifted by competitor industry norm.
    User site analysis is used to calculate the delta (for display), not to
    set the target profile.
    """
    # Start from category bias
    dims = dict(category_bias)

    # Blend with competitor average (if we have competitors)
    if competitors:
        for dim in ("formality", "density", "warmth", "creativity", "boldness"):
            comp_avg = sum(
                getattr(c.taste_profile, dim) for c in competitors
            ) / len(competitors)
            # 70% category bias, 30% competitor signal
            dims[dim] = dims.get(dim, 0.5) * 0.7 + comp_avg * 0.3

    # Clamp
    for k in dims:
        dims[k] = max(0.0, min(1.0, dims[k]))

    return TasteProfile(**dims)


# ---------------------------------------------------------------------------
# Phase 2B: Competitive intelligence (derived, no LLM)
# ---------------------------------------------------------------------------

@dataclass
class CompetitiveIntel:
    """Insights derived from competitor analysis."""
    industry_norm: TasteProfile | None = None
    blocks_all_have: list[str] = field(default_factory=list)
    blocks_none_have: list[str] = field(default_factory=list)
    differentiation_opportunities: list[str] = field(default_factory=list)


def derive_competitive_intel(
    competitors: list[CompetitorProfile],
    category_blocks: list[str],
) -> CompetitiveIntel:
    """Derive competitive intelligence from analyzed competitors."""
    if not competitors:
        return CompetitiveIntel()

    # Industry norm (average taste)
    dims = {}
    for dim in ("formality", "density", "warmth", "creativity", "boldness"):
        dims[dim] = sum(getattr(c.taste_profile, dim) for c in competitors) / len(competitors)
    industry_norm = TasteProfile(**dims)

    # Block analysis
    all_comp_blocks = [set(c.blocks_present) for c in competitors]
    if all_comp_blocks:
        blocks_all = set.intersection(*all_comp_blocks) if len(all_comp_blocks) > 1 else all_comp_blocks[0]
        blocks_any = set.union(*all_comp_blocks)
    else:
        blocks_all = set()
        blocks_any = set()

    # Map structure question IDs to block types
    struct_to_block = {
        "has_hero": "hero_banner",
        "has_pricing": "pricing_tiers",
        "has_testimonials": "testimonials",
        "has_booking": "booking_widget",
        "has_blog": "blog_previews",
        "has_faq": "faq_accordion",
        "has_gallery": "photo_gallery",
        "has_menu": "menu_display",
        "has_team": "content_sections",
        "has_contact_form": "contact_form",
    }

    blocks_none_have = []
    for block in category_blocks:
        # Check if any competitor has this block type
        block_ids = [k for k, v in struct_to_block.items() if v == block]
        if block_ids and not any(bid in blocks_any for bid in block_ids):
            blocks_none_have.append(block)

    return CompetitiveIntel(
        industry_norm=industry_norm,
        blocks_all_have=[struct_to_block.get(b, b) for b in blocks_all],
        blocks_none_have=blocks_none_have,
        differentiation_opportunities=blocks_none_have,
    )


# ---------------------------------------------------------------------------
# Phase 3: Content Generation (tier 2)
# ---------------------------------------------------------------------------

def generate_content(business: BusinessInfo,
                     category: str,
                     taste: TasteProfile,
                     config: EngineConfig) -> dict[str, Any]:
    """Generate all text content for the site using tier 2.

    Returns a content dict that feeds into RoundtableBridge.build().
    """
    params = taste.adapted_params()
    temp = params.content_temperature

    # Tone description for the copywriter
    tone_parts = []
    if taste.formality > 0.6:
        tone_parts.append("professional and authoritative")
    elif taste.formality < 0.4:
        tone_parts.append("casual and friendly")
    else:
        tone_parts.append("approachable yet professional")

    if taste.warmth > 0.6:
        tone_parts.append("warm and personal")
    if taste.boldness > 0.6:
        tone_parts.append("bold and confident")
    if taste.creativity > 0.5:
        tone_parts.append("creative and distinctive")

    tone_desc = ", ".join(tone_parts) if tone_parts else "professional and clear"

    # Load taxonomy for category info
    with open(TAXONOMY_PATH, "r", encoding="utf-8") as f:
        taxonomy = yaml.safe_load(f)
    cat_def = taxonomy["categories"][category]
    pages = cat_def["default_pages"]

    prompt = f"""Write website copy for a business. Output YAML only, no explanation.

Business: {business.name}
Description: {business.description}
Tagline: {business.tagline or "(generate one)"}
Phone: {business.phone}
Email: {business.email}
Address: {business.address}
Category: {category}
Pages: {', '.join(pages)}
Tone: {tone_desc}
Primary action: {business.primary_action}

Output this exact YAML structure:
```yaml
tagline: "short tagline for the business"
hero_headline: "compelling headline for homepage hero"
hero_subheadline: "2-3 sentence supporting text for homepage"
hero_services:
  hero_headline: "headline for services page hero"
  hero_subheadline: "supporting text for services page"
hero_about:
  hero_headline: "headline for about page hero"
  hero_subheadline: "supporting text for about page"
hero_contact:
  hero_headline: "headline for contact page hero"
  hero_subheadline: "supporting text for contact page"
cta_primary_text: "action button text"
cta_secondary_text: "secondary button text"
cta_heading: "CTA strip heading"
cta_subheading: "CTA strip supporting text"
section_headings:
  services_grid: "heading for services section"
  testimonials: "heading for testimonials section"
  stats_banner: "heading for stats/numbers section"
  process_steps: "heading for how-it-works section"
  credentials_strip: "heading for credentials section"
  content_sections: "heading for about content section"
meta_descriptions:
  home: "meta description for home page"
  services: "meta description for services page"
  about: "meta description for about page"
  contact: "meta description for contact page"
page_titles:
  home: "page title for home"
  services: "page title for services"
  about: "page title for about"
  contact: "page title for contact"
```"""

    system = (
        "You are a professional website copywriter. "
        "Output ONLY valid YAML inside a code block. No commentary."
    )

    result = _call_tier2(prompt, config, temperature=temp, system=system,
                         max_tokens=1500)

    if result.error:
        log.warning("Content generation failed: %s", result.error)
        return _fallback_content(business, category, pages)

    # Parse YAML from response
    content = _parse_yaml_response(result.text)
    if not content:
        log.warning("Content YAML parse failed, retrying with correction...")
        # One retry with correction prompt
        retry = _call_tier2(
            f"The previous output was not valid YAML. Please output ONLY "
            f"valid YAML with no markdown formatting, no explanation. "
            f"Just the key: value pairs.\n\nOriginal request:\n{prompt}",
            config, temperature=temp, system=system, max_tokens=1500,
        )
        content = _parse_yaml_response(retry.text)

    if not content:
        log.warning("Content generation failed after retry, using fallback")
        return _fallback_content(business, category, pages)

    # Ensure required fields have values
    content.setdefault("hero_headline", business.name)
    content.setdefault("hero_subheadline", business.description)
    content.setdefault("tagline", business.tagline)
    content.setdefault("cta_primary_text", "Get Started")
    content.setdefault("section_headings", {})
    content.setdefault("meta_descriptions", {})
    content.setdefault("page_titles", {})

    # Map page slugs: taxonomy uses "home" but renderer uses "index"
    for mapping_key in ("meta_descriptions", "page_titles"):
        mapping = content.get(mapping_key, {})
        if "home" in mapping and "index" not in mapping:
            mapping["index"] = mapping.pop("home")

    log.info("Content generated: %d fields, %dms",
             len(content), result.latency_ms)

    # Second call: block-specific list content (services, testimonials, etc.)
    block_content = generate_block_content(business, category, taste, config)
    if block_content:
        # Merge -- don't overwrite keys from first call
        for k, v in block_content.items():
            if k not in content:
                content[k] = v

    return content


def _parse_yaml_response(text: str) -> dict | None:
    """Extract YAML from LLM response, handling code blocks."""
    # Try to extract from ```yaml ... ``` block
    match = re.search(r"```(?:yaml)?\s*\n(.*?)```", text, re.DOTALL)
    if match:
        text = match.group(1)

    # Strip any remaining markdown
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]

    try:
        result = yaml.safe_load(text)
        if isinstance(result, dict):
            return result
    except yaml.YAMLError:
        pass

    return None


def _fallback_content(business: BusinessInfo, category: str,
                      pages: list[str]) -> dict[str, Any]:
    """Sensible defaults when LLM content generation fails."""
    name = business.name
    desc = business.description or f"Welcome to {name}"

    action_ctas = {
        "call": "Call Now",
        "book": "Book Now",
        "buy": "Get Started",
        "learn": "Learn More",
        "order": "Order Now",
    }

    content = {
        "tagline": business.tagline or name,
        "hero_headline": name,
        "hero_subheadline": desc,
        "cta_primary_text": action_ctas.get(business.primary_action, "Get Started"),
        "cta_secondary_text": "Learn More",
        "cta_heading": f"Ready to work with {name}?",
        "cta_subheading": desc,
        "section_headings": {},
        "meta_descriptions": {},
        "page_titles": {},
    }

    for page in pages:
        slug = "index" if page == "home" else page
        content["page_titles"][slug] = f"{name} - {page.replace('_', ' ').title()}"
        content["meta_descriptions"][slug] = desc

    return content


# ---------------------------------------------------------------------------
# Phase 3A: Block-specific content generation (tier 2)
# ---------------------------------------------------------------------------

_BLOCK_CONTENT_PROMPTS = {
    "service_business": """Generate content for a service business website. Output YAML only.

Business: {name}
Description: {description}

```yaml
services:
  - name: "service name"
    description: "1-2 sentence description"
  - name: "service name"
    description: "1-2 sentence description"
  - name: "service name"
    description: "1-2 sentence description"
testimonials:
  - quote: "realistic customer testimonial quote"
    name: "Customer Name"
    title: "Role or Location"
  - quote: "realistic customer testimonial quote"
    name: "Customer Name"
    title: "Role or Location"
  - quote: "realistic customer testimonial quote"
    name: "Customer Name"
    title: "Role or Location"
stats:
  - value: "number or short text"
    label: "what this stat measures"
  - value: "number or short text"
    label: "what this stat measures"
  - value: "number or short text"
    label: "what this stat measures"
  - value: "number or short text"
    label: "what this stat measures"
steps:
  - number: 1
    title: "step name"
    description: "1 sentence"
  - number: 2
    title: "step name"
    description: "1 sentence"
  - number: 3
    title: "step name"
    description: "1 sentence"
sections:
  - heading: "Our Story"
    body: "2-3 sentences about the company history and mission"
  - heading: "Why Choose Us"
    body: "2-3 sentences about competitive advantages"
faqs:
  - question: "common question"
    answer: "clear answer"
  - question: "common question"
    answer: "clear answer"
  - question: "common question"
    answer: "clear answer"
```""",

    "saas_product": """Generate content for a SaaS product website. Output YAML only.

Business: {name}
Description: {description}

```yaml
features:
  - title: "feature name"
    description: "1-2 sentence benefit-focused description"
  - title: "feature name"
    description: "1-2 sentence benefit-focused description"
  - title: "feature name"
    description: "1-2 sentence benefit-focused description"
  - title: "feature name"
    description: "1-2 sentence benefit-focused description"
  - title: "feature name"
    description: "1-2 sentence benefit-focused description"
  - title: "feature name"
    description: "1-2 sentence benefit-focused description"
testimonials:
  - quote: "realistic user testimonial"
    name: "User Name"
    title: "Role at Company"
  - quote: "realistic user testimonial"
    name: "User Name"
    title: "Role at Company"
  - quote: "realistic user testimonial"
    name: "User Name"
    title: "Role at Company"
stats:
  - value: "number"
    label: "metric"
  - value: "number"
    label: "metric"
  - value: "number"
    label: "metric"
  - value: "number"
    label: "metric"
tiers:
  - name: "Free"
    price: "$0"
    period: "/month"
    description: "For individuals getting started"
    features: ["feature 1", "feature 2", "feature 3"]
    cta_text: "Start Free"
    highlighted: false
  - name: "Pro"
    price: "$29"
    period: "/month"
    description: "For growing teams"
    features: ["Everything in Free", "feature 4", "feature 5", "feature 6"]
    cta_text: "Start Free Trial"
    highlighted: true
    badge: "POPULAR"
  - name: "Enterprise"
    price: "Custom"
    period: ""
    description: "For large organizations"
    features: ["Everything in Pro", "feature 7", "feature 8", "Dedicated support"]
    cta_text: "Contact Sales"
    highlighted: false
sections:
  - heading: "Our Mission"
    body: "2-3 sentences about the product mission"
  - heading: "Why Teams Choose Us"
    body: "2-3 sentences about competitive advantages"
faqs:
  - question: "common product question"
    answer: "clear answer"
  - question: "common product question"
    answer: "clear answer"
  - question: "common product question"
    answer: "clear answer"
```""",

    "restaurant": """Generate content for a restaurant website. Output YAML only.

Business: {name}
Description: {description}

```yaml
testimonials:
  - quote: "realistic diner review"
    name: "Guest Name"
    title: "Regular Guest"
  - quote: "realistic diner review"
    name: "Guest Name"
    title: "Food Critic"
  - quote: "realistic diner review"
    name: "Guest Name"
    title: "Local Resident"
stats:
  - value: "number or text"
    label: "metric"
  - value: "number or text"
    label: "metric"
  - value: "number or text"
    label: "metric"
  - value: "number or text"
    label: "metric"
sections:
  - heading: "Our Story"
    body: "2-3 sentences about the restaurant's history and philosophy"
  - heading: "Our Kitchen"
    body: "2-3 sentences about ingredients, sourcing, and cooking approach"
faqs:
  - question: "common restaurant question"
    answer: "clear answer"
  - question: "common restaurant question"
    answer: "clear answer"
  - question: "common restaurant question"
    answer: "clear answer"
```""",
}


def generate_block_content(business: BusinessInfo,
                           category: str,
                           taste: TasteProfile,
                           config: EngineConfig) -> dict[str, Any] | None:
    """Generate block-specific list content (services, testimonials, etc.).

    Separate from the headline/meta generation to keep prompts focused.
    Returns dict with keys like 'services', 'testimonials', 'stats', etc.
    """
    prompt_template = _BLOCK_CONTENT_PROMPTS.get(category)
    if not prompt_template:
        return None

    prompt = prompt_template.format(
        name=business.name,
        description=business.description or business.name,
    )

    params = taste.adapted_params()
    system = (
        "You are a website content writer. Output ONLY valid YAML "
        "inside a code block. No commentary. Make content specific "
        "and realistic for this business."
    )

    result = _call_tier2(prompt, config, temperature=params.content_temperature,
                         system=system, max_tokens=2500)

    if result.error:
        log.warning("Block content generation failed: %s", result.error)
        return None

    content = _parse_yaml_response(result.text)
    if not content:
        log.warning("Block content YAML parse failed")
        return None

    log.info("Block content generated: %d keys, %dms",
             len(content), result.latency_ms)
    return content


# ---------------------------------------------------------------------------
# Phase 3B: Block inclusion decisions (tier 1)
# ---------------------------------------------------------------------------

def decide_block_inclusion(block_type: str,
                           business_desc: str,
                           category: str,
                           competitors: list[CompetitorProfile],
                           config: EngineConfig) -> bool:
    """Use tier 1 to decide if an optional block should be included."""
    # Question 1: Would this block improve the site?
    q1 = (
        f"This {category.replace('_', ' ')} business: {business_desc}\n\n"
        f"Question: Would adding a {block_type.replace('_', ' ')} section "
        f"improve this website?"
    )
    r1 = _call_tier1(q1, config, question_id=f"include_{block_type}_q1")

    # Question 2: Competitor signal
    comp_count = sum(
        1 for c in competitors
        if any(block_type in b or b.replace("has_", "") in block_type
               for b in c.blocks_present)
    )
    total = len(competitors) if competitors else 0

    if total > 0:
        q2 = (
            f"{comp_count} of {total} competitor websites have a "
            f"{block_type.replace('_', ' ')} section. "
            f"Should this site include one?"
        )
        r2 = _call_tier1(q2, config, question_id=f"include_{block_type}_q2")
    else:
        r2 = Tier1Result(answer="idk", question_id=f"include_{block_type}_q2")

    # Decision logic from spec
    if r1.answer == "yes" and r2.answer == "yes":
        return True
    if r1.answer == "yes" and r2.answer == "no":
        return True  # Differentiation opportunity
    if r1.answer == "no":
        return False
    # idk cases: fall through to bridge's heuristic
    return None  # Caller should use heuristic fallback


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

class DecisionEngine:
    """Orchestrates the full neural flowchart pipeline.

    Input:  Business description (+ optional scraped HTML)
    Output: BlockSiteSpec ready for rendering
    """

    def __init__(self, config: EngineConfig | None = None):
        self.config = config or EngineConfig()
        self.bridge = RoundtableBridge()
        self._stats: dict[str, Any] = {}

    def run(self, description: str,
            business_name: str = "",
            tagline: str = "",
            phone: str = "",
            email: str = "",
            address: str = "",
            primary_action: str = "learn",
            competitor_html: list[tuple[str, str]] | None = None,
            user_site_html: str | None = None) -> BlockSiteSpec:
        """Run the full pipeline: classify -> profile -> select -> generate -> assemble.

        Args:
            description: Business description text.
            business_name: Business name (extracted from description if empty).
            tagline: Optional tagline.
            phone/email/address: Contact info.
            primary_action: One of call, book, buy, learn, order.
            competitor_html: List of (url, html) tuples for competitor sites.
            user_site_html: HTML of user's existing site (if any).

        Returns: BlockSiteSpec ready for BlockRenderer.render()
        """
        t_start = time.monotonic()
        self._stats = {"tier1_calls": 0, "tier2_calls": 0, "phases": {}}

        # ── Phase 1: Category Classification ──
        t0 = time.monotonic()
        category = classify_category(description, self.config)
        self._stats["phases"]["classification"] = {
            "category": category,
            "ms": int((time.monotonic() - t0) * 1000),
        }
        log.info("Phase 1: classified as %s", category)

        # Load category config
        taxonomy = self.bridge.taxonomy
        cat_def = taxonomy["categories"][category]
        cat_bias = cat_def.get("skin_bias", {})

        # ── Phase 2A: Site Analysis ──
        t0 = time.monotonic()
        competitors = []
        if competitor_html:
            for i, (url, html) in enumerate(competitor_html):
                profile = analyze_site_html(html, self.config,
                                            site_label=f"comp{i}")
                profile.url = url
                competitors.append(profile)

        user_site_profile = None
        if user_site_html:
            user_site_profile = analyze_site_html(
                user_site_html, self.config, site_label="user_site"
            )

        self._stats["phases"]["site_analysis"] = {
            "competitors_analyzed": len(competitors),
            "user_site_analyzed": user_site_profile is not None,
            "ms": int((time.monotonic() - t0) * 1000),
        }

        # ── Phase 2B: Merge taste profile ──
        taste = merge_taste_profiles(cat_bias, competitors, user_site_profile)
        log.info("Phase 2: taste profile: f=%.2f d=%.2f w=%.2f c=%.2f b=%.2f",
                 taste.formality, taste.density, taste.warmth,
                 taste.creativity, taste.boldness)

        # Competitive intelligence
        all_blocks = []
        for assembly in cat_def.get("page_assemblies", {}).values():
            all_blocks.extend(assembly.get("required", []))
            all_blocks.extend(assembly.get("optional", []))
        all_blocks = list(set(all_blocks))

        derive_competitive_intel(competitors, all_blocks)

        # ── Phase 3: Block Selection ──
        t0 = time.monotonic()

        if not self.config.dry_run and competitors:
            # Use tier 1 for optional block inclusion decisions
            selected_overrides = {}
            for assembly in cat_def.get("page_assemblies", {}).values():
                for block_type in assembly.get("optional", []):
                    if block_type in selected_overrides:
                        continue
                    decision = decide_block_inclusion(
                        block_type, description, category,
                        competitors, self.config,
                    )
                    # decision is True/False/None
                    # We only override if tier 1 gave a definitive answer
                    # None means "use bridge heuristic"
                    if decision is not None:
                        # Store as block_type -> variant (bridge will select variant)
                        # We just note inclusion; variant selection stays in bridge
                        pass
                        # Future: could store exclusion list here

        self._stats["phases"]["block_selection"] = {
            "ms": int((time.monotonic() - t0) * 1000),
        }

        # ── Phase 3B: Content Generation ──
        t0 = time.monotonic()
        biz = BusinessInfo(
            name=business_name or self._extract_name(description),
            tagline=tagline,
            description=description,
            primary_action=primary_action,
            phone=phone,
            email=email,
            address=address,
        )

        content = generate_content(biz, category, taste, self.config)
        self._stats["phases"]["content_generation"] = {
            "fields": len(content),
            "ms": int((time.monotonic() - t0) * 1000),
        }

        # ── Phase 4: Assembly via Bridge ──
        t0 = time.monotonic()

        # Determine palette and font from taste (bridge does this too,
        # but we want to log the choice)
        bridge_input = RoundtableInput(
            business=biz,
            category=category,
            taste_profile=taste,
            competitors=competitors,
            user_site_profile=user_site_profile,
        )

        spec = self.bridge.build(bridge_input, content=content)

        self._stats["phases"]["assembly"] = {
            "pages": len(spec.pages),
            "total_blocks": sum(len(p.blocks) for p in spec.pages),
            "ms": int((time.monotonic() - t0) * 1000),
        }

        total_ms = int((time.monotonic() - t_start) * 1000)
        self._stats["total_ms"] = total_ms
        log.info("Pipeline complete: %d pages, %d blocks, %dms total",
                 len(spec.pages),
                 sum(len(p.blocks) for p in spec.pages),
                 total_ms)

        return spec

    @property
    def stats(self) -> dict:
        """Pipeline execution stats from the last run."""
        return self._stats

    def _extract_name(self, description: str) -> str:
        """Best-effort business name extraction from description."""
        # Take first sentence or first N chars
        for delim in (".", " - ", " is ", " was "):
            if delim in description:
                candidate = description.split(delim)[0].strip()
                if 2 < len(candidate) < 60:
                    return candidate
        return description[:50].strip()
