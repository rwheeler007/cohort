"""CLI: Render a block-style site spec to HTML.

Usage:
    python -m cohort.website_creator.render_blocks examples/service_business_demo.yaml output/service_demo
    python -m cohort.website_creator.render_blocks examples/restaurant_demo.yaml output/restaurant_demo
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cohort.website_creator.block_renderer import BlockRenderer


def main():
    parser = argparse.ArgumentParser(description="Render a block-style site spec to HTML")
    parser.add_argument("spec", help="Path to site spec YAML file")
    parser.add_argument("output", help="Output directory for generated HTML")
    parser.add_argument("--blocks-dir", help="Custom blocks directory (default: built-in)")
    args = parser.parse_args()

    spec_path = Path(args.spec)
    if not spec_path.exists():
        print(f"[X] Spec file not found: {spec_path}")
        sys.exit(1)

    blocks_dir = Path(args.blocks_dir) if args.blocks_dir else None
    renderer = BlockRenderer(blocks_dir=blocks_dir)

    output = renderer.render(spec_path, args.output)

    # Count generated files
    html_files = list(output.glob("*.html"))
    print(f"[OK] Rendered {len(html_files)} page(s) to {output}")
    for f in sorted(html_files):
        print(f"     {f.name}")


if __name__ == "__main__":
    main()
