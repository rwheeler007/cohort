"""Site Brief schema -- the single contract between all pipeline stages.

A site_brief.yaml fully describes a website to be generated.  Every field
is consumed by the Jinja2 templates; the LLM roundtables populate
the ``roundtable_decisions`` section.
"""

from __future__ import annotations

import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Sub-schemas
# ---------------------------------------------------------------------------

@dataclass
class BrandTokens:
    """Design tokens extracted from brand analysis or user input."""
    primary_color: str = "#29AAE6"       # Main brand color
    secondary_color: str = "#081020"     # Dark accent / text
    accent_color: str = "#A4DEEE"        # Light accent / backgrounds
    background_color: str = "#ffffff"    # Page background
    text_color: str = "#081020"          # Body text
    font_heading: str = "system-ui, -apple-system, sans-serif"
    font_heading_weight: str = "700"   # Use "400" for single-weight fonts like Press Start 2P
    font_body: str = "system-ui, -apple-system, sans-serif"
    border_radius: str = "0.5rem"
    # Google Fonts to load (e.g. ["Press Start 2P", "Inter:wght@400;700"])
    google_fonts: list[str] = field(default_factory=list)
    # Derived from primary_color for hover states etc.
    primary_hover: str = ""

    def __post_init__(self):
        if not self.primary_hover:
            self.primary_hover = self.secondary_color


@dataclass
class NavItem:
    """Single navigation link, optionally with dropdown children."""
    label: str = ""
    href: str = ""
    external: bool = False
    children: list["NavItem"] = field(default_factory=list)


@dataclass
class FooterColumn:
    """A column in the site footer with a heading and links."""
    heading: str = ""
    links: list[NavItem] = field(default_factory=list)


@dataclass
class HeroSection:
    """Hero / landing section at top of homepage."""
    headline: str = ""
    subheadline: str = ""
    cta_primary_text: str = ""
    cta_primary_href: str = ""
    cta_secondary_text: str = ""
    cta_secondary_href: str = ""
    hero_image: str = ""          # Path or URL
    hero_video: str = ""          # Optional video background
    background_style: str = "gradient"  # gradient | image | video | solid


@dataclass
class PainPoint:
    """Single pain point for the Problem page."""
    title: str = ""
    description: str = ""
    cost_range: str = ""          # e.g. "$800-$2,000"
    icon: str = ""                # Optional icon class or path


@dataclass
class Testimonial:
    """Customer / user testimonial."""
    quote: str = ""
    name: str = ""
    title: str = ""
    company: str = ""
    photo: str = ""               # Optional photo path


@dataclass
class PricingTier:
    """Single pricing tier."""
    name: str = ""
    price: str = ""
    period: str = ""              # e.g. "/month", "one-time", ""
    description: str = ""
    features: list[str] = field(default_factory=list)
    cta_text: str = "Get Started"
    cta_href: str = "#"
    badge: str = ""               # e.g. "Most Popular", "Best Value"
    highlighted: bool = False


@dataclass
class Feature:
    """Single feature for How It Works / Features page."""
    title: str = ""
    description: str = ""
    icon: str = ""
    image: str = ""
    href: str = ""


@dataclass
class Step:
    """Single step in a process flow."""
    number: int = 0
    title: str = ""
    description: str = ""
    icon: str = ""


@dataclass
class SocialLink:
    """Social media link."""
    platform: str = ""            # github, twitter, linkedin, etc.
    url: str = ""
    label: str = ""


@dataclass
class ContactInfo:
    """Contact page configuration."""
    email: str = ""
    phone: str = ""
    address: str = ""
    form_fields: list[dict[str, str]] = field(default_factory=list)
    social_links: list[SocialLink] = field(default_factory=list)


@dataclass
class SEOConfig:
    """Per-page and global SEO configuration."""
    site_title: str = ""
    site_description: str = ""
    keywords: list[str] = field(default_factory=list)
    og_image: str = ""
    canonical_base: str = ""      # e.g. "https://cohort.dev"
    twitter_handle: str = ""


@dataclass
class PageConfig:
    """Configuration for which pages to generate and their content."""
    slug: str = ""                # e.g. "index", "pricing", "features"
    title: str = ""
    template: str = ""            # Template name: hero, problem, solution, etc.
    nav_label: str = ""           # Label in nav bar
    in_nav: bool = True
    meta_description: str = ""
    meta_keywords: list[str] = field(default_factory=list)
    # Page-specific content -- varies by template type
    content: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Main schema
# ---------------------------------------------------------------------------

@dataclass
class SiteBrief:
    """Complete website specification.

    This is the single source of truth consumed by the template renderer.
    The LLM roundtables populate ``roundtable_decisions``; everything else
    comes from user input or the intake scraper.
    """

    # --- Identity ---
    product_name: str = ""
    tagline: str = ""
    description: str = ""         # 1-2 sentence elevator pitch
    logo: str = ""                # Path to logo file
    favicon: str = ""

    # --- Brand ---
    brand: BrandTokens = field(default_factory=BrandTokens)

    # --- Navigation ---
    nav_items: list[NavItem] = field(default_factory=list)

    # --- Pages ---
    pages: list[PageConfig] = field(default_factory=list)

    # --- Shared content blocks ---
    hero: HeroSection = field(default_factory=HeroSection)
    pain_points: list[PainPoint] = field(default_factory=list)
    features: list[Feature] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    testimonials: list[Testimonial] = field(default_factory=list)
    pricing_tiers: list[PricingTier] = field(default_factory=list)
    contact: ContactInfo = field(default_factory=ContactInfo)

    # --- SEO ---
    seo: SEOConfig = field(default_factory=SEOConfig)

    # --- Footer ---
    footer_text: str = ""
    footer_links: list[NavItem] = field(default_factory=list)  # Legacy flat links (fallback)
    footer_columns: list[FooterColumn] = field(default_factory=list)  # Structured columns for footer.js
    built_with_cohort: bool = True  # Show "Built with Cohort" badge

    # --- Roundtable decisions (populated by LLM pipeline) ---
    roundtable_decisions: dict[str, Any] = field(default_factory=dict)

    # --- Source intelligence (populated by intake scraper) ---
    current_site_analysis: dict[str, Any] = field(default_factory=dict)
    competitor_analyses: list[dict[str, Any]] = field(default_factory=list)

    # --- Assets ---
    assets_dir: str = ""          # Directory containing images, etc.

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    @classmethod
    def from_yaml(cls, path: str | Path) -> "SiteBrief":
        """Load a SiteBrief from a YAML file."""
        path = Path(path)
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        return cls._from_dict(raw)

    @classmethod
    def _from_dict(cls, data: dict) -> "SiteBrief":
        """Recursively construct a SiteBrief from a raw dict."""
        brief = cls()

        # Simple scalar fields
        for key in ("product_name", "tagline", "description", "logo",
                    "favicon", "footer_text", "built_with_cohort",
                    "assets_dir"):
            if key in data:
                setattr(brief, key, data[key])

        # Brand tokens
        if "brand" in data:
            brief.brand = BrandTokens(**{
                k: v for k, v in data["brand"].items()
                if k in BrandTokens.__dataclass_fields__
            })

        # Navigation (supports nested children for dropdowns)
        if "nav_items" in data:
            brief.nav_items = [cls._parse_nav_item(n) for n in data["nav_items"]]

        # Hero
        if "hero" in data:
            brief.hero = HeroSection(**{
                k: v for k, v in data["hero"].items()
                if k in HeroSection.__dataclass_fields__
            })

        # Pain points
        if "pain_points" in data:
            brief.pain_points = [PainPoint(**p) for p in data["pain_points"]]

        # Features
        if "features" in data:
            brief.features = [Feature(**f) for f in data["features"]]

        # Steps
        if "steps" in data:
            brief.steps = [Step(**s) for s in data["steps"]]

        # Testimonials
        if "testimonials" in data:
            brief.testimonials = [Testimonial(**t) for t in data["testimonials"]]

        # Pricing tiers
        if "pricing_tiers" in data:
            brief.pricing_tiers = [PricingTier(**{
                k: v for k, v in t.items()
                if k in PricingTier.__dataclass_fields__
            }) for t in data["pricing_tiers"]]

        # Contact
        if "contact" in data:
            cdata = data["contact"]
            social = [SocialLink(**s) for s in cdata.pop("social_links", [])]
            brief.contact = ContactInfo(
                **{k: v for k, v in cdata.items()
                   if k in ContactInfo.__dataclass_fields__},
                social_links=social,
            )

        # SEO
        if "seo" in data:
            brief.seo = SEOConfig(**{
                k: v for k, v in data["seo"].items()
                if k in SEOConfig.__dataclass_fields__
            })

        # Pages
        if "pages" in data:
            brief.pages = [PageConfig(**p) for p in data["pages"]]

        # Footer links (legacy flat list)
        if "footer_links" in data:
            brief.footer_links = [cls._parse_nav_item(n) for n in data["footer_links"]]

        # Footer columns (structured)
        if "footer_columns" in data:
            brief.footer_columns = [
                FooterColumn(
                    heading=col.get("heading", ""),
                    links=[cls._parse_nav_item(lnk) for lnk in col.get("links", [])],
                )
                for col in data["footer_columns"]
            ]

        # Pass-through dicts
        for key in ("roundtable_decisions", "current_site_analysis",
                    "competitor_analyses"):
            if key in data:
                setattr(brief, key, data[key])

        return brief

    @staticmethod
    def _parse_nav_item(data: dict) -> NavItem:
        """Parse a NavItem dict, recursively handling children."""
        children_raw = data.pop("children", [])
        item = NavItem(**{k: v for k, v in data.items() if k in NavItem.__dataclass_fields__})
        if children_raw:
            item.children = [SiteBrief._parse_nav_item(c) for c in children_raw]
        # Restore mutated dict (pop removed 'children')
        if children_raw:
            data["children"] = children_raw
        return item

    def to_dict(self) -> dict:
        """Serialize to a plain dict for YAML export or template context."""
        import dataclasses
        return dataclasses.asdict(self)

    def save_yaml(self, path: str | Path) -> None:
        """Write this brief to a YAML file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False,
                      allow_unicode=True, sort_keys=False, width=120)
