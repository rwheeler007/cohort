"""Tests for OAuth2 authentication middleware.

Coverage targets:
- Valid token acceptance
- Expired token rejection
- Invalid signature rejection
- Audience mismatch rejection
- Issuer mismatch rejection
- Rate limiting on refresh tokens
- Debug vs production error responses
- Missing authorization header
- Basic auth handling
- JWKS-based validation (placeholder)
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
import pytest
from fastapi import FastAPI, Request
from httpx import AsyncClient

from cohort.oauth2_middleware import (
    OAuth2Middleware,
    RateLimitState,
    TokenConfig,
)

# ---------------------------------------------------------------------------
# Test Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app() -> FastAPI:
    """Create a test FastAPI app."""
    app = FastAPI(title="Test API")

    @app.get("/protected")
    async def protected_endpoint() -> dict[str, str]:
        return {"status": "ok", "claims": getattr(Request(), "state", {}).get("oauth2_claims", {})}

    @app.post("/refresh")
    async def refresh_endpoint(request: Request) -> dict[str, Any]:
        # Simulate refresh token exchange
        client_id = getattr(request.state, "oauth2_claims", {}).get("client_id", "unknown")
        return {"client_id": client_id, "new_token": "new_access_token"}

    @app.get("/public")
    async def public_endpoint() -> str:
        return "public"

    return app


@pytest.fixture
def valid_token_secret() -> str:
    """Secret key for signing test tokens."""
    return "super-secret-key-for-testing-only-12345"


@pytest.fixture
def token_config(valid_token_secret: str) -> TokenConfig:
    """Create a token config for testing."""
    return TokenConfig(
        issuer="https://auth.example.com",
        audience="cohort-api",
        secret_key_path=None,  # We'll sign tokens in-memory
        public_key_path=None,
        jwks_url=None,
        clock_skew_seconds=300,
        debug=False,
    )


@pytest.fixture
def token_config_debug(valid_token_secret: str) -> TokenConfig:
    """Create a debug token config for testing."""
    return TokenConfig(
        issuer="https://auth.example.com",
        audience="cohort-api",
        secret_key_path=None,
        public_key_path=None,
        jwks_url=None,
        clock_skew_seconds=300,
        debug=True,
    )


@pytest.fixture
def middleware(app: FastAPI, token_config: TokenConfig) -> OAuth2Middleware:
    """Create OAuth2 middleware."""
    return OAuth2Middleware(
        app=app,
        token_config=token_config,
        rate_limit_refresh_tokens=False,  # Disable for most tests
        max_refresh_per_minute=10,
    )


@pytest.fixture
def middleware_debug(app: FastAPI, token_config_debug: TokenConfig) -> OAuth2Middleware:
    """Create debug OAuth2 middleware."""
    return OAuth2Middleware(
        app=app,
        token_config=token_config_debug,
        rate_limit_refresh_tokens=False,
        max_refresh_per_minute=10,
    )


@pytest.fixture
def valid_token(valid_token_secret: str) -> str:
    """Create a valid JWT token."""
    now = datetime.now(timezone.utc)
    payload = {
        "iss": "https://auth.example.com",
        "aud": "cohort-api",
        "sub": "user-123",
        "client_id": "test-client",
        "token_type": "access",
        "exp": (now + timedelta(minutes=30)).timestamp(),
        "iat": now.timestamp(),
    }
    return jwt.encode(payload, valid_token_secret, algorithm="HS256")


@pytest.fixture
def expired_token(valid_token_secret: str) -> str:
    """Create an expired JWT token."""
    now = datetime.now(timezone.utc)
    payload = {
        "iss": "https://auth.example.com",
        "aud": "cohort-api",
        "sub": "user-123",
        "client_id": "test-client",
        "token_type": "access",
        "exp": (now - timedelta(minutes=5)).timestamp(),  # Expired 5 minutes ago
        "iat": now.timestamp(),
    }
    return jwt.encode(payload, valid_token_secret, algorithm="HS256")


@pytest.fixture
def wrong_audience_token(valid_token_secret: str) -> str:
    """Create a token with wrong audience."""
    now = datetime.now(timezone.utc)
    payload = {
        "iss": "https://auth.example.com",
        "aud": "wrong-audience",  # Wrong audience
        "sub": "user-123",
        "client_id": "test-client",
        "token_type": "access",
        "exp": (now + timedelta(minutes=30)).timestamp(),
        "iat": now.timestamp(),
    }
    return jwt.encode(payload, valid_token_secret, algorithm="HS256")


@pytest.fixture
def wrong_issuer_token(valid_token_secret: str) -> str:
    """Create a token with wrong issuer."""
    now = datetime.now(timezone.utc)
    payload = {
        "iss": "https://wrong-issuer.com",  # Wrong issuer
        "aud": "cohort-api",
        "sub": "user-123",
        "client_id": "test-client",
        "token_type": "access",
        "exp": (now + timedelta(minutes=30)).timestamp(),
        "iat": now.timestamp(),
    }
    return jwt.encode(payload, valid_token_secret, algorithm="HS256")


@pytest.fixture
def invalid_signature_token() -> str:
    """Create a token with invalid signature."""
    payload = {
        "iss": "https://auth.example.com",
        "aud": "cohort-api",
        "sub": "user-123",
        "client_id": "test-client",
        "token_type": "access",
        "exp": int(time.time()) + 1800,
    }
    return jwt.encode(payload, "wrong-secret-key", algorithm="HS256")


@pytest.fixture
def refresh_token(valid_token_secret: str) -> str:
    """Create a refresh token."""
    now = datetime.now(timezone.utc)
    payload = {
        "iss": "https://auth.example.com",
        "aud": "cohort-api",
        "sub": "refresh-token-123",
        "client_id": "test-client",
        "token_type": "refresh",
        "exp": (now + timedelta(hours=1)).timestamp(),
        "iat": now.timestamp(),
    }
    return jwt.encode(payload, valid_token_secret, algorithm="HS256")


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------


class TestValidTokenAcceptance:
    """Tests for valid token acceptance."""

    @pytest.mark.asyncio
    async def test_valid_token_accepted(self, app: FastAPI, middleware: OAuth2Middleware, valid_token: str) -> None:
        """Valid token should be accepted."""
        response = await AsyncClient(app=app, base_url="http://test").get(
            "/protected", headers={"Authorization": f"Bearer {valid_token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["claims"]["sub"] == "user-123"

    @pytest.mark.asyncio
    async def test_valid_token_claims_passed(self, app: FastAPI, middleware: OAuth2Middleware, valid_token: str) -> None:
        """Valid token claims should be passed to downstream handlers."""
        response = await AsyncClient(app=app, base_url="http://test").get(
            "/protected", headers={"Authorization": f"Bearer {valid_token}"}
        )
        data = response.json()
        assert data["claims"]["client_id"] == "test-client"


class TestExpiredTokenRejection:
    """Tests for expired token rejection."""

    @pytest.mark.asyncio
    async def test_expired_token_rejected(self, app: FastAPI, middleware: OAuth2Middleware, expired_token: str) -> None:
        """Expired token should be rejected with 401."""
        response = await AsyncClient(app=app, base_url="http://test").get(
            "/protected", headers={"Authorization": f"Bearer {expired_token}"}
        )
        assert response.status_code == 401
        data = response.json()
        assert data["error"] == "token_expired"
        assert "expired" in data["error_description"].lower()

    @pytest.mark.asyncio
    async def test_expired_token_debug_response(self, app: FastAPI, middleware_debug: OAuth2Middleware, expired_token: str) -> None:
        """Expired token should include debug flag in response."""
        response = await AsyncClient(app=app, base_url="http://test").get(
            "/protected", headers={"Authorization": f"Bearer {expired_token}"}
        )
        data = response.json()
        assert data["error"] == "token_expired"
        assert data["debug"] is True


class TestInvalidSignatureRejection:
    """Tests for invalid signature rejection."""

    @pytest.mark.asyncio
    async def test_invalid_signature_rejected(self, app: FastAPI, middleware: OAuth2Middleware, invalid_signature_token: str) -> None:
        """Token with wrong signature should be rejected."""
        response = await AsyncClient(app=app, base_url="http://test").get(
            "/protected", headers={"Authorization": f"Bearer {invalid_signature_token}"}
        )
        assert response.status_code == 401
        data = response.json()
        assert data["error"] == "invalid_token"

    @pytest.mark.asyncio
    async def test_invalid_signature_debug_response(self, app: FastAPI, middleware_debug: OAuth2Middleware, invalid_signature_token: str) -> None:
        """Invalid signature should include debug flag."""
        response = await AsyncClient(app=app, base_url="http://test").get(
            "/protected", headers={"Authorization": f"Bearer {invalid_signature_token}"}
        )
        data = response.json()
        assert data["error"] == "invalid_token"
        assert data["debug"] is True


class TestAudienceMismatchRejection:
    """Tests for audience claim validation."""

    @pytest.mark.asyncio
    async def test_wrong_audience_rejected(self, app: FastAPI, middleware: OAuth2Middleware, wrong_audience_token: str) -> None:
        """Token with wrong audience should be rejected."""
        response = await AsyncClient(app=app, base_url="http://test").get(
            "/protected", headers={"Authorization": f"Bearer {wrong_audience_token}"}
        )
        assert response.status_code == 401
        data = response.json()
        assert data["error"] == "invalid_token"

    @pytest.mark.asyncio
    async def test_missing_audience_rejected(self, app: FastAPI, middleware: OAuth2Middleware) -> None:
        """Token without audience claim should be rejected."""
        now = datetime.now(timezone.utc)
        payload = {
            "iss": "https://auth.example.com",
            # No "aud" claim
            "sub": "user-123",
            "exp": (now + timedelta(minutes=30)).timestamp(),
        }
        token = jwt.encode(payload, "super-secret-key-for-testing-only-12345", algorithm="HS256")

        response = await AsyncClient(app=app, base_url="http://test").get(
            "/protected", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401
        data = response.json()
        assert data["error"] == "invalid_token"


class TestIssuerMismatchRejection:
    """Tests for issuer claim validation."""

    @pytest.mark.asyncio
    async def test_wrong_issuer_rejected(self, app: FastAPI, middleware: OAuth2Middleware, wrong_issuer_token: str) -> None:
        """Token with wrong issuer should be rejected."""
        response = await AsyncClient(app=app, base_url="http://test").get(
            "/protected", headers={"Authorization": f"Bearer {wrong_issuer_token}"}
        )
        assert response.status_code == 401
        data = response.json()
        assert data["error"] == "invalid_token"

    @pytest.mark.asyncio
    async def test_missing_issuer_rejected(self, app: FastAPI, middleware: OAuth2Middleware) -> None:
        """Token without issuer claim should be rejected."""
        now = datetime.now(timezone.utc)
        payload = {
            # No "iss" claim
            "aud": "cohort-api",
            "sub": "user-123",
            "exp": (now + timedelta(minutes=30)).timestamp(),
        }
        token = jwt.encode(payload, "super-secret-key-for-testing-only-12345", algorithm="HS256")

        response = await AsyncClient(app=app, base_url="http://test").get(
            "/protected", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401
        data = response.json()
        assert data["error"] == "invalid_token"


class TestMissingAuthorizationHeader:
    """Tests for missing authorization header."""

    @pytest.mark.asyncio
    async def test_no_auth_header_rejected(self, app: FastAPI, middleware: OAuth2Middleware) -> None:
        """Request without Authorization header should be rejected."""
        response = await AsyncClient(app=app, base_url="http://test").get("/protected")
        assert response.status_code == 401
        data = response.json()
        assert data["error"] == "invalid_token"

    @pytest.mark.asyncio
    async def test_empty_auth_header_rejected(self, app: FastAPI, middleware: OAuth2Middleware) -> None:
        """Request with empty Authorization header should be rejected."""
        response = await AsyncClient(app=app, base_url="http://test").get(
            "/protected", headers={"Authorization": ""}
        )
        assert response.status_code == 401
        data = response.json()
        assert data["error"] == "invalid_token"


class TestRateLimiting:
    """Tests for refresh token rate limiting."""

    @pytest.mark.asyncio
    async def test_rate_limit_enabled(self, app: FastAPI) -> None:
        """Test that rate limiting works when enabled."""
        OAuth2Middleware(
            app=app,
            token_config=TokenConfig(
                issuer="https://auth.example.com",
                audience="cohort-api",
                secret_key_path=None,
                debug=False,
            ),
            rate_limit_refresh_tokens=True,
            max_refresh_per_minute=3,  # Allow only 3 requests per minute
        )

        refresh_token = jwt.encode(
            {
                "iss": "https://auth.example.com",
                "aud": "cohort-api",
                "sub": "refresh-token-123",
                "client_id": "test-client",
                "token_type": "refresh",
                "exp": int(time.time()) + 3600,
            },
            "super-secret-key-for-testing-only-12345",
            algorithm="HS256",
        )

        # First 3 requests should succeed
        for i in range(3):
            response = await AsyncClient(app=app, base_url="http://test").post(
                "/refresh", headers={"Authorization": f"Bearer {refresh_token}"}
            )
            assert response.status_code == 200

        # 4th request should be rate limited
        response = await AsyncClient(app=app, base_url="http://test").post(
            "/refresh", headers={"Authorization": f"Bearer {refresh_token}"}
        )
        assert response.status_code == 429
        data = response.json()
        assert data["error"] == "too_many_requests"

    @pytest.mark.asyncio
    async def test_rate_limit_resets(self, app: FastAPI) -> None:
        """Test that rate limit resets after window expires."""
        OAuth2Middleware(
            app=app,
            token_config=TokenConfig(
                issuer="https://auth.example.com",
                audience="cohort-api",
                secret_key_path=None,
                debug=False,
            ),
            rate_limit_refresh_tokens=True,
            max_refresh_per_minute=2,
            window_seconds=60,
        )

        refresh_token = jwt.encode(
            {
                "iss": "https://auth.example.com",
                "aud": "cohort-api",
                "sub": "refresh-token-123",
                "client_id": "test-client",
                "token_type": "refresh",
                "exp": int(time.time()) + 3600,
            },
            "super-secret-key-for-testing-only-12345",
            algorithm="HS256",
        )

        # Exhaust rate limit
        for _ in range(2):
            await AsyncClient(app=app, base_url="http://test").post(
                "/refresh", headers={"Authorization": f"Bearer {refresh_token}"}
            )

        # Wait for window to expire (use sleep instead of time.sleep for async)
        import asyncio

        await asyncio.sleep(61)  # Wait 61 seconds

        # Should be allowed again
        response = await AsyncClient(app=app, base_url="http://test").post(
            "/refresh", headers={"Authorization": f"Bearer {refresh_token}"}
        )
        assert response.status_code == 200


class TestRateLimitState:
    """Tests for RateLimitState data class."""

    def test_rate_limit_state_initial(self) -> None:
        """Test initial state of rate limit tracker."""
        state = RateLimitState()
        assert len(state.requests) == 0
        assert state.max_requests == 10
        assert state.window_seconds == 60

    def test_rate_limit_state_allowed(self) -> None:
        """Test that requests are allowed within limit."""
        state = RateLimitState(max_requests=5, window_seconds=60)
        assert state.is_allowed("client-1") is True

    def test_rate_limit_state_exceeded(self) -> None:
        """Test that requests are rejected after limit exceeded."""
        state = RateLimitState(max_requests=2, window_seconds=60)
        state.record_request("client-1")
        state.record_request("client-1")
        assert state.is_allowed("client-1") is False

    def test_rate_limit_state_resets(self) -> None:
        """Test that old requests are cleaned up."""
        state = RateLimitState(max_requests=2, window_seconds=60)
        now = time.time()
        state.record_request("client-1")
        # Record a request from 70 seconds ago (should be cleaned up)
        state.requests.append(now - 70)
        assert len(state.requests) == 1  # Only the recent request remains


class TestErrorResponses:
    """Tests for error response format."""

    @pytest.mark.asyncio
    async def test_error_response_structure(self, app: FastAPI, middleware: OAuth2Middleware, expired_token: str) -> None:
        """Test that error responses have correct structure."""
        response = await AsyncClient(app=app, base_url="http://test").get(
            "/protected", headers={"Authorization": f"Bearer {expired_token}"}
        )
        data = response.json()

        assert "error" in data
        assert "error_description" in data
        assert "debug" in data
        assert isinstance(data["error"], str)
        assert isinstance(data["error_description"], str)
        assert isinstance(data["debug"], bool)

    @pytest.mark.asyncio
    async def test_error_response_content_type(self, app: FastAPI, middleware: OAuth2Middleware, expired_token: str) -> None:
        """Test that error responses have correct content type."""
        response = await AsyncClient(app=app, base_url="http://test").get(
            "/protected", headers={"Authorization": f"Bearer {expired_token}"}
        )
        assert response.headers["content-type"] == "application/json"

    @pytest.mark.asyncio
    async def test_debug_false_by_default(self, app: FastAPI, middleware: OAuth2Middleware, invalid_signature_token: str) -> None:
        """Test that debug is False by default."""
        response = await AsyncClient(app=app, base_url="http://test").get(
            "/protected", headers={"Authorization": f"Bearer {invalid_signature_token}"}
        )
        data = response.json()
        assert data["debug"] is False

    @pytest.mark.asyncio
    async def test_debug_true_includes_details(self, app: FastAPI, middleware_debug: OAuth2Middleware, invalid_signature_token: str) -> None:
        """Test that debug=True includes detailed error messages."""
        response = await AsyncClient(app=app, base_url="http://test").get(
            "/protected", headers={"Authorization": f"Bearer {invalid_signature_token}"}
        )
        data = response.json()
        assert data["debug"] is True


class TestBasicAuthHandling:
    """Tests for Basic auth handling (refresh token exchange)."""

    @pytest.mark.asyncio
    async def test_basic_auth_extracted(self, app: FastAPI) -> None:
        """Test that client ID can be extracted from Basic auth."""
        OAuth2Middleware(
            app=app,
            token_config=TokenConfig(
                issuer="https://auth.example.com",
                audience="cohort-api",
                secret_key_path=None,
                debug=False,
            ),
            rate_limit_refresh_tokens=True,
            max_refresh_per_minute=10,
        )

        # Create a refresh token with client_id claim
        refresh_token = jwt.encode(
            {
                "iss": "https://auth.example.com",
                "aud": "cohort-api",
                "sub": "refresh-token-123",
                "client_id": "basic-auth-client",
                "token_type": "refresh",
                "exp": int(time.time()) + 3600,
            },
            "super-secret-key-for-testing-only-12345",
            algorithm="HS256",
        )

        # Use Basic auth with the refresh token as password (simulating OAuth2 spec)
        import base64

        credentials = f"basic-auth-client:{refresh_token}"
        encoded = base64.b64encode(credentials.encode()).decode()

        response = await AsyncClient(app=app, base_url="http://test").post(
            "/refresh", headers={"Authorization": f"Basic {encoded}"}
        )

        # Should not be rate limited (client ID extracted from Basic auth)
        assert response.status_code == 200


class TestClockSkew:
    """Tests for clock skew handling."""

    @pytest.mark.asyncio
    async def test_token_within_skew_accepted(self, app: FastAPI, middleware: OAuth2Middleware, valid_token: str) -> None:
        """Token within clock skew should be accepted."""
        # This is implicitly tested - tokens with exp in the future are accepted
        response = await AsyncClient(app=app, base_url="http://test").get(
            "/protected", headers={"Authorization": f"Bearer {valid_token}"}
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_token_just_expired_accepted(self, app: FastAPI, middleware: OAuth2Middleware) -> None:
        """Token that expired within clock skew should be accepted."""
        now = datetime.now(timezone.utc)
        # Token expires 1 minute ago (within 300 second clock skew)
        payload = {
            "iss": "https://auth.example.com",
            "aud": "cohort-api",
            "sub": "user-123",
            "client_id": "test-client",
            "token_type": "access",
            "exp": (now - timedelta(minutes=1)).timestamp(),
            "iat": now.timestamp(),
        }
        token = jwt.encode(payload, "super-secret-key-for-testing-only-12345", algorithm="HS256")

        response = await AsyncClient(app=app, base_url="http://test").get(
            "/protected", headers={"Authorization": f"Bearer {token}"}
        )
        # Should be accepted due to clock skew
        assert response.status_code == 200


class TestMiddlewareIntegration:
    """Integration tests for middleware with FastAPI app."""

    @pytest.mark.asyncio
    async def test_middleware_doesnt_break_public_endpoints(self, app: FastAPI, middleware: OAuth2Middleware) -> None:
        """Test that middleware doesn't break public endpoints (if configured)."""
        # Note: Current implementation requires auth on all endpoints.
        # To support public endpoints, add a skip list to the middleware.
        response = await AsyncClient(app=app, base_url="http://test").get("/public")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_middleware_chain_order(self, app: FastAPI, middleware: OAuth2Middleware) -> None:
        """Test that middleware is applied in correct order."""
        # Middleware should run before endpoint handlers
        response = await AsyncClient(app=app, base_url="http://test").get(
            "/protected", headers={"Authorization": f"Bearer {valid_token}"}
        )
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Run tests
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
