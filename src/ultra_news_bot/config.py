"""Application settings and dynamic sources.json manager."""

import json
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_channel_id: str = Field(default="", alias="TELEGRAM_CHANNEL_ID")
    allowed_user_ids_raw: str = Field(default="", alias="ALLOWED_USER_IDS")

    google_api_key: str = Field(default="", alias="GOOGLE_API_KEY")
    gemini_filter_model: str = Field(
        default="gemini-3.1-flash-lite", alias="GEMINI_FILTER_MODEL"
    )
    gemini_summary_model: str = Field(
        default="gemini-3.5-flash-lite", alias="GEMINI_SUMMARY_MODEL"
    )
    gemini_fallback_models_raw: str = Field(
        default=(
            "gemini-3.5-flash-lite,gemini-3.1-flash-lite,"
            "gemini-3.5-flash,gemini-3.6-flash,"
            "gemini-2.5-flash,gemini-2.5-flash-lite"
        ),
        alias="GEMINI_FALLBACK_MODELS",
    )
    gemini_max_attempts: int = Field(default=5, alias="GEMINI_MAX_ATTEMPTS")
    gemini_backoff_base_seconds: float = Field(default=10.0, alias="GEMINI_BACKOFF_BASE_SECONDS")
    gemini_backoff_max_seconds: float = Field(default=90.0, alias="GEMINI_BACKOFF_MAX_SECONDS")

    github_token: str = Field(default="", alias="GITHUB_TOKEN")

    timezone: str = Field(default="Europe/Moscow", alias="TIMEZONE")
    morning_digest_time: str = Field(default="09:00", alias="MORNING_DIGEST_TIME")
    evening_digest_time: str = Field(default="20:00", alias="EVENING_DIGEST_TIME")

    lookback_hours: int = Field(default=16, alias="LOOKBACK_HOURS")
    max_candidates_for_llm: int = Field(default=70, alias="MAX_CANDIDATES_FOR_LLM")
    top_items_to_summarize: int = Field(default=12, alias="TOP_ITEMS_TO_SUMMARIZE")
    http_timeout_seconds: float = Field(default=20.0, alias="HTTP_TIMEOUT_SECONDS")

    sources_path: Path = Field(default=Path("config/sources.json"), alias="SOURCES_PATH")
    db_path: Path = Field(default=Path("data/bot.db"), alias="DB_PATH")

    @property
    def allowed_user_ids(self) -> set[int]:
        ids: set[int] = set()
        for part in self.allowed_user_ids_raw.split(","):
            part = part.strip()
            if part.isdigit():
                ids.add(int(part))
        return ids

    @property
    def digest_target_chat_id(self) -> str | None:
        target = self.telegram_channel_id.strip()
        return target or None

    @property
    def gemini_fallback_models(self) -> list[str]:
        return [
            m.strip()
            for m in self.gemini_fallback_models_raw.split(",")
            if m.strip()
        ]


def load_sources_config(path: Path) -> dict[str, Any]:
    """Load sources.json from disk."""
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_sources_config(path: Path, data: dict[str, Any]) -> None:
    """Persist updated sources.json atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    tmp_path.replace(path)
