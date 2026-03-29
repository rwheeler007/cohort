"""Tests for cohort.permissions -- tier detection, gating, and browser config."""


import pytest

from cohort.permissions import (
    PermissionTier,
    browser_allow_local,
    browser_allowlist,
    get_tier,
    require_tier,
    reset_tier_cache,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Reset the cached tier before and after every test."""
    reset_tier_cache()
    yield
    reset_tier_cache()


# =====================================================================
# get_tier
# =====================================================================


class TestGetTier:
    def test_default_bare_metal(self, monkeypatch):
        """No env var + no Docker -> unrestricted."""
        monkeypatch.delenv("COHORT_TIER", raising=False)
        monkeypatch.delenv("COHORT_DOCKER", raising=False)
        # /.dockerenv won't exist on the test host
        assert get_tier() == PermissionTier.UNRESTRICTED

    def test_explicit_sandbox(self, monkeypatch):
        monkeypatch.setenv("COHORT_TIER", "sandbox")
        assert get_tier() == PermissionTier.SANDBOX

    def test_explicit_local(self, monkeypatch):
        monkeypatch.setenv("COHORT_TIER", "local")
        assert get_tier() == PermissionTier.LOCAL

    def test_explicit_unrestricted(self, monkeypatch):
        monkeypatch.setenv("COHORT_TIER", "unrestricted")
        assert get_tier() == PermissionTier.UNRESTRICTED

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("COHORT_TIER", "SANDBOX")
        assert get_tier() == PermissionTier.SANDBOX

    def test_whitespace_stripped(self, monkeypatch):
        monkeypatch.setenv("COHORT_TIER", "  local  ")
        assert get_tier() == PermissionTier.LOCAL

    def test_unknown_value_defaults_sandbox(self, monkeypatch):
        monkeypatch.setenv("COHORT_TIER", "bogus")
        assert get_tier() == PermissionTier.SANDBOX

    def test_docker_env_var_triggers_sandbox(self, monkeypatch):
        monkeypatch.delenv("COHORT_TIER", raising=False)
        monkeypatch.setenv("COHORT_DOCKER", "1")
        assert get_tier() == PermissionTier.SANDBOX

    def test_caching(self, monkeypatch):
        monkeypatch.setenv("COHORT_TIER", "local")
        assert get_tier() == PermissionTier.LOCAL
        # Change env var -- cached value should persist
        monkeypatch.setenv("COHORT_TIER", "sandbox")
        assert get_tier() == PermissionTier.LOCAL

    def test_reset_clears_cache(self, monkeypatch):
        monkeypatch.setenv("COHORT_TIER", "local")
        assert get_tier() == PermissionTier.LOCAL
        reset_tier_cache()
        monkeypatch.setenv("COHORT_TIER", "sandbox")
        assert get_tier() == PermissionTier.SANDBOX


# =====================================================================
# require_tier
# =====================================================================


class TestRequireTier:
    def test_sandbox_meets_sandbox(self, monkeypatch):
        monkeypatch.setenv("COHORT_TIER", "sandbox")
        assert require_tier(PermissionTier.SANDBOX) is True

    def test_sandbox_does_not_meet_local(self, monkeypatch):
        monkeypatch.setenv("COHORT_TIER", "sandbox")
        assert require_tier(PermissionTier.LOCAL) is False

    def test_sandbox_does_not_meet_unrestricted(self, monkeypatch):
        monkeypatch.setenv("COHORT_TIER", "sandbox")
        assert require_tier(PermissionTier.UNRESTRICTED) is False

    def test_local_meets_sandbox(self, monkeypatch):
        monkeypatch.setenv("COHORT_TIER", "local")
        assert require_tier(PermissionTier.SANDBOX) is True

    def test_local_meets_local(self, monkeypatch):
        monkeypatch.setenv("COHORT_TIER", "local")
        assert require_tier(PermissionTier.LOCAL) is True

    def test_local_does_not_meet_unrestricted(self, monkeypatch):
        monkeypatch.setenv("COHORT_TIER", "local")
        assert require_tier(PermissionTier.UNRESTRICTED) is False

    def test_unrestricted_meets_all(self, monkeypatch):
        monkeypatch.setenv("COHORT_TIER", "unrestricted")
        assert require_tier(PermissionTier.SANDBOX) is True
        assert require_tier(PermissionTier.LOCAL) is True
        assert require_tier(PermissionTier.UNRESTRICTED) is True


# =====================================================================
# browser_allowlist / browser_allow_local
# =====================================================================


class TestBrowserConfig:
    def test_sandbox_allowlist_is_localhost(self, monkeypatch):
        monkeypatch.setenv("COHORT_TIER", "sandbox")
        al = browser_allowlist()
        assert al is not None
        assert "localhost" in al
        assert "127.0.0.1" in al

    def test_local_allowlist_is_none(self, monkeypatch):
        monkeypatch.setenv("COHORT_TIER", "local")
        assert browser_allowlist() is None

    def test_unrestricted_allowlist_is_none(self, monkeypatch):
        monkeypatch.setenv("COHORT_TIER", "unrestricted")
        assert browser_allowlist() is None

    def test_sandbox_allows_local(self, monkeypatch):
        """Sandbox needs localhost for in-container pages."""
        monkeypatch.setenv("COHORT_TIER", "sandbox")
        assert browser_allow_local() is True

    def test_local_blocks_local(self, monkeypatch):
        """Local tier blocks RFC1918 by default."""
        monkeypatch.setenv("COHORT_TIER", "local")
        assert browser_allow_local() is False

    def test_unrestricted_allows_local(self, monkeypatch):
        monkeypatch.setenv("COHORT_TIER", "unrestricted")
        assert browser_allow_local() is True
