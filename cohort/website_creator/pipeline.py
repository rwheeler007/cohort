"""Website Creator Pipeline -- the main orchestrator.

intake (scrape + worksheet) -> dual roundtables -> unified spec -> render

This is the entry point for the website creation process.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path

from cohort.website_creator.intake import SiteAnalysis, scrape_site
from cohort.website_creator.renderer import TemplateRenderer
from cohort.website_creator.site_brief import SiteBrief

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

    def graduate(self, project_name: str, destination: Path) -> Path:
        """Move a generated site out of the creator into a permanent location.

        Updates the status to 'graduated' in both the destination copy and
        the source brief in examples/ (if it exists), preventing future
        renders from overwriting the production site.

        Args:
            project_name: Name of the project directory in output_base.
            destination: Parent directory for the graduated site
                         (e.g. ``cohort/website/``).

        Returns:
            Path to the graduated site directory.
        """
        source = self.output_base / project_name
        if not source.exists():
            raise FileNotFoundError(f"No generated site: {project_name}")

        dest = destination / project_name
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, dest, dirs_exist_ok=True)

        # Update status in the destination brief
        dest_brief_path = dest / "site_brief.yaml"
        if dest_brief_path.exists():
            brief = SiteBrief.from_yaml(dest_brief_path)
            brief.status = "graduated"
            brief.save_yaml(dest_brief_path)

        # Also update the canonical source brief in examples/
        examples_brief = Path(__file__).parent / "examples" / f"{project_name}_site_brief.yaml"
        if examples_brief.exists():
            src_brief = SiteBrief.from_yaml(examples_brief)
            src_brief.status = "graduated"
            src_brief.save_yaml(examples_brief)

        log.info("[OK] Graduated '%s' to %s", project_name, dest)
        return dest

    async def preview(self, brief_path: str | Path) -> Path:
        """Render a site to a preview location, bypassing graduation guard.

        Useful for testing template changes against a graduated site
        without touching the production files.

        Returns:
            Path to the preview directory.
        """
        brief = SiteBrief.from_yaml(brief_path)
        project_name = brief.product_name.lower().replace(" ", "-") or "site"
        preview_dir = self.output_base / f"{project_name}-preview"

        log.info("Rendering preview for %s...", brief.product_name)
        start = time.time()
        result = self.renderer.render(brief, preview_dir, ignore_status=True)
        elapsed = time.time() - start
        log.info("Preview rendered: %d pages in %.1fs -> %s",
                 len(brief.pages), elapsed, result)
        return result

    def _validate(self, output_dir: Path, brief: SiteBrief) -> list[str]:
        """Basic validation of generated site."""
        import re
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
            for m in re.finditer(r'href="([^"#][^"]*\.html)"', content):
                link = m.group(1)
                if not link.startswith("http") and not (output_dir / link).exists():
                    issues.append(f"{html_file.name}: broken link to {link}")

        # SEO checks
        for html_file in output_dir.glob("*.html"):
            content = html_file.read_text(encoding="utf-8")
            name = html_file.name
            if '<meta name="description"' not in content:
                issues.append(f"{name}: missing meta description")
            if not re.search(r'<title>.+</title>', content):
                issues.append(f"{name}: missing or empty <title>")
            h1_count = len(re.findall(r'<h1[\s>]', content))
            if h1_count == 0:
                issues.append(f"{name}: no <h1> tag")
            elif h1_count > 1:
                issues.append(f"{name}: multiple <h1> tags ({h1_count})")
            for img_match in re.finditer(r'<img\b([^>]*)>', content):
                if 'alt=' not in img_match.group(1):
                    issues.append(f"{name}: <img> missing alt attribute")

        # Check robots.txt exists
        if not (output_dir / "robots.txt").exists():
            issues.append("Missing robots.txt")

        return issues


# ----- CLI -----

def main():
    """CLI entry point for testing."""
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if len(sys.argv) < 2:
        print("Usage: python -m cohort.website_creator.pipeline <site_brief.yaml>")
        print("       python -m cohort.website_creator.pipeline --demo")
        print("       python -m cohort.website_creator.pipeline --graduate <project> <dest>")
        print("       python -m cohort.website_creator.pipeline --preview <site_brief.yaml>")
        print("       python -m cohort.website_creator.pipeline --deploy <output_dir> [--project-name <name>]")
        sys.exit(1)

    output_base = Path(__file__).parent / "output"
    creator = WebsiteCreator(output_base=output_base)

    if sys.argv[1] == "--deploy":
        # Deploy a generated site to Cloudflare Pages
        if len(sys.argv) < 3:
            print("Usage: --deploy <output_dir> [--project-name <name>]")
            sys.exit(1)
        from cohort.website_creator.deploy import deploy_to_cloudflare_pages
        project_name = None
        if "--project-name" in sys.argv:
            idx = sys.argv.index("--project-name")
            if idx + 1 < len(sys.argv):
                project_name = sys.argv[idx + 1]
        result = deploy_to_cloudflare_pages(Path(sys.argv[2]), project_name=project_name)
        if result["status"] == "success":
            print(f"\n[OK] Deployed: {result.get('url', 'check dashboard')}")
        else:
            print(f"\n[X] Failed: {result.get('error', 'unknown')}")
            sys.exit(1)

    elif sys.argv[1] == "--graduate":
        if len(sys.argv) < 4:
            print("Usage: --graduate <project_name> <destination_dir>")
            sys.exit(1)
        result = creator.graduate(sys.argv[2], Path(sys.argv[3]))
        print(f"\n[OK] Graduated: {result}")

    elif sys.argv[1] == "--preview":
        if len(sys.argv) < 3:
            print("Usage: --preview <site_brief.yaml>")
            sys.exit(1)
        result = asyncio.run(creator.preview(sys.argv[2]))
        print(f"\n[OK] Preview rendered: {result}")
        print(f"     Open {result / 'index.html'} in your browser.")

    elif sys.argv[1] == "--demo":
        brief_path = Path(__file__).parent / "examples" / "cohort_site_brief.yaml"
        if not brief_path.exists():
            print(f"Demo brief not found: {brief_path}")
            sys.exit(1)
        result = asyncio.run(creator.create_from_yaml(brief_path))
        print(f"\n[OK] Site generated: {result}")
        print(f"     Open {result / 'index.html'} in your browser.")

    else:
        brief_path = Path(sys.argv[1])
        result = asyncio.run(creator.create_from_yaml(brief_path))
        print(f"\n[OK] Site generated: {result}")
        print(f"     Open {result / 'index.html'} in your browser.")


if __name__ == "__main__":
    main()
