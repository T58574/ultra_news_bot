"""Tests for netrunner HTML formatting and Telegram message splitting."""

from datetime import datetime, timezone

from ultra_news_bot.models import DigestEntry
from ultra_news_bot.pipeline.formatter import MAX_MESSAGE_LEN, render_digest_messages


def test_formatter_escapes_html_and_splits_long_digests() -> None:
    cats = {
        "ai": {"label": "AI / NEURAL", "emoji": "🤖", "enabled": True},
    }
    entries = [
        DigestEntry(
            uid=f"id:{i}",
            category="ai",
            headline=f"Release <v{i}> & benchmarks",
            summary="Details " + ("x" * 600),
            source_label="HN: 200↑",
            url=f"https://example.com/{i}",
            discussion_url=f"https://news.ycombinator.com/item?id={i}",
        )
        for i in range(10)
    ]

    messages = render_digest_messages(
        entries=entries,
        categories_cfg=cats,
        created_at=datetime(2026, 11, 15, 9, 0, tzinfo=timezone.utc),
        total_scanned=100,
        candidates_sent=50,
        model_filter="gemini-3.1-flash-lite",
        model_summary="gemini-3.5-flash-lite",
    )

    assert len(messages) > 1
    assert all(len(msg) <= MAX_MESSAGE_LEN for msg in messages)
    assert "&lt;v0&gt; &amp; benchmarks" in messages[0]
    assert "NETRUNNER // FEED" in messages[0]
