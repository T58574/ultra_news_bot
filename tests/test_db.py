"""Tests for SQLite persistence and quota tracking."""

from datetime import datetime, timezone
from pathlib import Path

from ultra_news_bot.db import BotDatabase


def test_db_usage_and_digest_cache(tmp_path: Path) -> None:
    db = BotDatabase(tmp_path / "bot.db")
    db.record_llm_call("gemini-3.1-flash-lite")
    db.record_llm_call("gemini-3.1-flash-lite")
    db.record_llm_call("gemini-3.5-flash-lite")

    usage = db.get_today_usage()
    assert usage["gemini-3.1-flash-lite"] == 2
    assert usage["gemini-3.5-flash-lite"] == 1

    db.save_digest(
        created_at=datetime(2026, 11, 15, 9, 0, tzinfo=timezone.utc),
        cover_image_url="https://example.com/img.png",
        messages=["hello"],
        total_scanned=42,
        selected_count=5,
    )
    latest = db.get_latest_digest()
    assert latest is not None
    assert latest["messages"] == ["hello"]
    assert latest["cover_image_url"] == "https://example.com/img.png"
