"""Hacker News scraper via HN Algolia API."""

import logging
from datetime import datetime, timezone

import httpx

from ..models import NewsItem

logger = logging.getLogger(__name__)

HN_ALGOLIA_URL = "https://hn.algolia.com/api/v1/search"


async def fetch_hackernews(
    client: httpx.AsyncClient,
    since: datetime,
    limit: int = 40,
    min_score: int = 50,
) -> list[NewsItem]:
    """Fetch recent high-signal stories from Hacker News."""
    since_ts = int(since.timestamp())
    params = {
        "tags": "story",
        "numericFilters": f"created_at_i>={since_ts},points>={min_score}",
        "hitsPerPage": limit,
    }
    try:
        resp = await client.get(HN_ALGOLIA_URL, params=params)
        resp.raise_for_status()
        hits = resp.json().get("hits", [])
    except Exception as exc:
        logger.warning("HN fetch failed: %s", exc)
        return []

    items: list[NewsItem] = []
    for hit in hits:
        object_id = hit.get("objectID")
        title = (hit.get("title") or "").strip()
        if not object_id or not title:
            continue
        discussion_url = f"https://news.ycombinator.com/item?id={object_id}"
        url = hit.get("url") or discussion_url
        created_ts = hit.get("created_at_i") or int(datetime.now(timezone.utc).timestamp())
        published_at = datetime.fromtimestamp(created_ts, tz=timezone.utc)
        snippet = (hit.get("story_text") or "")[:400].strip()

        items.append(
            NewsItem(
                uid=f"hn:{object_id}",
                title=title,
                url=url,
                source_kind="hn",
                source_name="Hacker News",
                category="code",
                score=int(hit.get("points") or 0),
                comments_count=int(hit.get("num_comments") or 0),
                published_at=published_at,
                snippet=snippet,
                discussion_url=discussion_url,
            )
        )
    return items
