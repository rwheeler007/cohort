"""Tests that license declarations are consistent across all project files.

Catches drift between LICENSE, pyproject.toml, Dockerfile, website, and
structured data. The canonical license is Apache-2.0.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_LICENSE = "Apache-2.0"
EXPECTED_SPDX = "Apache-2.0"


class TestLicenseConsistency:
    """Every file that declares a license must agree on Apache-2.0."""

    def test_license_file_is_apache(self):
        """LICENSE file must contain the Apache 2.0 header."""
        license_text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
        assert "Apache License" in license_text
        assert "Version 2.0" in license_text
        assert "MIT" not in license_text, "LICENSE file still contains MIT text"

    def test_pyproject_license_field(self):
        """pyproject.toml license field must be Apache-2.0."""
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert f'license = {{text = "{EXPECTED_SPDX}"}}' in pyproject

    def test_pyproject_classifier(self):
        """pyproject.toml classifiers must list Apache, not MIT."""
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert "Apache Software License" in pyproject
        assert "MIT License" not in pyproject, (
            "pyproject.toml classifier still references MIT"
        )

    def test_dockerfile_label(self):
        """Dockerfile LABEL license must be Apache-2.0."""
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
        match = re.search(r'LABEL\s+license="([^"]+)"', dockerfile)
        assert match is not None, "Dockerfile has no license LABEL"
        assert match.group(1) == EXPECTED_SPDX, (
            f"Dockerfile license is '{match.group(1)}', expected '{EXPECTED_SPDX}'"
        )

    def test_download_page_license_text(self):
        """Website download page must reference Apache 2.0."""
        download_html = REPO_ROOT / "cohort" / "website" / "cohort" / "download.html"
        if not download_html.exists():
            pytest.skip("download.html not found in repo")
        text = download_html.read_text(encoding="utf-8")
        assert "Apache 2.0" in text or "Apache-2.0" in text, (
            "download.html does not mention Apache 2.0"
        )
        assert "MIT License" not in text, (
            "download.html still references MIT"
        )

    def test_structured_data_no_mit(self):
        """JSON-LD structured data on download page must not say MIT."""
        download_html = REPO_ROOT / "cohort" / "website" / "cohort" / "download.html"
        if not download_html.exists():
            pytest.skip("download.html not found in repo")
        text = download_html.read_text(encoding="utf-8")
        # Look inside <script type="application/ld+json"> blocks
        ld_blocks = re.findall(
            r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>',
            text,
            re.DOTALL,
        )
        for block in ld_blocks:
            assert "MIT" not in block, (
                "Structured data (JSON-LD) still references MIT"
            )
