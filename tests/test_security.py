"""
Unit Tests for Security Defense Mechanisms.
Verifies constant-time comparison, secret redaction filter, rate limiting, and input validation.
"""

import logging
import pytest
from services.security import (
    InMemoryRateLimiter,
    RedactingFilter,
    verify_secret,
)
from services.config import Settings


def test_verify_secret():
    """Test constant-time secret comparison."""
    secret = "my_super_secure_admin_key_12345"
    assert verify_secret(secret, secret) is True
    assert verify_secret(f" {secret} ", secret) is True  # Strips whitespace
    assert verify_secret("wrong_secret", secret) is False
    assert verify_secret("", secret) is False
    assert verify_secret(None, secret) is False
    assert verify_secret(secret, None) is False
    assert verify_secret("", "") is False


def test_redacting_filter():
    """Test that logs mask bot tokens, passwords, and secrets."""
    secret = "super_secret_dashboard_token_xyz"
    discord_token = "FAKE_DISCORD_TOKEN_FOR_TESTS"

    filter_instance = RedactingFilter(secrets_to_mask=[secret, discord_token])

    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg=f"Connected with token {discord_token} and secret {secret}",
        args=(),
        exc_info=None,
    )

    filter_instance.filter(record)

    assert discord_token not in record.msg
    assert secret not in record.msg
    assert "***REDACTED" in record.msg


def test_in_memory_rate_limiter():
    """Test rate limiter allows requests up to threshold and throttles afterwards."""
    limiter = InMemoryRateLimiter(requests_per_minute=3)
    ip = "192.168.1.100"

    # First 3 requests should be allowed
    assert limiter.is_rate_limited(ip) is False
    assert limiter.is_rate_limited(ip) is False
    assert limiter.is_rate_limited(ip) is False

    # 4th request must be throttled
    assert limiter.is_rate_limited(ip) is True

    # Different IP should have independent budget
    other_ip = "10.0.0.1"
    assert limiter.is_rate_limited(other_ip) is False


def test_settings_validation():
    """Test settings model defaults and parsing."""
    settings = Settings(
        discord_token="fake_token",
        port=9000,
        rate_limit_per_minute=30,
    )

    assert settings.port == 9000
    assert settings.rate_limit_per_minute == 30
    assert settings.bot_prefix == ","
    assert len(settings.dashboard_secret) >= 16
