"""
Security and Defense Layer.
Includes token redaction logging, constant-time secret comparison,
rate limiting, security headers, and authentication verification.
"""

from __future__ import annotations
import re
import sys
import time
import logging
import secrets
from collections import defaultdict
from typing import Dict, List, Optional
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader, HTTPBearer, HTTPAuthorizationCredentials
from services.config import get_settings

# Force UTF-8 encoding on Windows to prevent UnicodeEncodeError with special Discord nicknames/emojis
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass

logger = logging.getLogger("security")

# Security schemes for Swagger/OpenAPI and FastAPI dependencies
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer_security = HTTPBearer(auto_error=False)


class RedactingFilter(logging.Filter):
    """
    Logging filter that sanitizes logs to prevent sensitive credentials,
    tokens, or passwords from ever leaking into stdout or log files.
    """

    def __init__(self, secrets_to_mask: Optional[List[str]] = None):
        super().__init__()
        self.secrets_to_mask = [s for s in (secrets_to_mask or []) if s and len(s) >= 4]
        # Regex to detect Discord Bot Token pattern: e.g. MTIzNDU2Nzg...
        self.token_pattern = re.compile(
            r"([MN][A-Za-z0-9]{23,26}\.[A-Za-z0-9_\-]{6}\.[A-Za-z0-9_\-]{27,38})"
        )
        # Regex to mask generic Bearer tokens
        self.bearer_pattern = re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.]{8,}", re.IGNORECASE)

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self.sanitize(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: self.sanitize(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    self.sanitize(a) if isinstance(a, str) else a
                    for a in record.args
                )
        return True

    def sanitize(self, text: str) -> str:
        for sec in self.secrets_to_mask:
            text = text.replace(sec, "***REDACTED***")
        text = self.token_pattern.sub("***REDACTED_DISCORD_TOKEN***", text)
        text = self.bearer_pattern.sub(r"\1***REDACTED_BEARER***", text)
        return text


def setup_secure_logging() -> logging.Logger:
    """Configures root logger with credentials redaction filter."""
    settings = get_settings()
    log_level = getattr(logging, settings.log_level, logging.INFO)

    # Gather known secrets for the redaction filter
    secrets_list = [settings.discord_token, settings.dashboard_secret]

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Format handler if none exists
    if not root_logger.handlers:
        console_handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    redacting_filter = RedactingFilter(secrets_list)
    for handler in root_logger.handlers:
        handler.addFilter(redacting_filter)

    return root_logger


def verify_secret(provided: Optional[str], expected: Optional[str]) -> bool:
    """
    Performs constant-time comparison of two strings to prevent timing attacks.
    Returns False immediately if either parameter is None or empty.
    """
    if not provided or not expected:
        return False
    return secrets.compare_digest(provided.strip(), expected.strip())


class InMemoryRateLimiter:
    """
    Sliding window rate limiter per client IP.
    Self-cleaning to prevent memory growth over long periods.
    """

    def __init__(self, requests_per_minute: int = 60):
        self.requests_per_minute = requests_per_minute
        self.window_seconds = 60
        self.requests: Dict[str, List[float]] = defaultdict(list)
        self.last_cleanup = time.time()

    def is_rate_limited(self, client_ip: str) -> bool:
        now = time.time()

        # Prune memory periodically every 2 minutes
        if now - self.last_cleanup > 120:
            self._cleanup(now)
            self.last_cleanup = now

        window_start = now - self.window_seconds
        timestamps = self.requests[client_ip]

        # Discard timestamps outside window
        self.requests[client_ip] = [ts for ts in timestamps if ts > window_start]

        if len(self.requests[client_ip]) >= self.requests_per_minute:
            return True

        self.requests[client_ip].append(now)
        return False

    def _cleanup(self, now: float) -> None:
        window_start = now - self.window_seconds
        stale_keys = [
            ip
            for ip, timestamps in self.requests.items()
            if not timestamps or timestamps[-1] <= window_start
        ]
        for ip in stale_keys:
            del self.requests[ip]


# Singleton rate limiter instance
rate_limiter = InMemoryRateLimiter()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Enforces modern browser defense headers to mitigate XSS, clickjacking,
    and MIME-sniffing vulnerabilities.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=(), payment=()"
        )

        # Allow WebSockets, static styling, inline icons and fonts
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: https://cdn.discordapp.com; "
            "script-src 'self' 'unsafe-inline'; "
            "connect-src 'self' ws: wss:;"
        )
        return response


def get_client_ip(request: Request) -> str:
    """Extracts client IP, respecting X-Forwarded-For if behind a reverse proxy."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def verify_admin_token(
    api_key: Optional[str] = Security(api_key_header),
    bearer: Optional[HTTPAuthorizationCredentials] = Security(bearer_security),
) -> bool:
    """
    FastAPI dependency that enforces admin authentication.
    Accepts X-API-Key or Bearer token matching DASHBOARD_SECRET.
    """
    settings = get_settings()
    token = api_key or (bearer.credentials if bearer else None)

    if not token or not verify_secret(token, settings.dashboard_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: Valid administrator token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return True


def check_auth_optional(
    api_key: Optional[str] = Security(api_key_header),
    bearer: Optional[HTTPAuthorizationCredentials] = Security(bearer_security),
) -> bool:
    """
    FastAPI dependency that returns True if user provided a valid admin token,
    or False otherwise, without raising an exception.
    """
    settings = get_settings()
    token = api_key or (bearer.credentials if bearer else None)
    if token and verify_secret(token, settings.dashboard_secret):
        return True
    return False
