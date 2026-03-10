"""Cohort Website Creator -- multi-agent website generation pipeline.

YAML-in, HTML-out. LLMs make design/content decisions via compiled
roundtables; Jinja2 templates render the results into static HTML.

Usage:
    from cohort.website_creator import WebsiteCreator
    creator = WebsiteCreator()
    output_dir = await creator.create("site_brief.yaml")
"""

from cohort.website_creator.site_brief import SiteBrief
from cohort.website_creator.renderer import TemplateRenderer
from cohort.website_creator.pipeline import WebsiteCreator

__all__ = ["SiteBrief", "TemplateRenderer", "WebsiteCreator"]
