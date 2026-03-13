"""Tests for the website creator decision engine pipeline.

Covers:
- 3 E2E tests (service_business, restaurant, saas_product) using dry-run mode
- 4 unit tests for YAML parsing, fallback content, and dry-run LLM isolation
"""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch

from cohort.website_creator.decision_engine import (
    DecisionEngine,
    EngineConfig,
    _fallback_content,
    _parse_yaml_response,
)
from cohort.website_creator.roundtable_bridge import (
    BlockSiteSpec,
    BusinessInfo,
)
from cohort.website_creator.block_renderer import BlockRenderer


# =====================================================================
# Helpers
# =====================================================================

def _run_dry_engine(
    name: str,
    description: str,
    action: str = "learn",
    phone: str = "",
    email: str = "",
    address: str = "",
    tagline: str = "",
) -> BlockSiteSpec:
    """Run the decision engine in dry-run mode and return the spec."""
    config = EngineConfig(dry_run=True)
    engine = DecisionEngine(config)
    return engine.run(
        description=description,
        business_name=name,
        tagline=tagline,
        phone=phone,
        email=email,
        address=address,
        primary_action=action,
    )


def _render_spec(spec: BlockSiteSpec, output_dir: Path) -> dict[str, str]:
    """Render a spec to HTML files and return {filename: html_content}."""
    renderer = BlockRenderer()
    renderer.render(spec.to_render_dict(), output_dir)
    result = {}
    for html_file in output_dir.glob("*.html"):
        result[html_file.name] = html_file.read_text(encoding="utf-8")
    return result


def _assert_html_structure(html: str, business_name: str) -> None:
    """Assert basic HTML structure and business name presence."""
    assert len(html) > 0, "HTML file is empty"
    assert "<html" in html.lower(), "Missing <html> tag"
    assert "<head" in html.lower(), "Missing <head> tag"
    assert "<body" in html.lower(), "Missing <body> tag"
    assert business_name in html, f"Business name '{business_name}' not found in HTML"


# =====================================================================
# E2E Tests (dry-run mode -- no LLM calls)
# =====================================================================

class TestE2EServiceBusiness:
    """Test 1: E2E pipeline for a service business (plumber)."""

    def test_e2e_service_business(self, tmp_path: Path) -> None:
        name = "Power Plumbing Co."
        spec = _run_dry_engine(
            name=name,
            description="Portland plumber since 1979. 30 trucks serving residential and commercial customers. Licensed and insured.",
            action="call",
            phone="(503) 244-1900",
            email="service@powerplumbing.com",
            address="6611 SW Multnomah Blvd, Portland, OR 97223",
        )

        # Should produce 4 pages: index, services, about, contact
        page_slugs = [p.slug for p in spec.pages]
        assert "index" in page_slugs
        assert "services" in page_slugs
        assert "about" in page_slugs
        assert "contact" in page_slugs
        assert len(spec.pages) == 4

        # Render to HTML
        files = _render_spec(spec, tmp_path / "output")

        expected_files = {"index.html", "services.html", "about.html", "contact.html"}
        assert set(files.keys()) == expected_files

        for filename, html in files.items():
            _assert_html_structure(html, name)


class TestE2ERestaurant:
    """Test 2: E2E pipeline for a restaurant."""

    @patch("cohort.website_creator.decision_engine.classify_category", return_value="restaurant")
    def test_e2e_restaurant(self, mock_classify, tmp_path: Path) -> None:
        name = "Mama Rosa's Trattoria"
        spec = _run_dry_engine(
            name=name,
            description="Authentic Italian restaurant in the heart of downtown. Family recipes passed down for three generations. Fresh pasta made daily.",
            action="book",
            phone="(212) 555-0100",
            email="reservations@mamarosas.com",
            address="123 Main St, New York, NY 10001",
        )

        # Restaurant category: home, menu, about, contact
        page_slugs = [p.slug for p in spec.pages]
        assert "index" in page_slugs
        assert "menu" in page_slugs
        assert "about" in page_slugs
        assert "contact" in page_slugs
        assert len(spec.pages) == 4

        # Render to HTML
        files = _render_spec(spec, tmp_path / "output")

        expected_files = {"index.html", "menu.html", "about.html", "contact.html"}
        assert set(files.keys()) == expected_files

        for filename, html in files.items():
            _assert_html_structure(html, name)

        # Restaurant-specific: menu page should exist and contain the business name
        assert name in files["menu.html"]


class TestE2ESaaSProduct:
    """Test 3: E2E pipeline for a SaaS product."""

    @patch("cohort.website_creator.decision_engine.classify_category", return_value="saas_product")
    def test_e2e_saas_product(self, mock_classify, tmp_path: Path) -> None:
        name = "TaskFlow"
        spec = _run_dry_engine(
            name=name,
            description="TaskFlow is a project management app for small teams. Kanban boards, time tracking, and team chat in one platform.",
            action="buy",
            email="hello@taskflow.io",
        )

        # SaaS category: home, features, pricing, contact
        page_slugs = [p.slug for p in spec.pages]
        assert "index" in page_slugs
        assert "features" in page_slugs
        assert "pricing" in page_slugs
        assert "contact" in page_slugs
        assert len(spec.pages) == 4

        # Render to HTML
        files = _render_spec(spec, tmp_path / "output")

        expected_files = {"index.html", "features.html", "pricing.html", "contact.html"}
        assert set(files.keys()) == expected_files

        for filename, html in files.items():
            _assert_html_structure(html, name)

        # SaaS-specific: features page should contain business name
        assert name in files["features.html"]


# =====================================================================
# Unit Tests
# =====================================================================

class TestYamlParsing:
    """Tests 4 & 5: YAML response parsing from LLM output."""

    def test_yaml_parsing_malformed(self) -> None:
        """Test 4: Malformed YAML input returns None (graceful handling)."""
        # Completely broken YAML
        assert _parse_yaml_response("this is not yaml: [[[") is None

        # Empty string
        assert _parse_yaml_response("") is None

        # YAML that parses but is not a dict (e.g. a list)
        assert _parse_yaml_response("- item1\n- item2") is None

        # YAML that is just a scalar
        assert _parse_yaml_response("just a string") is None

        # Truncated code block
        assert _parse_yaml_response("```yaml\nkey: value\n") is not None  # partial block still parseable

        # Nested broken YAML inside code block
        assert _parse_yaml_response("```yaml\n{[bad yaml\n```") is None

    def test_yaml_parsing_code_block(self) -> None:
        """Test 5: YAML wrapped in code blocks is correctly extracted."""
        # Standard ```yaml``` block
        text = '```yaml\ntagline: "Best Plumber"\nhero_headline: "We Fix It"\n```'
        result = _parse_yaml_response(text)
        assert result is not None
        assert result["tagline"] == "Best Plumber"
        assert result["hero_headline"] == "We Fix It"

        # Code block without yaml language tag
        text2 = '```\ntagline: "No Tag"\n```'
        result2 = _parse_yaml_response(text2)
        assert result2 is not None
        assert result2["tagline"] == "No Tag"

        # With surrounding commentary text
        text3 = 'Here is the YAML:\n```yaml\nkey: value\nnested:\n  inner: data\n```\nHope that helps!'
        result3 = _parse_yaml_response(text3)
        assert result3 is not None
        assert result3["key"] == "value"
        assert result3["nested"]["inner"] == "data"

        # Raw YAML without code fences
        text4 = 'tagline: "Raw YAML"\nhero_headline: "Direct"'
        result4 = _parse_yaml_response(text4)
        assert result4 is not None
        assert result4["tagline"] == "Raw YAML"


class TestFallbackContent:
    """Test 6: Fallback content returns complete, non-empty defaults."""

    def test_fallback_content_all_blocks(self) -> None:
        """All categories produce complete fallback content with no empty required values."""
        categories_and_pages = {
            "service_business": ["home", "services", "about", "contact"],
            "saas_product": ["home", "features", "pricing", "contact"],
            "restaurant": ["home", "menu", "about", "contact"],
        }

        for category, pages in categories_and_pages.items():
            biz = BusinessInfo(
                name=f"Test {category.title()} Biz",
                description=f"A test {category} for validation.",
                primary_action="learn",
            )

            content = _fallback_content(biz, category, pages)

            # Required top-level keys must exist and be non-empty
            assert content["tagline"], f"Empty tagline for {category}"
            assert content["hero_headline"], f"Empty hero_headline for {category}"
            assert content["hero_subheadline"], f"Empty hero_subheadline for {category}"
            assert content["cta_primary_text"], f"Empty cta_primary_text for {category}"
            assert content["cta_secondary_text"], f"Empty cta_secondary_text for {category}"
            assert content["cta_heading"], f"Empty cta_heading for {category}"
            assert content["cta_subheading"], f"Empty cta_subheading for {category}"

            # Nested dicts must be present
            assert isinstance(content["section_headings"], dict)
            assert isinstance(content["meta_descriptions"], dict)
            assert isinstance(content["page_titles"], dict)

            # Each page should have a title and meta description
            for page in pages:
                slug = "index" if page == "home" else page
                assert slug in content["page_titles"], (
                    f"Missing page_title for {slug} in {category}"
                )
                assert slug in content["meta_descriptions"], (
                    f"Missing meta_description for {slug} in {category}"
                )
                assert content["page_titles"][slug], (
                    f"Empty page_title for {slug} in {category}"
                )
                assert content["meta_descriptions"][slug], (
                    f"Empty meta_description for {slug} in {category}"
                )


class TestDryRunNoLLMCalls:
    """Test 7: Dry-run mode never makes HTTP calls to Ollama."""

    def test_dry_run_no_llm_calls(self) -> None:
        """Running with dry_run=True must not make any httpx calls."""
        import httpx as real_httpx

        original_client = real_httpx.Client

        calls = []

        def spy_client(*args, **kwargs):
            calls.append(("Client", args, kwargs))
            raise RuntimeError(
                "httpx.Client was called during dry-run -- LLM call leaked!"
            )

        with patch.object(real_httpx, "Client", side_effect=spy_client):
            config = EngineConfig(dry_run=True)
            engine = DecisionEngine(config)

            spec = engine.run(
                description="A local bakery specializing in sourdough bread and pastries.",
                business_name="Golden Crust Bakery",
                primary_action="order",
                phone="(415) 555-0200",
            )

        # Pipeline should complete successfully without any HTTP calls
        assert type(spec).__name__ == "BlockSiteSpec"
        assert len(spec.pages) > 0
        assert spec.site["name"] == "Golden Crust Bakery"

        # Verify: httpx.Client was never called
        assert len(calls) == 0, "httpx.Client was called during dry-run"

        # The spec should produce valid YAML
        yaml_output = spec.to_yaml()
        assert len(yaml_output) > 100, "YAML output suspiciously short"
        assert "Golden Crust Bakery" in yaml_output
