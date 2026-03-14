"""CLI entry point for the decision engine pipeline.

Usage:
    # Full pipeline: description -> rendered site
    python -m cohort.website_creator.run_engine \
        --name "Power Plumbing Co." \
        --description "Portland plumber since 1979. 30 trucks, residential and commercial." \
        --phone "(503) 244-1900" \
        --email "service@powerplumbingco.com" \
        --address "6611 SW Multnomah Blvd, Portland, OR 97223" \
        --action call \
        --output g:/tmp/power_plumbing_engine

    # Dry run (no LLM calls, prints site spec YAML to stdout)
    python -m cohort.website_creator.run_engine \
        --name "Power Plumbing Co." \
        --description "Portland plumber since 1979." \
        --dry-run

    # With worksheet questions (YAML file)
    python -m cohort.website_creator.run_engine \
        --name "Power Plumbing Co." \
        --description "Portland plumber since 1979." \
        --questions worksheet.yaml \
        --output g:/tmp/power_plumbing_engine

    # With worksheet questions (inline YAML)
    python -m cohort.website_creator.run_engine \
        --name "Power Plumbing Co." \
        --description "Portland plumber since 1979." \
        -q '{differentiator: "30 years experience, 30 trucks", ideal_customer: "Portland homeowners", top_services: "drain cleaning, water heaters, remodels", service_area: "Portland metro"}' \
        --output g:/tmp/power_plumbing_engine

    # Control page count (1-5, default 4)
    python -m cohort.website_creator.run_engine \
        --name "Power Plumbing Co." \
        --description "Portland plumber since 1979." \
        --pages 2 \
        --output g:/tmp/power_plumbing_engine

    # With competitor site HTML files
    python -m cohort.website_creator.run_engine \
        --name "TaskFlow" \
        --description "Project management app for small teams" \
        --action buy \
        --competitor-html competitor1.html \
        --competitor-html competitor2.html \
        --output g:/tmp/taskflow_engine
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import yaml
from pathlib import Path

from .decision_engine import DecisionEngine, EngineConfig
from .block_renderer import BlockRenderer


# ---------------------------------------------------------------------------
# Worksheet questions (MVP for small business websites)
# ---------------------------------------------------------------------------

WORKSHEET_KEYS = {
    "call_to_action": "What is your primary call-to-action? (call, book, buy, signup)",
    "differentiator": "What makes you different from competitors?",
    "ideal_customer": "Who is your ideal customer?",
    "top_services": "What are your top 3 services/products?",
    "service_area": "What is your service area or location?",
}


def _load_questions(questions_arg: str) -> dict[str, str]:
    """Load worksheet questions from a YAML file path or inline YAML string.

    Returns a dict with keys from WORKSHEET_KEYS. Missing keys are omitted.
    """
    path = Path(questions_arg)
    if path.exists() and path.is_file():
        raw = path.read_text(encoding="utf-8")
    else:
        # Treat as inline YAML
        raw = questions_arg

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        print(f"[!] Failed to parse questions YAML: {e}")
        return {}

    if not isinstance(data, dict):
        print("[!] Questions must be a YAML mapping (key: value pairs)")
        return {}

    # Accept only known keys, warn about unknown ones
    result = {}
    for key, value in data.items():
        if key in WORKSHEET_KEYS:
            result[key] = str(value)
        else:
            print(f"[!] Unknown worksheet key: {key} (ignored)")

    return result


def _build_questions_context(questions: dict[str, str]) -> str:
    """Format worksheet answers into a context block for the content prompt."""
    if not questions:
        return ""

    lines = ["Worksheet answers from the business owner:"]
    label_map = {
        "call_to_action": "Primary call-to-action",
        "differentiator": "What makes them different",
        "ideal_customer": "Ideal customer",
        "top_services": "Top services/products",
        "service_area": "Service area/location",
    }
    for key, value in questions.items():
        label = label_map.get(key, key)
        lines.append(f"  {label}: {value}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Progress printing
# ---------------------------------------------------------------------------

def _progress(step: int, total: int, msg: str) -> float:
    """Print a progress line and return the current monotonic time."""
    print(f"[{step}/{total}] {msg}")
    return time.monotonic()


def _elapsed(t0: float) -> str:
    """Format elapsed time since t0."""
    ms = int((time.monotonic() - t0) * 1000)
    if ms >= 1000:
        return f"{ms / 1000:.1f}s"
    return f"{ms}ms"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Website Creator Decision Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--name", required=True, help="Business name")
    parser.add_argument("--description", required=True, help="Business description")
    parser.add_argument("--tagline", default="", help="Business tagline")
    parser.add_argument("--phone", default="", help="Phone number")
    parser.add_argument("--email", default="", help="Email address")
    parser.add_argument("--address", default="", help="Physical address")
    parser.add_argument("--action", default="learn",
                        choices=["call", "book", "buy", "learn", "order"],
                        help="Primary user action")
    parser.add_argument("--output", "-o", default="",
                        help="Output directory for rendered HTML")
    parser.add_argument("--save-yaml", default="",
                        help="Save the generated site spec YAML to this path")
    parser.add_argument("--competitor-html", action="append", default=[],
                        help="Path to competitor site HTML file (can specify multiple)")
    parser.add_argument("--user-site-html", default="",
                        help="Path to user's existing site HTML")
    parser.add_argument("--questions", "-q", default="",
                        help="Worksheet questions as YAML file path or inline YAML string")
    parser.add_argument("--pages", type=int, default=0,
                        help="Number of pages to generate (1-5, default: all from taxonomy)")

    # Engine config
    parser.add_argument("--ollama-url", default="http://localhost:11434",
                        help="Ollama API URL")
    parser.add_argument("--tier1-model", default="qwen3.5:2b",
                        help="Tier 1 classification model")
    parser.add_argument("--tier2-model", default="qwen3.5:9b",
                        help="Tier 2 generation model")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip LLM calls, print site spec YAML and exit")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print LLM prompts, response lengths, and model load times")

    args = parser.parse_args(argv)

    # Validate --pages
    if args.pages != 0 and not (1 <= args.pages <= 5):
        print("[!] --pages must be between 1 and 5")
        return 1

    # Setup logging -- verbose gets DEBUG, normal gets WARNING (progress prints handle INFO)
    level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Load worksheet questions
    questions = {}
    if args.questions:
        questions = _load_questions(args.questions)
        if questions:
            print(f"[*] Loaded {len(questions)} worksheet answer(s)")
            if args.verbose:
                for k, v in questions.items():
                    print(f"    {k}: {v}")

    # If worksheet provides call_to_action and --action was not explicitly set,
    # use the worksheet value
    action = args.action
    if questions.get("call_to_action") and action == "learn":
        cta = questions["call_to_action"].lower().strip()
        for valid in ("call", "book", "buy", "order"):
            if valid in cta:
                action = valid
                break

    config = EngineConfig(
        ollama_url=args.ollama_url,
        tier1_model=args.tier1_model,
        tier2_model=args.tier2_model,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    # Load competitor HTML if provided
    competitor_html = []
    for path_str in args.competitor_html:
        p = Path(path_str)
        if p.exists():
            html = p.read_text(encoding="utf-8", errors="replace")
            competitor_html.append((str(p), html))
        else:
            print(f"[!] Competitor HTML not found: {path_str}")

    user_site_html = None
    if args.user_site_html:
        p = Path(args.user_site_html)
        if p.exists():
            user_site_html = p.read_text(encoding="utf-8", errors="replace")
        else:
            print(f"[!] User site HTML not found: {args.user_site_html}")

    # Enrich description with worksheet answers for the engine
    description = args.description
    questions_context = _build_questions_context(questions)

    # ── Run the engine with progress output ──
    t_total = time.monotonic()

    print(f"\n[>>] Decision engine: {args.name}")
    if args.dry_run:
        print("[*] Dry run mode -- no LLM calls")
    if args.pages:
        print(f"[*] Page limit: {args.pages}")
    print()

    engine = DecisionEngine(config)

    # Step 1: Classify
    t0 = _progress(1, 4, "Classifying business category...")
    from .decision_engine import classify_category
    category = classify_category(description, config)
    print(f"    -> {category} ({_elapsed(t0)})")

    # Step 2: Taste profile (site analysis + merge)
    t0 = _progress(2, 4, "Generating taste profile...")
    from .decision_engine import (
        analyze_site_html, merge_taste_profiles,
    )
    from .roundtable_bridge import RoundtableBridge
    bridge = RoundtableBridge()
    taxonomy = bridge.taxonomy
    cat_def = taxonomy["categories"][category]
    cat_bias = cat_def.get("skin_bias", {})

    competitors = []
    if competitor_html:
        for i, (url, html) in enumerate(competitor_html):
            if args.verbose:
                print(f"    Analyzing competitor {i + 1}: {url}")
            profile = analyze_site_html(html, config, site_label=f"comp{i}")
            profile.url = url
            competitors.append(profile)

    user_site_profile = None
    if user_site_html:
        if args.verbose:
            print("    Analyzing user's existing site...")
        user_site_profile = analyze_site_html(user_site_html, config, site_label="user_site")

    taste = merge_taste_profiles(cat_bias, competitors, user_site_profile)
    print(f"    -> formality={taste.formality:.2f} density={taste.density:.2f} "
          f"warmth={taste.warmth:.2f} creativity={taste.creativity:.2f} "
          f"boldness={taste.boldness:.2f} ({_elapsed(t0)})")

    # Step 3: Content generation
    t0 = _progress(3, 4, "Generating content...")
    from .decision_engine import generate_content
    from .roundtable_bridge import BusinessInfo

    biz = BusinessInfo(
        name=args.name,
        tagline=args.tagline,
        description=description,
        primary_action=action,
        phone=args.phone,
        email=args.email,
        address=args.address,
    )

    # Inject worksheet context into the engine's content generation
    # by temporarily enriching the description
    if questions_context:
        biz_for_content = biz.model_copy()
        biz_for_content.description = f"{description}\n\n{questions_context}"
    else:
        biz_for_content = biz

    content = generate_content(biz_for_content, category, taste, config)
    print(f"    -> {len(content)} content fields ({_elapsed(t0)})")
    if args.verbose:
        for key in sorted(content.keys()):
            val = content[key]
            if isinstance(val, str):
                print(f"    {key}: {val[:80]}{'...' if len(val) > 80 else ''}")
            elif isinstance(val, dict):
                print(f"    {key}: ({len(val)} entries)")
            elif isinstance(val, list):
                print(f"    {key}: ({len(val)} items)")

    # Step 4: Render pages
    t0 = _progress(4, 4, "Rendering pages...")
    from .roundtable_bridge import RoundtableInput

    bridge_input = RoundtableInput(
        business=biz,
        category=category,
        taste_profile=taste,
        competitors=competitors,
        user_site_profile=user_site_profile,
    )

    spec = bridge.build(bridge_input, content=content)

    # Apply --pages limit: keep first N pages (home is always first)
    if args.pages and len(spec.pages) > args.pages:
        spec.pages = spec.pages[:args.pages]

    total_blocks = sum(len(p.blocks) for p in spec.pages)
    print(f"    -> {len(spec.pages)} pages, {total_blocks} blocks ({_elapsed(t0)})")

    # ── Summary ──
    total_ms = int((time.monotonic() - t_total) * 1000)
    print(f"\n[OK] Pipeline complete in {_elapsed(t_total)}")
    print(f"  Pages: {len(spec.pages)}")
    for page in spec.pages:
        print(f"    {page.slug}.html ({len(page.blocks)} blocks)")
    print(f"  Skin: palette={spec.skin.palette_id}, font={spec.skin.font_pairing_id}")

    # ── Dry run: print YAML and exit ──
    if args.dry_run:
        print("\n--- Site Spec (YAML) ---")
        print(spec.to_yaml())
        return 0

    # Save YAML if requested
    if args.save_yaml:
        spec.save_yaml(args.save_yaml)
        print(f"\n[OK] Spec saved to {args.save_yaml}")

    # Render if output dir specified
    if args.output:
        renderer = BlockRenderer()
        output_path = renderer.render(spec.to_render_dict(), args.output)
        print(f"[OK] Site rendered to {output_path}")
        for page in spec.pages:
            print(f"  {output_path / (page.slug + '.html')}")
    elif not args.save_yaml:
        # Print YAML to stdout if no output specified
        print("\n--- Generated Site Spec ---")
        print(spec.to_yaml())

    return 0


if __name__ == "__main__":
    sys.exit(main())
