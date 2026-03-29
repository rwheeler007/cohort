"""Deploy -- push generated sites to Cloudflare Pages or other hosts.

Usage:
    python -m cohort.website_creator.deploy <output_dir> [--project-name my-site]
    python -m cohort.website_creator.deploy output/joes-plumbing --project-name joes-plumbing

Requires: wrangler CLI installed and authenticated (`npx wrangler login`).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("cohort.website_creator.deploy")


def find_wrangler() -> str | None:
    """Find the wrangler CLI binary."""
    # Try direct path first
    wrangler = shutil.which("wrangler")
    if wrangler:
        return wrangler
    # Try via npx
    npx = shutil.which("npx")
    if npx:
        return f"{npx} wrangler"
    return None


def deploy_to_cloudflare_pages(
    output_dir: str | Path,
    project_name: str | None = None,
    branch: str = "production",
) -> dict:
    """Deploy a generated site to Cloudflare Pages.

    Args:
        output_dir: Path to the generated site directory (contains index.html).
        project_name: Cloudflare Pages project name. Auto-created if it doesn't exist.
        branch: Branch name for deployment. "production" = live site.

    Returns:
        Dict with deployment info (url, project_name, status).
    """
    output_dir = Path(output_dir)
    if not output_dir.exists():
        raise FileNotFoundError(f"Output directory not found: {output_dir}")
    if not (output_dir / "index.html").exists():
        raise FileNotFoundError(f"No index.html in {output_dir} -- is this a generated site?")

    wrangler = find_wrangler()
    if not wrangler:
        raise RuntimeError(
            "wrangler CLI not found. Install with: npm install -g wrangler\n"
            "Then authenticate with: wrangler login"
        )

    # Derive project name from directory if not given
    if not project_name:
        project_name = output_dir.name

    # Sanitize project name (Cloudflare Pages rules: lowercase, alphanumeric + hyphens)
    project_name = project_name.lower().replace(" ", "-").replace("_", "-")

    log.info("Deploying %s to Cloudflare Pages project '%s'...", output_dir, project_name)

    # Build the command
    cmd = f"{wrangler} pages deploy \"{output_dir}\" --project-name {project_name} --branch {branch}"

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode == 0:
            # Parse the deployment URL from output
            url = _extract_url(result.stdout)
            log.info("[OK] Deployed to: %s", url or "check Cloudflare dashboard")
            return {
                "status": "success",
                "project_name": project_name,
                "url": url,
                "stdout": result.stdout,
            }
        else:
            log.error("Deployment failed:\n%s\n%s", result.stdout, result.stderr)
            return {
                "status": "error",
                "project_name": project_name,
                "error": result.stderr or result.stdout,
            }
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "project_name": project_name,
            "error": "Deployment timed out after 120 seconds",
        }


def _extract_url(stdout: str) -> str:
    """Extract the deployment URL from wrangler output."""
    import re
    # wrangler pages deploy outputs: "Deployment complete! https://xxxx.pages.dev"
    match = re.search(r"(https://[^\s]+\.pages\.dev[^\s]*)", stdout)
    if match:
        return match.group(1)
    # Also check for custom domain
    match = re.search(r"(https://[^\s]+)", stdout)
    return match.group(1) if match else ""


# ----- CLI -----

def main():
    """CLI entry point."""
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="Deploy a generated website to Cloudflare Pages")
    parser.add_argument("output_dir", type=Path, help="Path to the generated site directory")
    parser.add_argument("--project-name", "-p", help="Cloudflare Pages project name (default: directory name)")
    parser.add_argument("--branch", "-b", default="production", help="Branch name (default: production)")

    args = parser.parse_args()

    result = deploy_to_cloudflare_pages(
        args.output_dir,
        project_name=args.project_name,
        branch=args.branch,
    )

    if result["status"] == "success":
        print(f"\n[OK] Site deployed: {result.get('url', 'check dashboard')}")
        print(f"     Project: {result['project_name']}")
    else:
        print(f"\n[X] Deployment failed: {result.get('error', 'unknown error')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
