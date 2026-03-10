"""Website Creator Pipeline -- the main orchestrator.

intake (scrape + worksheet) -> dual roundtables -> unified spec -> render

This is the entry point for the website creation process.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from cohort.website_creator.site_brief import SiteBrief
from cohort.website_creator.renderer import TemplateRenderer
from cohort.website_creator.intake import scrape_site, SiteAnalysis

log = logging.getLogger("cohort.website_creator")


class WebsiteCreator:
    """Orchestrates the full website creation pipeline.

    Stages:
        1. Intake: scrape current site + competitors
        2. Worksheet: LLM-guided 20-question brief (or load from YAML)
        3. Frontend roundtable: visual identity, layout, content strategy
        4. Backend roundtable: forms, SEO, performance, security
        5. Unify: merge roundtable decisions into site_brief
        6. Render: Jinja2 templates -> static HTML
        7. Validate: link check, accessibility basics
    """

    def __init__(self, output_base: str | Path | None = None):
        self.output_base = Path(output_base) if output_base else Path("output")
        self.renderer = TemplateRenderer()

    async def create_from_yaml(self, brief_path: str | Path) -> Path:
        """Create a website from an existing site_brief.yaml.

        This is the simplest path -- skips intake and roundtables.
        Used for testing and for pre-populated briefs.
        """
        brief = SiteBrief.from_yaml(brief_path)
        project_name = brief.product_name.lower().replace(" ", "-") or "site"
        output_dir = self.output_base / project_name

        log.info("Rendering %s from YAML brief...", brief.product_name)
        start = time.time()
        result = self.renderer.render(brief, output_dir)
        elapsed = time.time() - start
        log.info("Rendered %d pages in %.1fs -> %s",
                 len(brief.pages), elapsed, result)
        return result

    async def create_from_url(
        self,
        current_url: str,
        competitor_urls: list[str],
        worksheet_answers: dict[int, str],
    ) -> Path:
        """Full pipeline: scrape -> enrich -> render.

        Args:
            current_url: User's existing website URL
            competitor_urls: Up to 2 competitor URLs
            worksheet_answers: Dict mapping question ID -> answer string

        Returns:
            Path to the generated site directory.
        """
        log.info("Starting website creation pipeline...")
        start = time.time()

        # Stage 1: Scrape sites in parallel
        log.info("Stage 1: Scraping %d sites...", 1 + len(competitor_urls))
        scrape_tasks = [scrape_site(current_url)]
        for url in competitor_urls[:2]:
            scrape_tasks.append(scrape_site(url))

        results = await asyncio.gather(*scrape_tasks, return_exceptions=True)
        current_site = results[0] if not isinstance(results[0], Exception) else SiteAnalysis(url=current_url)
        competitors = [
            r for r in results[1:] if isinstance(r, SiteAnalysis)
        ]

        # Stage 2: Build brief from worksheet + scraped data
        log.info("Stage 2: Building brief from worksheet...")
        from cohort.website_creator.intake import build_brief_from_worksheet
        raw_brief = build_brief_from_worksheet(
            worksheet_answers, current_site, competitors
        )

        # Stage 3+4: Roundtables (placeholder -- will integrate with Cohort's
        # compiled_roundtable when wired into the server)
        log.info("Stage 3-4: Roundtable enrichment (placeholder)...")
        enriched_brief = await self._run_roundtables(raw_brief, current_site, competitors)

        # Stage 5: Build SiteBrief
        brief = SiteBrief._from_dict(enriched_brief)
        project_name = brief.product_name.lower().replace(" ", "-") or "site"
        output_dir = self.output_base / project_name

        # Stage 6: Render
        log.info("Stage 6: Rendering...")
        result = self.renderer.render(brief, output_dir)

        # Stage 7: Validate
        log.info("Stage 7: Validating...")
        issues = self._validate(result, brief)
        if issues:
            log.warning("Validation issues: %s", issues)

        elapsed = time.time() - start
        log.info("Pipeline complete: %d pages in %.1fs -> %s",
                 len(brief.pages), elapsed, result)

        # Save the brief for reference
        brief.save_yaml(output_dir / "site_brief.yaml")

        return result

    async def _run_roundtables(
        self,
        brief: dict,
        current_site: SiteAnalysis,
        competitors: list[SiteAnalysis],
    ) -> dict:
        """Run frontend + backend roundtables to enrich the brief.

        For now, this is a pass-through. When integrated with Cohort's
        server, it will use compiled_roundtable.py to run actual
        multi-agent discussions.

        The roundtable integration points:
        - Frontend RT: web_developer, brand_design_agent, content_strategy_agent, marketing_agent
          -> Decides: color refinement, layout choices, CTA wording, content tone
        - Backend RT: python_developer, security_agent
          -> Decides: form handling, SEO specifics, hosting recommendations

        Both feed into brief["roundtable_decisions"].
        """
        # TODO: Wire to Cohort compiled_roundtable
        # For now, apply sensible defaults from scraped data
        decisions = brief.get("roundtable_decisions", {})

        # Auto-populate steps if not provided
        if not brief.get("steps"):
            brief["steps"] = [
                {"number": 1, "title": "Sign Up", "description": "Create your free account in seconds.", "icon": ""},
                {"number": 2, "title": "Configure", "description": "Set up your preferences and connect your tools.", "icon": ""},
                {"number": 3, "title": "Launch", "description": "Go live and start seeing results immediately.", "icon": ""},
            ]

        # Auto-generate hero if missing
        hero = brief.get("hero", {})
        if not hero.get("headline"):
            hero["headline"] = brief.get("product_name", "Your Product")
            hero["subheadline"] = brief.get("tagline", "")
            hero["cta_primary_text"] = "Get Started"
            hero["cta_primary_href"] = "#pricing"
            brief["hero"] = hero

        brief["roundtable_decisions"] = decisions
        return brief

    def _validate(self, output_dir: Path, brief: SiteBrief) -> list[str]:
        """Basic validation of generated site."""
        issues = []

        # Check all expected pages exist
        for page in brief.pages:
            slug = page.slug if hasattr(page, "slug") else page.get("slug", "")
            page_file = output_dir / f"{slug}.html"
            if not page_file.exists():
                issues.append(f"Missing page: {slug}.html")

        # Check CSS exists
        if not (output_dir / "styles.css").exists():
            issues.append("Missing styles.css")

        # Check for broken internal links
        for html_file in output_dir.glob("*.html"):
            content = html_file.read_text(encoding="utf-8")
            import re
            for m in re.finditer(r'href="([^"#][^"]*\.html)"', content):
                link = m.group(1)
                if not link.startswith("http") and not (output_dir / link).exists():
                    issues.append(f"{html_file.name}: broken link to {link}")

        return issues


# ----- CLI -----

def main():
    """CLI entry point for testing."""
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if len(sys.argv) < 2:
        print("Usage: python -m cohort.website_creator.pipeline <site_brief.yaml>")
        print("       python -m cohort.website_creator.pipeline --demo")
        sys.exit(1)

    if sys.argv[1] == "--demo":
        # Generate from the Cohort test brief
        brief_path = Path(__file__).parent / "examples" / "cohort_site_brief.yaml"
        if not brief_path.exists():
            print(f"Demo brief not found: {brief_path}")
            sys.exit(1)
    else:
        brief_path = Path(sys.argv[1])

    creator = WebsiteCreator(output_base=Path(__file__).parent / "output")
    result = asyncio.run(creator.create_from_yaml(brief_path))
    print(f"\n[OK] Site generated: {result}")
    print(f"     Open {result / 'index.html'} in your browser.")


if __name__ == "__main__":
    main()
