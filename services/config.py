"""
Configuration Management for Discord VC Counter Bot.
Uses Pydantic for validation and python-dotenv for environment loading.
"""

from __future__ import annotations
import os
import secrets
from functools import lru_cache
from typing import List
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Load .env file if present
load_dotenv()


class Settings(BaseModel):
    # Discord Bot Credentials
    discord_token: str = Field(
        default_factory=lambda: os.getenv("DISCORD_TOKEN", "")
    )
    discord_client_id: str = Field(
        default_factory=lambda: os.getenv("DISCORD_CLIENT_ID", "")
    )
    bot_prefix: str = Field(
        default_factory=lambda: os.getenv("BOT_PREFIX", ",")
    )
    bot_status: str = Field(
        default_factory=lambda: os.getenv("BOT_STATUS", "Hiring Weekly Paid staffs For .gg/heartbeatz")
    )
    bot_custom_status: str = Field(
        default_factory=lambda: os.getenv("BOT_CUSTOM_STATUS", "Hiring Weekly Paid staffs For .gg/heartbeatz")
    )
    bot_banner_status: str = Field(
        default_factory=lambda: os.getenv("BOT_BANNER_STATUS", "")
    )
    bot_banner_type: str = Field(
        default_factory=lambda: os.getenv("BOT_BANNER_TYPE", "watching")
    )
    bot_activity_type: str = Field(
        default_factory=lambda: os.getenv("BOT_ACTIVITY_TYPE", "custom")
    )
    enable_privileged_intents: bool = Field(
        default_factory=lambda: os.getenv("ENABLE_PRIVILEGED_INTENTS", "true").lower()
        in ("1", "true", "yes")
    )

    # Web Server & Host Configuration
    host: str = Field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    port: int = Field(
        default_factory=lambda: int(os.getenv("PORT", "8000"))
    )
    dashboard_url: str = Field(
        default_factory=lambda: os.getenv("DASHBOARD_URL", "https://usb-academic-governmental-musician.trycloudflare.com")
    )

    # Security & Dashboard Access
    dashboard_secret: str = Field(
        default_factory=lambda: os.getenv(
            "DASHBOARD_SECRET",
            secrets.token_urlsafe(32),
        )
    )
    default_public_dashboard: bool = Field(
        default_factory=lambda: os.getenv(
            "DEFAULT_PUBLIC_DASHBOARD", "false"
        ).lower()
        in ("1", "true", "yes")
    )
    cors_origins: str = Field(
        default_factory=lambda: os.getenv("CORS_ORIGINS", "*")
    )
    rate_limit_per_minute: int = Field(
        default_factory=lambda: int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))
    )

    # Counter Channels Config
    # Discord rate-limit safety: 300s = 5 minutes minimum between channel renames
    counter_update_interval_seconds: int = Field(
        default_factory=lambda: int(os.getenv("COUNTER_UPDATE_INTERVAL_SECONDS", "300"))
    )

    # Persistent Storage
    database_path: str = Field(
        default_factory=lambda: os.getenv("DATABASE_PATH", "data/bot.db")
    )

    # Runtime & Logging
    environment: str = Field(
        default_factory=lambda: os.getenv("ENVIRONMENT", "production")
    )
    log_level: str = Field(
        default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper()
    )

    @property
    def cors_origins_list(self) -> List[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


@lru_cache()
def get_settings() -> Settings:
    """Return cached singleton instance of validated settings."""
    return Settings()
