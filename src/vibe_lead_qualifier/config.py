"""Application configuration loaded from environment variables."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings.

    Secrets deliberately have no non-empty defaults and are never serialized by
    the application.
    """

    vibe_api_token: str | None = field(repr=False)
    vibe_webhook_secret: str | None = field(repr=False)
    vibe_base_url: str
    database_path: Path
    log_level: str
    http_timeout_seconds: float = 20.0
    enable_demo_endpoints: bool = False

    @classmethod
    def from_env(cls, env_file: str | Path | None = ".env") -> Settings:
        if env_file is not None:
            load_dotenv(dotenv_path=env_file, override=False)

        log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        if log_level not in logging.getLevelNamesMapping():
            log_level = "INFO"

        token = os.getenv("VIBE_API_TOKEN") or None
        webhook_secret = os.getenv("VIBE_WEBHOOK_SECRET") or None
        return cls(
            vibe_api_token=token,
            vibe_webhook_secret=webhook_secret,
            vibe_base_url=os.getenv("VIBE_BASE_URL", "https://lk.vibemarketolog.ru"),
            database_path=Path(os.getenv("DATABASE_PATH", "./data/vibe_leads.sqlite3")),
            log_level=log_level,
            http_timeout_seconds=float(os.getenv("HTTP_TIMEOUT_SECONDS", "20")),
            enable_demo_endpoints=os.getenv("ENABLE_DEMO_ENDPOINTS", "false").strip().casefold()
            in {"1", "true", "yes", "on"},
        )
