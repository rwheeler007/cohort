"""Pydantic v2 schemas for the website creator pipeline.

Defines the contract between:
  - Decision Engine (produces EngineOutput)
  - Roundtable Bridge (consumes RoundtableInput, produces SiteSpec)
  - Block Renderer (consumes SiteSpec)

These schemas are the canonical definitions. The models in
roundtable_bridge.py are the runtime implementations; these schemas
can be used for validation, serialization, and API contracts.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Pipeline input
# ---------------------------------------------------------------------------

class BusinessInput(BaseModel):
    """Input to the website creation pipeline.

    Captures everything the user provides before the decision engine runs.
    """
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, description="Business name")
    description: str = Field("", description="Free-text business description")
    url: str = Field("", description="Existing website URL (if any)")
    category: Literal["service_business", "saas_product", "restaurant", ""] = Field(
        "", description="Business category; empty string means auto-classify"
    )
    action: Literal["call", "book", "buy", "learn", "order"] = Field(
        "learn", description="Primary call-to-action type"
    )
    competitor_html: list[tuple[str, str]] = Field(
        default_factory=list,
        description="List of (url, html) tuples for competitor sites",
    )
    user_site_html: str = Field(
        "", description="HTML of the user's existing site for analysis"
    )

    # Optional contact info
    tagline: str = ""
    phone: str = ""
    email: str = ""
    address: str = ""


# ---------------------------------------------------------------------------
# Taste profile
# ---------------------------------------------------------------------------

class TasteProfile(BaseModel):
    """5-dimension taste profile on a 0.0-1.0 scale.

    Accumulated from category defaults, competitor analysis, and user
    site analysis. Drives skin selection, variant picking, and content
    temperature.
    """
    model_config = ConfigDict(extra="forbid")

    formality: float = Field(0.5, ge=0.0, le=1.0, description="Corporate vs casual")
    density: float = Field(0.5, ge=0.0, le=1.0, description="Content-heavy vs airy")
    warmth: float = Field(0.5, ge=0.0, le=1.0, description="Warm/organic vs cool/minimal")
    creativity: float = Field(0.3, ge=0.0, le=1.0, description="Conventional vs experimental")
    boldness: float = Field(0.5, ge=0.0, le=1.0, description="Subtle vs high-contrast/loud")


# ---------------------------------------------------------------------------
# Block content
# ---------------------------------------------------------------------------

class BlockContent(BaseModel):
    """Content for a single block in a page assembly.

    `type` and `variant` identify which template to render.
    All other fields are template variables -- different block types
    need different fields, so extra fields are allowed.
    """
    model_config = ConfigDict(extra="allow")

    type: str = Field(..., description="Block type (e.g. hero_banner, nav, footer)")
    variant: str = Field(..., description="Template variant (e.g. hero_centered, nav_clean)")

    # Common optional fields across many block types
    headline: str = ""
    subheadline: str = ""
    section_headline: str = ""
    cta_text: str = ""
    cta_href: str = "#"

    def to_render_dict(self) -> dict[str, Any]:
        """Flatten to the dict format BlockRenderer expects."""
        d = self.model_dump()
        # Remove empty-string defaults that the template shouldn't see
        # unless they were explicitly set
        return {k: v for k, v in d.items() if v != "" or k in ("type", "variant")}


# ---------------------------------------------------------------------------
# Page spec
# ---------------------------------------------------------------------------

class PageSpec(BaseModel):
    """A single page in the generated site."""
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(..., description="URL slug (e.g. 'index', 'about', 'services')")
    title: str = Field("", description="HTML <title> content")
    meta_description: str = Field("", description="Meta description for SEO")
    blocks: list[BlockContent] = Field(
        default_factory=list, description="Ordered list of blocks on this page"
    )

    def to_render_dict(self) -> dict[str, Any]:
        """Convert to the dict format BlockRenderer expects."""
        return {
            "slug": self.slug,
            "title": self.title,
            "meta_description": self.meta_description,
            "blocks": [b.to_render_dict() for b in self.blocks],
        }


# ---------------------------------------------------------------------------
# Skin spec
# ---------------------------------------------------------------------------

class SkinSpec(BaseModel):
    """Visual skin for the site -- palette, fonts, and border radius.

    `palette_id` and `font_pairing_id` are taxonomy references resolved
    by the bridge into concrete color/font values.
    """
    model_config = ConfigDict(extra="forbid")

    palette_id: str = Field("ocean", description="Taxonomy palette ID")
    font_pairing_id: str = Field("clean", description="Taxonomy font pairing ID")
    border_radius: str = Field("8px", description="Global border radius for cards/buttons")

    # Resolved color values (populated by bridge from taxonomy)
    primary_color: str = ""
    secondary_color: str = ""
    accent_color: str = ""

    # Resolved font values (populated by bridge from taxonomy)
    font_heading: str = ""
    font_body: str = ""

    def to_render_dict(self) -> dict[str, Any]:
        """Convert to the dict format BlockRenderer.resolve_skin() produces."""
        d: dict[str, Any] = {}
        if self.palette_id:
            d["palette_id"] = self.palette_id
        if self.font_pairing_id:
            d["font_pairing_id"] = self.font_pairing_id
        d["border_radius"] = self.border_radius
        for key in ("primary_color", "secondary_color", "accent_color",
                     "font_heading", "font_body"):
            val = getattr(self, key)
            if val:
                d[key] = val
        return d


# ---------------------------------------------------------------------------
# Full site spec
# ---------------------------------------------------------------------------

class SiteSpec(BaseModel):
    """Complete site specification -- the central data structure.

    Produced by the roundtable bridge, consumed by the block renderer.
    """
    model_config = ConfigDict(extra="forbid")

    site: dict[str, str] = Field(
        default_factory=dict,
        description="Site-level metadata: name, tagline, description",
    )
    skin: SkinSpec = Field(default_factory=SkinSpec)
    pages: list[PageSpec] = Field(default_factory=list)

    def to_render_dict(self) -> dict[str, Any]:
        """Convert to the dict format BlockRenderer.render() expects."""
        return {
            "site": self.site,
            "skin": self.skin.to_render_dict(),
            "pages": [p.to_render_dict() for p in self.pages],
        }


# ---------------------------------------------------------------------------
# Engine output
# ---------------------------------------------------------------------------

class CompetitorSnapshot(BaseModel):
    """Summary of a single analyzed competitor site."""
    model_config = ConfigDict(extra="forbid")

    url: str = ""
    taste_profile: TasteProfile = Field(default_factory=TasteProfile)
    blocks_present: list[str] = Field(default_factory=list)


class EngineOutput(BaseModel):
    """What the decision engine produces after a full pipeline run.

    Contains the classified category, merged taste profile, generated
    content, block-specific content, competitor snapshots, and timing stats.
    """
    model_config = ConfigDict(extra="allow")

    category: Literal["service_business", "saas_product", "restaurant"] = Field(
        ..., description="Classified business category"
    )
    taste_profile: TasteProfile = Field(
        default_factory=TasteProfile,
        description="Merged taste profile (category bias + competitor signal)",
    )
    content: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "LLM-generated content map: hero_headline, hero_subheadline, "
            "cta_primary_text, section_headings, meta_descriptions, page_titles, etc."
        ),
    )
    block_content: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Block-specific list content: services, testimonials, stats, "
            "steps, faqs, sections, tiers, features, etc."
        ),
    )
    competitors: list[CompetitorSnapshot] = Field(
        default_factory=list,
        description="Analyzed competitor profiles",
    )
    stats: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Pipeline execution stats: tier1_calls, tier2_calls, total_ms, "
            "per-phase timing and metadata"
        ),
    )
