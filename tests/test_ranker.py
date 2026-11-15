"""Tests for URL normalization, deduplication, and ranking."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from ultra_news_bot.db import BotDatabase
from ultra_news_bot.models import NewsItem
from ultra_news_bot.pipeline.ranker import deduplicate_and_rank, normalize_url


def test_normalize_url_strips_tracking_and_www() -> None:
    u1 = "https://www.example.com/post/123/?utm_source=hn&ref=top#comments"
    u2 = "https://example.com/post/123"
    assert normalize_url(u1) == normalize_url(u2)


def test_deduplicate_and_rank_filters_seen_and_similar_titles(tmp_path: Path) -> None:
    db = BotDatabase(tmp_path / "test.db")
    db.mark_seen([(normalize_url("https://seen.example.com/old"), "Old item")])

    now = datetime.now(timezone.utc)
    cats = {
        "ai": {"label": "AI", "enabled": True, "keywords": ["llm", "model"]},
        "code": {"label": "CODE", "enabled": True, "keywords": ["python"]},
    }
    items = [
        NewsItem(
            uid="1",
            title="Old item",
            url="https://seen.example.com/old",
            source_kind="hn",
            source_name="HN",
            published_at=now,
            score=500,
        ),
        NewsItem(
            uid="2",
            title="OpenAI releases new open weight LLM model",
            url="https://news.example.com/a",
            source_kind="hn",
            source_name="HN",
            published_at=now - timedelta(hours=1),
            score=300,
        ),
        NewsItem(
            uid="3",
            title="OpenAI releases new open weight LLM model today",
            url="https://other.example.com/b",
            source_kind="reddit",
            source_name="r/LocalLLaMA",
            published_at=now - timedelta(hours=2),
            score=120,
        ),
    ]

    result = deduplicate_and_rank(items, cats, db, max_candidates=10)
    assert len(result) == 1
    assert result[0].uid == "2"
    assert result[0].category == "ai"
