"""Template Renderer -- turns a SiteBrief into static HTML files.

YAML-in, HTML-out. No LLM calls here -- pure template rendering.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from cohort.website_creator.site_brief import SiteBrief


# Map template names to template files
TEMPLATE_MAP = {
    "hero": "hero.html.j2",
    "problem": "problem.html.j2",
    "solution": "solution.html.j2",
    "features": "features.html.j2",
    "how-it-works": "features.html.j2",  # alias
    "contact": "contact.html.j2",
    "content": "content.html.j2",
    "about": "content.html.j2",
    "legal": "content.html.j2",
    "docs": "content.html.j2",
    "documentation": "content.html.j2",
    "benchmarks": "benchmarks.html.j2",
    "marketing": "marketing.html.j2",
    "ai-perspective": "ai-perspective.html.j2",
}

TEMPLATES_DIR = Path(__file__).parent / "templates"
TOKENS_DIR = Path(__file__).parent / "tokens"


class TemplateRenderer:
    """Renders a SiteBrief into a directory of static HTML + CSS files."""

    def __init__(self, templates_dir: Path | None = None):
        self.templates_dir = templates_dir or TEMPLATES_DIR
        self.env = Environment(
            loader=FileSystemLoader(str(self.templates_dir)),
            autoescape=select_autoescape(["html"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def render(self, brief: SiteBrief, output_dir: str | Path) -> Path:
        """Render the full site into output_dir.

        Returns the output directory Path.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Build shared template context from the brief (all dicts)
        context = brief.to_dict()

        # 1. Generate CSS from tokens
        self._render_css(brief, output_dir)

        # 2. Render each page (use dict versions from context)
        for page_cfg in context["pages"]:
            self._render_page(page_cfg, context, output_dir)

        # 3. Copy assets if provided
        if brief.assets_dir:
            assets_src = Path(brief.assets_dir)
            if assets_src.exists():
                assets_dst = output_dir / "assets"
                if assets_dst.exists():
                    shutil.rmtree(assets_dst)
                shutil.copytree(assets_src, assets_dst)

        # 4. Generate sitemap.xml
        self._render_sitemap(brief, output_dir)

        # 5. Generate robots.txt
        self._render_robots(brief, output_dir)

        return output_dir

    def _render_css(self, brief: SiteBrief, output_dir: Path) -> None:
        """Render CSS from design tokens."""
        tokens_template = TOKENS_DIR / "styles.css.j2"
        if tokens_template.exists():
            css_env = Environment(
                loader=FileSystemLoader(str(TOKENS_DIR)),
                autoescape=False,
            )
            template = css_env.get_template("styles.css.j2")
            import dataclasses
            brand_dict = dataclasses.asdict(brief.brand)
            # Jinja2 needs dot-access, use a simple namespace
            class _NS:
                def __init__(self, d):
                    self.__dict__.update(d)
            css = template.render(brand=_NS(brand_dict))
            (output_dir / "styles.css").write_text(css, encoding="utf-8")

    def _render_page(self, page_cfg: dict, context: dict, output_dir: Path) -> None:
        """Render a single page."""
        template_name = page_cfg.get("template", "content")
        template_file = TEMPLATE_MAP.get(template_name, "content.html.j2")

        try:
            template = self.env.get_template(template_file)
        except Exception:
            # Fall back to content template
            template = self.env.get_template("content.html.j2")

        # Merge page-specific context
        page_context = {**context, "page": page_cfg}

        slug = page_cfg.get("slug", "page")
        filename = f"{slug}.html"
        html = template.render(**page_context)
        (output_dir / filename).write_text(html, encoding="utf-8")

    def _render_sitemap(self, brief: SiteBrief, output_dir: Path) -> None:
        """Generate a simple sitemap.xml."""
        if not brief.seo.canonical_base:
            return

        base = brief.seo.canonical_base.rstrip("/")
        urls = []
        for page in brief.pages:
            slug = page.slug if isinstance(page, dict) is False else page.get("slug", "")
            if hasattr(page, "slug"):
                slug = page.slug
            elif isinstance(page, dict):
                slug = page.get("slug", "")
            priority = "1.0" if slug == "index" else "0.8"
            urls.append(f"  <url><loc>{base}/{slug}.html</loc><priority>{priority}</priority></url>")

        sitemap = '<?xml version="1.0" encoding="UTF-8"?>\n'
        sitemap += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        sitemap += "\n".join(urls) + "\n"
        sitemap += "</urlset>\n"
        (output_dir / "sitemap.xml").write_text(sitemap, encoding="utf-8")

    def _render_robots(self, brief: SiteBrief, output_dir: Path) -> None:
        """Generate robots.txt."""
        lines = ["User-agent: *", "Allow: /"]
        if brief.seo.canonical_base:
            base = brief.seo.canonical_base.rstrip("/")
            lines.append(f"Sitemap: {base}/sitemap.xml")
        (output_dir / "robots.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
