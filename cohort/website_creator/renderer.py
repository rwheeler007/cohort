"""Template Renderer -- turns a SiteBrief into static HTML files.

YAML-in, HTML-out. No LLM calls here -- pure template rendering.
"""

from __future__ import annotations

import json
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

    def __init__(self, templates_dir: Path | None = None, *, overwrite_existing: bool = False):
        self.templates_dir = templates_dir or TEMPLATES_DIR
        self.overwrite_existing = overwrite_existing
        self.env = Environment(
            loader=FileSystemLoader(str(self.templates_dir)),
            autoescape=select_autoescape(["html"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def render(self, brief: SiteBrief, output_dir: str | Path, *,
               ignore_status: bool = False) -> Path:
        """Render the full site into output_dir.

        Args:
            brief: The site specification to render.
            output_dir: Target directory for generated files.
            ignore_status: If True, skip the graduation guard (used by preview mode).

        Returns the output directory Path.
        """
        if not ignore_status and getattr(brief, "status", "draft") == "graduated":
            raise RuntimeError(
                f"Cannot render graduated site '{brief.product_name}'. "
                f"Use preview mode or change status in site_brief.yaml."
            )

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Build shared template context from the brief (all dicts)
        context = brief.to_dict()
        context["jsonld"] = self._build_jsonld(brief)

        # 1. Generate CSS from tokens
        self._render_css(brief, output_dir)

        # 2. Render each page (use dict versions from context)
        for page_cfg in context["pages"]:
            self._render_page(page_cfg, context, output_dir)

        # 3. Generate nav.js and footer.js from brief data
        self._render_nav_js(brief, output_dir)
        self._render_footer_js(brief, output_dir)

        # 5. Copy assets if provided
        if brief.assets_dir:
            assets_src = Path(brief.assets_dir)
            if assets_src.exists():
                assets_dst = output_dir / "assets"
                if assets_dst.exists():
                    shutil.rmtree(assets_dst)
                shutil.copytree(assets_src, assets_dst)

        # 6. Generate sitemap.xml
        self._render_sitemap(brief, output_dir)

        # 7. Generate robots.txt
        self._render_robots(brief, output_dir)

        # 8. Generate 404 page
        self._render_404(brief, output_dir)

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
        """Render a single page.

        Skips pages that already exist on disk unless the page config
        sets ``overwrite: true`` or the renderer was created with
        ``overwrite_existing=True``.  This protects hand-edited HTML
        from being clobbered by a re-render.
        """
        slug = page_cfg.get("slug", "page")
        filename = f"{slug}.html"
        target = output_dir / filename

        # Skip existing files unless explicitly told to overwrite
        if target.exists() and not self.overwrite_existing:
            page_overwrite = page_cfg.get("overwrite", False)
            if not page_overwrite:
                return

        template_name = page_cfg.get("template", "content")
        template_file = TEMPLATE_MAP.get(template_name, "content.html.j2")

        try:
            template = self.env.get_template(template_file)
        except Exception:
            # Fall back to content template
            template = self.env.get_template("content.html.j2")

        # Merge page-specific context
        page_context = {**context, "page": page_cfg}

        html = template.render(**page_context)
        target.write_text(html, encoding="utf-8")

    # ------------------------------------------------------------------
    # Nav & Footer JS generation
    # ------------------------------------------------------------------

    @staticmethod
    def _nav_item_to_js(item) -> dict:
        """Convert a NavItem (or dict) to a JS-compatible dict."""
        if isinstance(item, dict):
            label = item.get("label", "")
            href = item.get("href", "")
            children = item.get("children", [])
        else:
            label, href, children = item.label, item.href, item.children

        if children:
            return {
                "label": label,
                "children": [TemplateRenderer._nav_item_to_js(c) for c in children],
            }
        return {"label": label, "href": href}

    def _render_nav_js(self, brief: SiteBrief, output_dir: Path) -> None:
        """Generate nav.js from the brief's nav_items."""
        items = [self._nav_item_to_js(n) for n in brief.nav_items]
        items_json = json.dumps(items, indent=4)

        # Build favicon injector if no favicon file is specified.
        # Uses brand primary color and product initials as an inline SVG.
        favicon_block = ""
        if not brief.favicon:
            # URL-encode the primary color (# -> %23)
            color = brief.brand.primary_color.replace("#", "%23")
            text_color = brief.brand.secondary_color.replace("#", "%23")
            # Use first 2 chars of product name as initials
            initials = brief.product_name[:2].upper() if brief.product_name else "CO"
            favicon_block = f"""\
// Inject favicon from brand colors (works with local file:// and hosted)
(function () {{
    if (!document.querySelector('link[rel="icon"]')) {{
        var link = document.createElement("link");
        link.rel = "icon";
        link.type = "image/svg+xml";
        link.href = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect x='3' y='4' width='26' height='18' rx='3' fill='{color}'/%3E%3Cpolygon points='8,22 14,22 10,28' fill='{color}'/%3E%3Ctext x='16' y='17' text-anchor='middle' font-family='monospace' font-weight='bold' font-size='14' fill='{text_color}'%3E{initials}%3C/text%3E%3C/svg%3E";
        document.head.appendChild(link);
    }}
}})();

"""

        js = f"""\
{favicon_block}\
// Single source of truth for site navigation.
// Generated by Cohort Website Creator from the site brief.
// Items with `children` render as grouped dropdowns.
const NAV_ITEMS = {items_json};

document.addEventListener("DOMContentLoaded", function () {{
    var ul = document.querySelector(".nav-links");
    if (!ul) return;

    ul.innerHTML = NAV_ITEMS.map(function (item) {{
        if (!item.children) {{
            return '<li><a href="' + item.href + '">' + item.label + '</a></li>';
        }}
        var subs = item.children.map(function (child) {{
            return '<li><a href="' + child.href + '">' + child.label + '</a></li>';
        }}).join("\\n");
        return '<li class="nav-dropdown">' +
            '<button class="nav-dropdown-toggle" aria-expanded="false">' +
                item.label + ' <span class="nav-caret">&#9662;</span>' +
            '</button>' +
            '<ul class="nav-dropdown-menu">' + subs + '</ul>' +
        '</li>';
    }}).join("\\n");

    // Desktop: open/close on click, close when clicking outside
    document.addEventListener("click", function (e) {{
        var toggle = e.target.closest(".nav-dropdown-toggle");
        var allDropdowns = ul.querySelectorAll(".nav-dropdown");

        if (toggle) {{
            e.preventDefault();
            var parent = toggle.closest(".nav-dropdown");
            var isOpen = parent.classList.contains("open");

            // Close all first
            allDropdowns.forEach(function (d) {{
                d.classList.remove("open");
                d.querySelector(".nav-dropdown-toggle").setAttribute("aria-expanded", "false");
            }});

            // Toggle the clicked one
            if (!isOpen) {{
                parent.classList.add("open");
                toggle.setAttribute("aria-expanded", "true");
            }}
        }} else {{
            // Click outside -- close all
            allDropdowns.forEach(function (d) {{
                d.classList.remove("open");
                d.querySelector(".nav-dropdown-toggle").setAttribute("aria-expanded", "false");
            }});
        }}
    }});
}});
"""
        (output_dir / "nav.js").write_text(js, encoding="utf-8")

    def _render_footer_js(self, brief: SiteBrief, output_dir: Path) -> None:
        """Generate footer.js from the brief's footer config."""
        import dataclasses

        # Build footer config from structured columns or legacy flat links
        footer_cfg = {
            "brand": {
                "name": brief.product_name,
                "tagline": brief.tagline,
            },
            "columns": [],
            "bottom": {
                "copyright": brief.footer_text or f"&copy; {brief.product_name}. All rights reserved.",
                "badge": "[*] Built with Cohort" if brief.built_with_cohort else "",
            },
        }

        if brief.footer_columns:
            # Use structured columns
            for col in brief.footer_columns:
                col_data = {
                    "heading": col.heading,
                    "links": [
                        {"label": lnk.label, "href": lnk.href, **({"external": True} if lnk.external else {})}
                        for lnk in col.links
                    ],
                }
                footer_cfg["columns"].append(col_data)
        else:
            # Fallback: build columns from legacy footer_links + contact
            if brief.footer_links:
                footer_cfg["columns"].append({
                    "heading": "Links",
                    "links": [{"label": lnk.label, "href": lnk.href, **({"external": True} if lnk.external else {})} for lnk in brief.footer_links],
                })
            if brief.contact.email or brief.contact.social_links:
                connect_links = []
                if brief.contact.email:
                    connect_links.append({"label": brief.contact.email, "href": f"mailto:{brief.contact.email}"})
                for social in brief.contact.social_links:
                    connect_links.append({"label": social.label or social.platform, "href": social.url, "external": True})
                footer_cfg["columns"].append({"heading": "Connect", "links": connect_links})

        cfg_json = json.dumps(footer_cfg, indent=4)

        js = f"""\
// Single source of truth for site footer.
// Generated by Cohort Website Creator from the site brief.
var FOOTER_CONFIG = {cfg_json};

document.addEventListener("DOMContentLoaded", function () {{
    var footer = document.querySelector(".site-footer");
    if (!footer) return;

    var brandHtml =
        '<div>' +
            '<h4 style="color:#fff; margin-bottom:1rem;">' + FOOTER_CONFIG.brand.name + '</h4>' +
            '<p>' + FOOTER_CONFIG.brand.tagline + '</p>' +
        '</div>';

    var columnsHtml = FOOTER_CONFIG.columns.map(function (col) {{
        var linksHtml = col.links.map(function (link) {{
            var attrs = link.external ? ' target="_blank" rel="noopener"' : '';
            return '<li style="margin-bottom:0.5rem;"><a href="' + link.href + '"' + attrs + '>' + link.label + '</a></li>';
        }}).join("\\n");
        return '<div>' +
            '<h4 style="color:#fff; margin-bottom:1rem;">' + col.heading + '</h4>' +
            '<ul style="list-style:none; padding:0; margin:0;">' + linksHtml + '</ul>' +
        '</div>';
    }}).join("\\n");

    footer.innerHTML =
        '<div class="container">' +
            '<div class="footer-grid">' + brandHtml + columnsHtml + '</div>' +
            '<div class="footer-bottom">' +
                '<p>' + FOOTER_CONFIG.bottom.copyright + '</p>' +
                (FOOTER_CONFIG.bottom.badge ? '<div class="cohort-badge">' + FOOTER_CONFIG.bottom.badge + '</div>' : '') +
            '</div>' +
        '</div>';
}});
"""
        (output_dir / "footer.js").write_text(js, encoding="utf-8")

    def _render_sitemap(self, brief: SiteBrief, output_dir: Path) -> None:
        """Generate a simple sitemap.xml."""
        if not brief.seo.canonical_base:
            return

        from datetime import date
        today = date.today().isoformat()
        base = brief.seo.canonical_base.rstrip("/")
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        ]
        for page in brief.pages:
            if hasattr(page, "slug"):
                slug = page.slug
            elif isinstance(page, dict):
                slug = page.get("slug", "")
            else:
                slug = ""
            priority = "1.0" if slug == "index" else "0.8"
            lines.append(f"  <url>")
            lines.append(f"    <loc>{base}/{slug}.html</loc>")
            lines.append(f"    <lastmod>{today}</lastmod>")
            lines.append(f"    <priority>{priority}</priority>")
            lines.append(f"  </url>")
        lines.append("</urlset>")
        (output_dir / "sitemap.xml").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _render_robots(self, brief: SiteBrief, output_dir: Path) -> None:
        """Generate robots.txt."""
        lines = ["User-agent: *", "Allow: /"]
        if brief.seo.canonical_base:
            base = brief.seo.canonical_base.rstrip("/")
            lines.append(f"Sitemap: {base}/sitemap.xml")
        (output_dir / "robots.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _render_404(self, brief: SiteBrief, output_dir: Path) -> None:
        """Generate a branded 404 page for static hosts."""
        import dataclasses
        brand = dataclasses.asdict(brief.brand)
        name = brief.product_name or "Site"
        lang = brief.language or "en"
        html = f"""\
<!DOCTYPE html>
<html lang="{lang}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Page Not Found | {name}</title>
    <meta name="robots" content="noindex">
    <link rel="stylesheet" href="styles.css">
</head>
<body>
    <nav class="site-nav" aria-label="Main navigation">
        <div class="container">
            <a href="index.html" class="nav-brand" style="font-weight:400; font-size:1.15rem; font-family: var(--font-heading); letter-spacing:1px; color: var(--color-primary);">
                {name}
            </a>
        </div>
    </nav>
    <main id="main" style="text-align:center; padding:6rem 1rem;">
        <h1 class="responsive-h1">Page Not Found</h1>
        <p class="responsive-p-large" style="max-width:40ch;">
            The page you're looking for doesn't exist or has been moved.
        </p>
        <a href="index.html" class="btn-primary" style="margin-top:2rem;">Back to Home</a>
    </main>
</body>
</html>
"""
        (output_dir / "404.html").write_text(html, encoding="utf-8")

    @staticmethod
    def _build_jsonld(brief: SiteBrief) -> str:
        """Build JSON-LD structured data for the homepage.

        Generates Organization schema for all sites, plus LocalBusiness
        if address/phone are provided (service businesses, restaurants).
        """
        canonical = brief.seo.canonical_base.rstrip("/") if brief.seo.canonical_base else ""

        org: dict = {
            "@context": "https://schema.org",
            "@type": "Organization",
            "name": brief.product_name,
        }
        if brief.description:
            org["description"] = brief.description
        if canonical:
            org["url"] = canonical
        if brief.logo and brief.logo.startswith("http"):
            org["logo"] = brief.logo
        if brief.seo.og_image:
            org["image"] = brief.seo.og_image
        if brief.contact.email:
            org["email"] = brief.contact.email
        if brief.contact.phone:
            org["telephone"] = brief.contact.phone

        # Social profiles
        same_as = [s.url for s in brief.contact.social_links if s.url]
        if same_as:
            org["sameAs"] = same_as

        # Upgrade to LocalBusiness if we have a physical address
        if brief.contact.address:
            org["@type"] = "LocalBusiness"
            org["address"] = {
                "@type": "PostalAddress",
                "streetAddress": brief.contact.address,
            }

        return json.dumps(org, indent=2)
