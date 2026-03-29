"""Block Populator -- translates decision engine output into block assembly specs.

This module sits between the decision engine (taste profile + category + business info)
and the BlockRenderer (page assembly YAML). It is the "make-or-break" module for MVP.

Input:  PopulatorInput (business info, category, taste profile, competitor intel)
Output: BlockSiteSpec (complete spec ready for BlockRenderer.render())

All schemas are Pydantic v2 for validation + YAML serialization.

Renamed from roundtable_bridge.py -- the old module name is kept for backward
compatibility but this is the canonical location.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Taxonomy loader
# ---------------------------------------------------------------------------

TAXONOMY_PATH = Path(__file__).parent / "taxonomy.yaml"

_taxonomy_cache: dict | None = None


def _load_taxonomy() -> dict:
    global _taxonomy_cache
    if _taxonomy_cache is None:
        with open(TAXONOMY_PATH, "r", encoding="utf-8") as f:
            _taxonomy_cache = yaml.safe_load(f)
    return _taxonomy_cache


# ---------------------------------------------------------------------------
# Input schemas (what the decision engine produces)
# ---------------------------------------------------------------------------

class TasteProfile(BaseModel):
    """5-dimension taste profile accumulated from user choices + site analysis."""
    formality: float = Field(0.5, ge=0.0, le=1.0)
    density: float = Field(0.5, ge=0.0, le=1.0)
    warmth: float = Field(0.5, ge=0.0, le=1.0)
    creativity: float = Field(0.3, ge=0.0, le=1.0)
    boldness: float = Field(0.5, ge=0.0, le=1.0)

    def adapted_params(self) -> "AdaptedParameters":
        """Calculate adapted AI/rendering parameters from this profile."""
        return AdaptedParameters(
            ai_temperature=round(0.15 + self.creativity * 0.4, 3),
            variant_pool_size=2 + round(self.creativity * 4),
            constraint_tightness=round(1.0 - self.creativity * 0.5, 3),
            content_temperature=round(0.10 + self.creativity * 0.5 + self.boldness * 0.1, 3),
            border_radius=f"{round(self.warmth * 16)}px",
        )


class AdaptedParameters(BaseModel):
    """Concrete parameters derived from taste profile."""
    ai_temperature: float = Field(ge=0.15, le=0.55)
    variant_pool_size: int = Field(ge=2, le=6)
    constraint_tightness: float = Field(ge=0.5, le=1.0)
    content_temperature: float = Field(ge=0.10, le=0.70)
    border_radius: str = "8px"


class CompetitorProfile(BaseModel):
    """Analyzed competitor site."""
    url: str = ""
    taste_profile: TasteProfile = Field(default_factory=TasteProfile)
    blocks_present: list[str] = Field(default_factory=list)


class BusinessInfo(BaseModel):
    """User-provided business information."""
    name: str
    tagline: str = ""
    description: str = ""
    primary_action: Literal["call", "book", "buy", "learn", "order"] = "learn"
    existing_site_url: str = ""
    phone: str = ""
    email: str = ""
    address: str = ""


class PopulatorInput(BaseModel):
    """Complete input to the block populator."""
    business: BusinessInfo
    category: Literal["service_business", "saas_product", "restaurant"]
    taste_profile: TasteProfile = Field(default_factory=TasteProfile)
    competitors: list[CompetitorProfile] = Field(default_factory=list)
    user_site_profile: CompetitorProfile | None = None

    # Overrides (optional -- decision engine can pre-select these)
    palette_id: str = ""
    font_pairing_id: str = ""
    selected_blocks: dict[str, str] | None = None  # {block_type: variant_id}


# ---------------------------------------------------------------------------
# Output schemas (what BlockRenderer consumes)
# ---------------------------------------------------------------------------

class BlockSpec(BaseModel):
    """Single block in a page assembly."""
    type: str
    variant: str
    # All remaining fields are template variables, stored as extra
    data: dict[str, Any] = Field(default_factory=dict)

    def to_render_dict(self) -> dict:
        """Flatten to the dict format BlockRenderer expects."""
        return {"type": self.type, "variant": self.variant, **self.data}


class PageSpec(BaseModel):
    """Single page in the site."""
    slug: str
    title: str = ""
    meta_description: str = ""
    blocks: list[BlockSpec] = Field(default_factory=list)

    def to_render_dict(self) -> dict:
        return {
            "slug": self.slug,
            "title": self.title,
            "meta_description": self.meta_description,
            "blocks": [b.to_render_dict() for b in self.blocks],
        }


class SkinSpec(BaseModel):
    """Resolved skin for the site."""
    palette_id: str = "ocean"
    font_pairing_id: str = "clean"
    border_radius: str = "8px"
    # Resolved values (filled by bridge)
    primary_color: str = ""
    secondary_color: str = ""
    accent_color: str = ""
    font_heading: str = ""
    font_body: str = ""

    def to_render_dict(self) -> dict:
        d = {}
        if self.palette_id:
            d["palette_id"] = self.palette_id
        if self.font_pairing_id:
            d["font_pairing_id"] = self.font_pairing_id
        d["border_radius"] = self.border_radius
        # Include resolved values as overrides
        for key in ("primary_color", "secondary_color", "accent_color",
                     "font_heading", "font_body"):
            val = getattr(self, key)
            if val:
                d[key] = val
        return d


class BlockSiteSpec(BaseModel):
    """Complete site spec ready for BlockRenderer.render()."""
    site: dict[str, str] = Field(default_factory=dict)
    skin: SkinSpec = Field(default_factory=SkinSpec)
    pages: list[PageSpec] = Field(default_factory=list)

    def to_render_dict(self) -> dict:
        return {
            "site": self.site,
            "skin": self.skin.to_render_dict(),
            "pages": [p.to_render_dict() for p in self.pages],
        }

    def to_yaml(self) -> str:
        return yaml.dump(self.to_render_dict(), default_flow_style=False,
                         allow_unicode=True, sort_keys=False, width=120)

    def save_yaml(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_yaml(), encoding="utf-8")


# ---------------------------------------------------------------------------
# Bridge logic
# ---------------------------------------------------------------------------

class BlockPopulator:
    """Translates decision engine output into a renderable site spec.

    This is the deterministic core. It does NOT call LLMs -- that's the
    decision engine's job. The populator takes the engine's decisions and
    assembles a complete BlockSiteSpec.

    LLM-generated content (headlines, descriptions, CTAs) is passed in
    via the `content` parameter to `build()`, not generated here.
    """

    def __init__(self):
        self.taxonomy = _load_taxonomy()

    def build(self, inp: PopulatorInput,
              content: dict[str, Any] | None = None) -> BlockSiteSpec:
        """Build a complete site spec from roundtable input.

        Args:
            inp: Decision engine output (business, category, taste, competitors).
            content: LLM-generated content map. Keys like "hero_headline",
                     "hero_subheadline", "cta_primary_text", "meta_description",
                     "section_headings" (dict), "service_descriptions" (list), etc.

        Returns a BlockSiteSpec ready for BlockRenderer.render().
        """
        content = content or {}
        params = inp.taste_profile.adapted_params()

        # 1. Resolve skin
        skin = self._resolve_skin(inp, params)

        # 2. Get category page assemblies
        cat_def = self.taxonomy["categories"][inp.category]
        default_pages = cat_def["default_pages"]
        assemblies = cat_def["page_assemblies"]

        # 3. Build pages
        pages = []
        for page_slug in default_pages:
            assembly = assemblies.get(page_slug, {})
            page = self._build_page(
                page_slug, assembly, inp, content, params
            )
            pages.append(page)

        # 4. Assemble site spec
        return BlockSiteSpec(
            site={
                "name": inp.business.name,
                "tagline": inp.business.tagline or content.get("tagline", ""),
                "description": inp.business.description,
            },
            skin=skin,
            pages=pages,
        )

    def _resolve_skin(self, inp: PopulatorInput,
                      params: AdaptedParameters) -> SkinSpec:
        """Resolve palette and font pairing from taste profile or overrides."""
        skin = SkinSpec(border_radius=params.border_radius)

        # Palette
        palette_id = inp.palette_id
        if not palette_id:
            palette_id = self._best_palette(inp.taste_profile)
        skin.palette_id = palette_id

        for p in self.taxonomy.get("skin", {}).get("palettes", []):
            if p["id"] == palette_id:
                skin.primary_color = p["primary"]
                skin.secondary_color = p["secondary"]
                skin.accent_color = p["accent"]
                break

        # Font pairing
        font_id = inp.font_pairing_id
        if not font_id:
            font_id = self._best_font_pairing(inp.taste_profile)
        skin.font_pairing_id = font_id

        for fp in self.taxonomy.get("skin", {}).get("font_pairings", []):
            if fp["id"] == font_id:
                skin.font_heading = fp["heading"]
                skin.font_body = fp["body"]
                break

        return skin

    def _best_palette(self, taste: TasteProfile) -> str:
        """Select palette by tag match against taste profile."""
        palettes = self.taxonomy.get("skin", {}).get("palettes", [])
        if not palettes:
            return "ocean"

        # Score each palette
        best_id = palettes[0]["id"]
        best_score = -1.0

        tag_scores = {
            "cool": 1.0 - taste.warmth,
            "warm": taste.warmth,
            "professional": taste.formality,
            "formal": taste.formality,
            "creative": taste.creativity,
            "bold": taste.boldness,
            "organic": taste.warmth * 0.7 + (1.0 - taste.boldness) * 0.3,
        }

        for p in palettes:
            score = sum(tag_scores.get(tag, 0.0) for tag in p.get("tags", []))
            if score > best_score:
                best_score = score
                best_id = p["id"]

        return best_id

    def _best_font_pairing(self, taste: TasteProfile) -> str:
        """Select font pairing by tag match against taste profile."""
        pairings = self.taxonomy.get("skin", {}).get("font_pairings", [])
        if not pairings:
            return "clean"

        best_id = pairings[0]["id"]
        best_score = -1.0

        tag_scores = {
            "formal": taste.formality,
            "professional": taste.formality,
            "modern": 0.5 + taste.creativity * 0.3,
            "friendly": 1.0 - taste.formality,
            "warm": taste.warmth,
            "elegant": taste.formality * 0.6 + taste.warmth * 0.4,
            "bold": taste.boldness,
            "minimal": 1.0 - taste.density,
            "creative": taste.creativity,
        }

        for fp in pairings:
            score = sum(tag_scores.get(tag, 0.0) for tag in fp.get("tags", []))
            if score > best_score:
                best_score = score
                best_id = fp["id"]

        return best_id

    def _build_page(self, slug: str, assembly: dict,
                    inp: PopulatorInput, content: dict,
                    params: AdaptedParameters) -> PageSpec:
        """Build a single page from category assembly + decisions."""
        page_title = content.get("page_titles", {}).get(
            slug, f"{inp.business.name} - {slug.replace('_', ' ').title()}"
        )
        meta_desc = content.get("meta_descriptions", {}).get(
            slug, inp.business.description
        )

        blocks = []

        # Required blocks
        for block_type in assembly.get("required", []):
            variant = self._select_variant(block_type, inp, params)
            block_data = self._populate_block(block_type, variant, inp, content, slug)
            blocks.append(BlockSpec(type=block_type, variant=variant, data=block_data))

        # Optional blocks (include based on pre-selected or taste-based heuristic)
        for block_type in assembly.get("optional", []):
            if inp.selected_blocks and block_type in inp.selected_blocks:
                variant = inp.selected_blocks[block_type]
                block_data = self._populate_block(block_type, variant, inp, content, slug)
                blocks.append(BlockSpec(type=block_type, variant=variant, data=block_data))
            elif self._should_include_optional(block_type, inp):
                variant = self._select_variant(block_type, inp, params)
                block_data = self._populate_block(block_type, variant, inp, content, slug)
                blocks.append(BlockSpec(type=block_type, variant=variant, data=block_data))

        # Enforce max_blocks
        max_blocks = assembly.get("max_blocks", 12)
        if len(blocks) > max_blocks:
            blocks = blocks[:max_blocks]

        # Reorder blocks: structural elements go in semantic order
        # top_info_strip before nav, nav first, footer last
        blocks = self._reorder_blocks(blocks)

        return PageSpec(
            slug="index" if slug == "home" else slug,
            title=page_title,
            meta_description=meta_desc,
            blocks=blocks,
        )

    @staticmethod
    def _reorder_blocks(blocks: list[BlockSpec]) -> list[BlockSpec]:
        """Reorder blocks into correct semantic position.

        Ensures: top_info_strip first, nav second, footer last,
        and everything else in between in original order.
        """
        top_strips = []
        nav_blocks = []
        footer_blocks = []
        body_blocks = []

        for b in blocks:
            if b.type == "top_info_strip":
                top_strips.append(b)
            elif b.type == "nav":
                nav_blocks.append(b)
            elif b.type == "footer":
                footer_blocks.append(b)
            else:
                body_blocks.append(b)

        return top_strips + nav_blocks + body_blocks + footer_blocks

    def _select_variant(self, block_type: str, inp: PopulatorInput,
                        params: AdaptedParameters) -> str:
        """Select best variant for a block based on taste profile tags.

        Only considers variants that have actual template files on disk.
        """
        # Check if pre-selected
        if inp.selected_blocks and block_type in inp.selected_blocks:
            return inp.selected_blocks[block_type]

        block_def = self.taxonomy.get("blocks", {}).get(block_type, {})
        variants = block_def.get("variants", [])
        if not variants:
            return block_type  # fallback to block name

        # Filter to variants with actual templates
        blocks_dir = Path(__file__).parent / "blocks"
        variants = [
            v for v in variants
            if (blocks_dir / block_type / f"{v['id']}.html.j2").exists()
        ]
        if not variants:
            # No templates at all -- return first from taxonomy as fallback
            return block_def["variants"][0]["id"]

        taste = inp.taste_profile
        tag_scores = {
            "formal": taste.formality,
            "conventional": 1.0 - taste.creativity,
            "minimal": 1.0 - taste.density,
            "modern": 0.5 + taste.creativity * 0.3,
            "warm": taste.warmth,
            "cool": 1.0 - taste.warmth,
            "bold": taste.boldness,
            "creative": taste.creativity,
            "dense": taste.density,
        }

        best_variant = variants[0]["id"]
        best_score = -1.0

        # Limit pool by creativity
        pool = variants[:params.variant_pool_size]

        for v in pool:
            score = sum(tag_scores.get(tag, 0.0) for tag in v.get("tags", []))
            if score > best_score:
                best_score = score
                best_variant = v["id"]

        return best_variant

    def _should_include_optional(self, block_type: str,
                                 inp: PopulatorInput) -> bool:
        """Heuristic: should this optional block be included?

        In the full pipeline, the decision engine (tier 1) makes this call.
        This is the deterministic fallback for --dry-run / no-LLM mode.
        """
        # Action-based inclusion
        action_blocks = {
            "call": ["top_info_strip", "booking_widget"],
            "book": ["booking_widget", "reservation_cta", "top_info_strip"],
            "buy": ["pricing_tiers", "brand_trust_bar"],
            "learn": ["faq_accordion", "blog_previews", "content_sections"],
            "order": ["order_online_cta", "menu_display"],
        }
        action = inp.business.primary_action
        if block_type in action_blocks.get(action, []):
            return True

        # Always include high-value social proof
        if block_type in ("testimonials", "stats_banner", "credentials_strip"):
            return True

        # Include process_steps for service businesses
        if block_type == "process_steps" and inp.category == "service_business":
            return True

        # Include specials for restaurants
        if block_type in ("specials_showcase", "photo_gallery") and inp.category == "restaurant":
            return True

        # Density check -- dense profiles include more
        if inp.taste_profile.density > 0.6:
            return True

        return False

    def _populate_block(self, block_type: str, variant: str,
                        inp: PopulatorInput, content: dict,
                        page_slug: str) -> dict:
        """Populate template variables for a block.

        Uses content dict for LLM-generated text, falls back to
        business info for structural data.
        """
        biz = inp.business
        data: dict[str, Any] = {}

        # Nav blocks get the same data everywhere
        if block_type == "nav":
            data["product_name"] = biz.name
            data["logo"] = ""
            data["nav_items"] = content.get("nav_items", self._default_nav(inp))
            cta = self._primary_cta(inp, content)
            data["cta_text"] = cta.get("text", "")
            data["cta_href"] = cta.get("href", "#")
            return data

        # Footer blocks
        if block_type == "footer":
            data["product_name"] = biz.name
            data["tagline"] = biz.tagline or content.get("tagline", "")
            data["footer_columns"] = content.get("footer_columns", [])
            data["social_links"] = content.get("social_links", [])
            name = biz.name.rstrip(".")
            data["copyright_text"] = content.get(
                "copyright_text", f"{name}. All rights reserved."
            )
            return data

        # Hero blocks
        if block_type == "hero_banner":
            section_content = content.get(f"hero_{page_slug}", content)
            data["headline"] = section_content.get(
                "hero_headline", content.get("hero_headline", biz.name)
            )
            data["subheadline"] = section_content.get(
                "hero_subheadline", content.get("hero_subheadline", biz.description)
            )
            cta = self._primary_cta(inp, content)
            data["cta_primary_text"] = cta.get("text", "")
            data["cta_primary_href"] = cta.get("href", "#")
            data["cta_secondary_text"] = content.get("cta_secondary_text", "")
            # Default secondary CTA to a useful page instead of "#"
            default_secondary = "contact.html"
            if inp.category == "saas_product":
                default_secondary = "pricing.html"
            elif inp.category == "restaurant":
                default_secondary = "menu.html"
            data["cta_secondary_href"] = content.get("cta_secondary_href", "") or default_secondary
            data["hero_image"] = content.get("hero_image", "")
            return data

        # CTA strip
        if block_type == "cta_strip":
            data["heading"] = content.get("cta_heading", f"Ready to get started with {biz.name}?")
            data["subheading"] = content.get("cta_subheading", biz.description)
            cta = self._primary_cta(inp, content)
            data["cta_text"] = cta.get("text", "Get Started")
            data["cta_href"] = cta.get("href", "#")
            return data

        # Contact form
        if block_type == "contact_form":
            data["section_headline"] = content.get(
                "section_headings", {}
            ).get("contact", "Contact Us")
            data["form_fields"] = content.get("form_fields", [
                {"name": "name", "label": "Your Name", "type": "text", "required": True},
                {"name": "email", "label": "Email", "type": "email", "required": True},
                {"name": "message", "label": "Message", "type": "textarea", "required": True},
            ])
            data["email"] = biz.email
            data["phone"] = biz.phone
            data["address"] = biz.address
            data["form_action"] = content.get("form_action", "")
            return data

        # Top info strip -- populated from business info
        if block_type == "top_info_strip":
            data["phone"] = biz.phone
            data["email"] = biz.email
            data["address"] = biz.address
            data["hours_text"] = content.get("hours_text", "")
            return data

        # Booking widget -- populated from business info
        if block_type == "booking_widget":
            data["heading"] = content.get(
                "section_headings", {}
            ).get("booking_widget", f"Schedule Service with {biz.name}")
            data["description"] = content.get(
                "booking_description",
                "Call us or book online for fast, reliable service.",
            )
            data["phone"] = biz.phone
            data["button_text"] = content.get("booking_button_text", "Book Online")
            data["booking_url"] = content.get("booking_url", "contact.html")
            return data

        # Credentials strip -- text badges (no images needed)
        if block_type == "credentials_strip":
            data["heading"] = content.get(
                "section_headings", {}
            ).get("credentials_strip", "")
            data["credentials"] = content.get("credentials", [])
            # Fallback: generate text-only badges if LLM didn't provide
            if not data["credentials"]:
                data["credentials"] = self._default_credentials(inp)
            return data

        # --- Blocks that need list data from LLM with fallbacks ---

        # Section headline (shared by all remaining blocks)
        section_key = block_type.replace("_", " ").title()
        data["section_headline"] = content.get(
            "section_headings", {}
        ).get(block_type, section_key)

        # Stats banner
        if block_type == "stats_banner":
            data["stats"] = content.get("stats", [])
            if not data["stats"]:
                data["stats"] = self._default_stats(inp)
            return data

        # Process steps
        if block_type == "process_steps":
            data["heading"] = data.pop("section_headline", "")
            data["steps"] = content.get("steps", [])
            if not data["steps"]:
                data["steps"] = self._default_steps(inp)
            return data

        # Testimonials
        if block_type == "testimonials":
            data["testimonials"] = content.get("testimonials", [])
            if not data["testimonials"]:
                data["testimonials"] = self._default_testimonials()
            return data

        # Services grid
        if block_type == "services_grid":
            data["services"] = content.get("services", [])
            if not data["services"]:
                data["services"] = self._default_services(inp, content)
            return data

        # Features grid (SaaS)
        if block_type == "features_grid":
            data["features"] = content.get("features", [])
            return data

        # FAQ accordion
        if block_type == "faq_accordion":
            data["faqs"] = content.get("faqs", [])
            if not data["faqs"]:
                data["faqs"] = self._default_faqs(inp)
            return data

        # Content sections (about page)
        if block_type == "content_sections":
            data["sections"] = content.get("sections", [])
            if not data["sections"]:
                data["sections"] = self._default_about_sections(inp, content)
            return data

        # Pricing tiers (SaaS)
        if block_type == "pricing_tiers":
            data["tiers"] = content.get("tiers", [])
            return data

        # Generic passthrough for unhandled blocks
        block_content = content.get(block_type, {})
        if isinstance(block_content, dict):
            data.update(block_content)
        elif isinstance(block_content, list):
            data[block_type] = block_content

        return data

    # ------------------------------------------------------------------
    # Default content generators (deterministic fallbacks)
    # ------------------------------------------------------------------

    def _default_credentials(self, inp: PopulatorInput) -> list[dict]:
        """Generate text-only credential badges from business info."""
        creds = []
        if inp.business.description:
            # Look for common credential signals in description
            desc_lower = inp.business.description.lower()
            keyword_map = [
                (("licensed", "license", "licensed technician"), "Licensed"),
                (("insured", "insurance", "fully insured"), "Insured"),
                (("bonded",), "Bonded"),
                (("certified", "certification"), "Certified"),
                (("bbb", "better business", "accredited"), "BBB Accredited"),
                (("award", "winning", "best of"), "Award Winning"),
                (("family owned", "family-owned", "family run"), "Family Owned"),
                (("veteran", "vet owned"), "Veteran Owned"),
                (("emergency", "24/7", "24 hour"), "24/7 Emergency Service"),
                (("guarantee", "guaranteed", "warranty"), "Satisfaction Guaranteed"),
                (("established", "since 19", "since 20"), "Established Business"),
            ]
            for keywords, label in keyword_map:
                if any(w in desc_lower for w in keywords):
                    creds.append({"name": label, "image": ""})

        # Category-appropriate defaults if nothing matched
        if not creds:
            if inp.category == "service_business":
                creds = [
                    {"name": "Licensed & Insured", "image": ""},
                    {"name": "Satisfaction Guaranteed", "image": ""},
                ]
            elif inp.category == "saas_product":
                creds = [
                    {"name": "SOC 2 Compliant", "image": ""},
                    {"name": "99.9% Uptime", "image": ""},
                ]
            elif inp.category == "restaurant":
                creds = [
                    {"name": "Health Inspected", "image": ""},
                    {"name": "Locally Sourced", "image": ""},
                ]
        return creds

    def _default_stats(self, inp: PopulatorInput) -> list[dict]:
        """Generate placeholder stats from business info."""
        stats = []
        desc_lower = (inp.business.description or "").lower()

        # Try to extract numbers from description
        import re
        numbers = re.findall(r'(\d+)\+?\s*(year|truck|client|customer|project|employee|team|location|store)', desc_lower)
        for val, label in numbers[:4]:
            stats.append({"value": f"{val}+", "label": label.title() + "s"})

        # Fill to 4 with category defaults
        if inp.category == "service_business":
            defaults = [
                {"value": "24/7", "label": "Emergency Service"},
                {"value": "100%", "label": "Satisfaction Guarantee"},
                {"value": "1000+", "label": "Projects Completed"},
                {"value": "5-Star", "label": "Average Rating"},
            ]
        elif inp.category == "saas_product":
            defaults = [
                {"value": "99.9%", "label": "Uptime"},
                {"value": "10K+", "label": "Active Users"},
                {"value": "50+", "label": "Integrations"},
                {"value": "24/7", "label": "Support"},
            ]
        else:  # restaurant
            defaults = [
                {"value": "Fresh", "label": "Daily Ingredients"},
                {"value": "100%", "label": "Scratch-Made"},
                {"value": "5-Star", "label": "Reviews"},
                {"value": "7 Days", "label": "A Week"},
            ]

        while len(stats) < 4 and defaults:
            stat = defaults.pop(0)
            if not any(s["label"] == stat["label"] for s in stats):
                stats.append(stat)

        return stats[:4]

    def _default_steps(self, inp: PopulatorInput) -> list[dict]:
        """Generate how-it-works steps by category."""
        if inp.category == "service_business":
            return [
                {"number": 1, "title": "Contact Us",
                 "description": f"Call us at {inp.business.phone or 'our number'} or fill out our online form."},
                {"number": 2, "title": "Get a Quote",
                 "description": "We evaluate your needs and provide a transparent, upfront quote."},
                {"number": 3, "title": "We Get It Done",
                 "description": "Our team completes the work to your satisfaction, guaranteed."},
            ]
        elif inp.category == "saas_product":
            return [
                {"number": 1, "title": "Sign Up Free",
                 "description": "Create your account in seconds. No credit card required."},
                {"number": 2, "title": "Set Up Your Workspace",
                 "description": "Import your data or start fresh with our guided setup."},
                {"number": 3, "title": "See Results",
                 "description": "Start getting value from day one with our intuitive tools."},
            ]
        else:  # restaurant
            return [
                {"number": 1, "title": "Browse Our Menu",
                 "description": "Explore our carefully crafted dishes and seasonal specials."},
                {"number": 2, "title": "Reserve a Table",
                 "description": "Book online or call us to reserve your spot."},
                {"number": 3, "title": "Enjoy Your Meal",
                 "description": "Sit back, relax, and let us take care of the rest."},
            ]

    def _default_testimonials(self) -> list[dict]:
        """Placeholder testimonials (should be replaced by LLM or scraped data)."""
        return [
            {"quote": "Outstanding service from start to finish. Highly recommended!",
             "name": "Happy Customer", "title": "Verified Review"},
            {"quote": "Professional, reliable, and fairly priced. We'll definitely be back.",
             "name": "Satisfied Client", "title": "Verified Review"},
            {"quote": "They went above and beyond our expectations. Five stars!",
             "name": "Loyal Customer", "title": "Verified Review"},
        ]

    def _default_services(self, inp: PopulatorInput,
                          content: dict) -> list[dict]:
        """Generate service cards from business description."""
        # Try to extract service names from description
        services = []
        desc = inp.business.description or ""

        # Common patterns: "Offers X, Y, Z" or "Services include X, Y, Z"
        import re
        for pattern in [
            r'(?:offers?|provides?|specializ\w+ in|services?\s+include)\s+(.+?)(?:\.|$)',
        ]:
            match = re.search(pattern, desc, re.IGNORECASE)
            if match:
                items = re.split(r',\s*(?:and\s+)?', match.group(1))
                for item in items:
                    item = item.strip().rstrip(".")
                    if 3 < len(item) < 60:
                        services.append({
                            "name": item.title(),
                            "description": "",
                            "icon": "",
                            "cta_text": "Learn More",
                            "cta_href": "contact.html",
                        })

        if not services:
            # Category defaults
            if inp.category == "service_business":
                services = [
                    {"name": "Residential Services", "description": "Quality service for your home.", "icon": ""},
                    {"name": "Commercial Services", "description": "Professional solutions for your business.", "icon": ""},
                    {"name": "Emergency Services", "description": "Available when you need us most.", "icon": ""},
                ]
            elif inp.category == "saas_product":
                services = [
                    {"name": "Core Platform", "description": "Everything you need to get started.", "icon": ""},
                    {"name": "Integrations", "description": "Connect with your existing tools.", "icon": ""},
                    {"name": "Analytics", "description": "Insights to drive better decisions.", "icon": ""},
                ]

        return services[:6]

    def _default_faqs(self, inp: PopulatorInput) -> list[dict]:
        """Generate category-appropriate FAQ entries."""
        biz = inp.business
        if inp.category == "service_business":
            return [
                {"question": "What areas do you serve?",
                 "answer": f"{biz.name} serves the local area and surrounding communities. Contact us for details."},
                {"question": "Do you offer free estimates?",
                 "answer": "Yes, we provide upfront quotes before starting any work. No surprises."},
                {"question": "Are you licensed and insured?",
                 "answer": f"Yes, {biz.name} is fully licensed and insured for your protection."},
            ]
        elif inp.category == "saas_product":
            return [
                {"question": "Is there a free trial?",
                 "answer": "Yes, you can try our platform free for 14 days. No credit card required."},
                {"question": "Can I cancel anytime?",
                 "answer": "Absolutely. No long-term contracts. Cancel anytime from your account settings."},
                {"question": "Do you offer team plans?",
                 "answer": "Yes, we offer plans for teams of all sizes with volume discounts."},
            ]
        else:  # restaurant
            return [
                {"question": "Do you take reservations?",
                 "answer": "Yes, reservations are recommended especially on weekends. Book online or call us."},
                {"question": "Do you accommodate dietary restrictions?",
                 "answer": "We offer vegetarian, vegan, and gluten-free options. Please inform your server of any allergies."},
                {"question": "Do you offer catering?",
                 "answer": f"Yes, {biz.name} offers catering for events of all sizes. Contact us for a custom menu."},
            ]

    def _default_about_sections(self, inp: PopulatorInput,
                                content: dict) -> list[dict]:
        """Generate about page content sections."""
        biz = inp.business
        sections = []

        # Our Story
        story = content.get("about_story", "")
        if not story and biz.description:
            story = biz.description
        if story:
            sections.append({"heading": "Our Story", "body": story})

        # Why Choose Us
        why_us = content.get("about_why_us", "")
        if not why_us:
            why_us = (
                f"At {biz.name}, we're committed to delivering quality and value. "
                f"Our team brings expertise, reliability, and a genuine dedication "
                f"to every customer we serve."
            )
        sections.append({"heading": f"Why Choose {biz.name}", "body": why_us})

        return sections

    def _default_nav(self, inp: PopulatorInput) -> list[dict]:
        """Generate default nav items from category page list."""
        cat_def = self.taxonomy["categories"][inp.category]
        nav = []
        for page in cat_def["default_pages"]:
            label = page.replace("_", " ").title()
            href = "index.html" if page == "home" else f"{page}.html"
            nav.append({"label": label, "href": href})
        return nav

    def _primary_cta(self, inp: PopulatorInput,
                     content: dict) -> dict[str, str]:
        """Determine primary CTA text and href from business action."""
        # Determine href from action type
        action_hrefs = {
            "call": f"tel:{inp.business.phone}" if inp.business.phone else "contact.html",
            "book": "contact.html",
            "buy": "contact.html",
            "learn": "contact.html",
            "order": "contact.html",
        }
        default_href = action_hrefs.get(inp.business.primary_action, "contact.html")

        if content.get("cta_primary_text"):
            return {
                "text": content["cta_primary_text"],
                "href": content.get("cta_primary_href", default_href),
            }

        action_ctas = {
            "call": "Call Now",
            "book": "Book Now",
            "buy": "Get Started",
            "learn": "Learn More",
            "order": "Order Now",
        }
        return {
            "text": action_ctas.get(inp.business.primary_action, "Get Started"),
            "href": default_href,
        }

    # ------------------------------------------------------------------
    # Dry-run mode
    # ------------------------------------------------------------------

    def dry_run(self, inp: PopulatorInput) -> dict:
        """Show what the roundtable would receive as input without calling LLM.

        Returns a dict summarizing:
        - Category and page assemblies that will be used
        - Taste profile and adapted parameters
        - Which optional blocks would be included (heuristic)
        - Which variants would be selected (tag matching)
        - What content fields need to be generated by LLM
        """
        params = inp.taste_profile.adapted_params()
        cat_def = self.taxonomy["categories"][inp.category]
        skin = self._resolve_skin(inp, params)

        pages_summary = {}
        content_needs = set()

        for page_slug in cat_def["default_pages"]:
            assembly = cat_def["page_assemblies"].get(page_slug, {})
            blocks = []

            for bt in assembly.get("required", []):
                v = self._select_variant(bt, inp, params)
                blocks.append({"type": bt, "variant": v, "status": "required"})
                content_needs.update(self._content_fields_for(bt))

            for bt in assembly.get("optional", []):
                included = self._should_include_optional(bt, inp)
                v = self._select_variant(bt, inp, params) if included else "n/a"
                blocks.append({
                    "type": bt, "variant": v,
                    "status": "included" if included else "excluded",
                })
                if included:
                    content_needs.update(self._content_fields_for(bt))

            pages_summary[page_slug] = blocks

        return {
            "category": inp.category,
            "taste_profile": inp.taste_profile.model_dump(),
            "adapted_parameters": params.model_dump(),
            "skin": skin.model_dump(),
            "pages": pages_summary,
            "content_fields_needed": sorted(content_needs),
            "competitor_count": len(inp.competitors),
        }

    def _content_fields_for(self, block_type: str) -> list[str]:
        """List of content fields the LLM needs to generate for this block."""
        field_map = {
            "hero_banner": ["hero_headline", "hero_subheadline"],
            "cta_strip": ["cta_heading", "cta_subheading"],
            "features_grid": ["features"],
            "testimonials": ["testimonials"],
            "services_grid": ["services"],
            "pricing_tiers": ["tiers"],
            "faq_accordion": ["faqs"],
            "menu_display": ["categories"],
            "contact_form": [],  # structural, not content
            "nav": ["nav_items"],
            "footer": ["footer_columns"],
            "process_steps": ["steps"],
            "stats_banner": ["stats"],
            "credentials_strip": ["credentials"],
            "brand_trust_bar": ["logos"],
            "specials_showcase": ["specials"],
            "reservation_cta": ["hours"],
            "chef_about": ["name", "bio_paragraphs"],
        }
        return field_map.get(block_type, [f"{block_type}_content"])


# ---------------------------------------------------------------------------
# Backward-compatible aliases (old names from roundtable_bridge.py)
# ---------------------------------------------------------------------------
RoundtableInput = PopulatorInput
RoundtableBridge = BlockPopulator
