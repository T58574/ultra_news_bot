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


def test_dedup_against_recent_db_titles_and_category_balance(tmp_path: Path) -> None:
    db = BotDatabase(tmp_path / "test_hist.db")
    db.mark_seen([
        ("https://techcrunch.com/spacex-starship-test", "SpaceX Starship completes orbital flight test")
    ])

    now = datetime.now(timezone.utc)
    cats = {
        "ai": {"label": "AI", "enabled": True, "keywords": ["llm"]},
        "space_sci": {"label": "SPACE", "enabled": True, "keywords": ["spacex", "mars"]},
    }
    items = [
        # Duplicate topic with different URL from 72h DB history -> must be filtered
        NewsItem(
            uid="dup_hist",
            title="SpaceX Starship completes orbital flight test today",
            url="https://arstechnica.com/starship-flight",
            source_kind="rss",
            source_name="Ars",
            category="space_sci",
            published_at=now,
        ),
        NewsItem(
            uid="ai_1",
            title="New LLM benchmark released",
            url="https://example.com/ai1",
            source_kind="hn",
            source_name="HN",
            category="ai",
            published_at=now,
            score=500,
        ),
        NewsItem(
            uid="ai_2",
            title="Another LLM quantization paper",
            url="https://example.com/ai2",
            source_kind="hn",
            source_name="HN",
            category="ai",
            published_at=now,
            score=450,
        ),
        NewsItem(
            uid="space_1",
            title="NASA Mars rover discovers new mineral",
            url="https://example.com/mars",
            source_kind="rss",
            source_name="Spaceflight",
            category="space_sci",
            published_at=now,
        ),
    ]

    result = deduplicate_and_rank(items, cats, db, max_candidates=2)
    uids = {item.uid for item in result}
    assert "dup_hist" not in uids
    assert uids == {"ai_1", "space_1"}
