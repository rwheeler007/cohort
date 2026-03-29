"""Block Renderer -- assembles pages from composable blocks.

Takes a block-style site spec (category, skin, pages with block lists)
and renders each page through blocks/_base.html.j2.

This is the V2 renderer. The original TemplateRenderer handles legacy
flat templates; this one handles the taxonomy-driven block system.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

BLOCKS_DIR = Path(__file__).parent / "blocks"
TAXONOMY_PATH = Path(__file__).parent / "taxonomy.yaml"


def load_taxonomy() -> dict:
    """Load and cache the taxonomy definition."""
    with open(TAXONOMY_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class BlockRenderer:
    """Renders a block-style site spec into static HTML files.

    Input format (YAML):
        site:
          name: "Joe's Plumbing"
          tagline: "..."
        skin:
          palette_id: ocean       # or inline primary/secondary/accent
          font_pairing_id: clean  # or inline heading/body
          border_radius: 8px
        pages:
          - slug: index
            title: "Home"
            meta_description: "..."
            blocks:
              - type: nav
                variant: nav_clean
                product_name: "Joe's Plumbing"
                nav_items: [...]
              - type: hero_banner
                variant: hero_centered
                headline: "Your Trusted Local Plumber"
                ...
    """

    def __init__(self, blocks_dir: Path | None = None):
        self.blocks_dir = blocks_dir or BLOCKS_DIR
        self.env = Environment(
            loader=FileSystemLoader(str(self.blocks_dir.parent)),
            autoescape=select_autoescape(["html"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self._taxonomy = None

    @property
    def taxonomy(self) -> dict:
        if self._taxonomy is None:
            self._taxonomy = load_taxonomy()
        return self._taxonomy

    def resolve_skin(self, skin_cfg: dict) -> dict:
        """Resolve palette/font IDs to concrete values using taxonomy."""
        skin = {}

        # Resolve palette
        palette_id = skin_cfg.get("palette_id")
        if palette_id:
            for p in self.taxonomy.get("skin", {}).get("palettes", []):
                if p["id"] == palette_id:
                    skin["primary_color"] = p["primary"]
                    skin["secondary_color"] = p["secondary"]
                    skin["accent_color"] = p["accent"]
                    break
        # Allow inline overrides
        for key in ("primary_color", "secondary_color", "accent_color"):
            if key in skin_cfg:
                skin[key] = skin_cfg[key]

        # Resolve font pairing
        font_id = skin_cfg.get("font_pairing_id")
        if font_id:
            for fp in self.taxonomy.get("skin", {}).get("font_pairings", []):
                if fp["id"] == font_id:
                    skin["font_heading"] = fp["heading"]
                    skin["font_body"] = fp["body"]
                    skin["google_fonts"] = [fp["heading"], fp["body"]]
                    break
        for key in ("font_heading", "font_body", "google_fonts"):
            if key in skin_cfg:
                skin[key] = skin_cfg[key]

        # Border radius
        skin["border_radius"] = skin_cfg.get("border_radius", "8px")

        return skin

    def render(self, spec: dict | str | Path, output_dir: str | Path) -> Path:
        """Render a full block-style site.

        Args:
            spec: Site spec dict, or path to a YAML file.
            output_dir: Target directory for generated HTML files.

        Returns the output directory Path.
        """
        if isinstance(spec, (str, Path)):
            with open(spec, "r", encoding="utf-8") as f:
                spec = yaml.safe_load(f)

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Resolve skin
        skin = self.resolve_skin(spec.get("skin", {}))
        site = spec.get("site", {})

        # Render each page
        for page_cfg in spec.get("pages", []):
            self._render_page(page_cfg, skin, site, output_dir)

        # Copy assets if specified
        assets_dir = spec.get("assets_dir")
        if assets_dir:
            assets_src = Path(assets_dir)
            if assets_src.exists():
                assets_dst = output_dir / "assets"
                if assets_dst.exists():
                    shutil.rmtree(assets_dst)
                shutil.copytree(assets_src, assets_dst)

        return output_dir

    def _render_page(self, page_cfg: dict, skin: dict, site: dict,
                     output_dir: Path) -> None:
        """Render a single page through blocks/_base.html.j2."""
        slug = page_cfg.get("slug", "page")
        filename = f"{slug}.html"

        template = self.env.get_template("blocks/_base.html.j2")

        # Each block in the list becomes available as `block` inside its template
        context = {
            "page": page_cfg,
            "skin": skin,
            "site": site,
        }

        html = template.render(**context)
        (output_dir / filename).write_text(html, encoding="utf-8")

    def render_single_block(self, block_type: str, variant: str,
                            block_data: dict, skin: dict | None = None) -> str:
        """Render a single block to HTML string. Useful for previews.

        Args:
            block_type: e.g. "hero_banner"
            variant: e.g. "hero_centered"
            block_data: Template variables for this block.
            skin: Skin dict (uses defaults if None).

        Returns rendered HTML fragment.
        """
        template_path = f"blocks/{block_type}/{variant}.html.j2"
        template = self.env.get_template(template_path)

        skin = skin or self.resolve_skin({"palette_id": "ocean", "font_pairing_id": "clean"})

        # The block templates expect `block.*` variables
        context = {
            "block": block_data,
            "skin": skin,
        }
        return template.render(**context)
