"""Reddit JSON scraper for configured subreddits."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from ..models import NewsItem

logger = logging.getLogger(__name__)

REDDIT_USER_AGENT = "ultra-news-bot/0.1 (personal digest)"


async def _fetch_subreddit(
    client: httpx.AsyncClient,
    subreddit: str,
    category: str,
    since: datetime,
    limit: int,
    min_score: int,
) -> list[NewsItem]:
    url = f"https://www.reddit.com/r/{subreddit}/hot.json"
    headers = {"User-Agent": REDDIT_USER_AGENT}
    try:
        resp = await client.get(url, params={"limit": limit}, headers=headers)
        resp.raise_for_status()
        children = resp.json().get("data", {}).get("children", [])
    except Exception as exc:
        logger.warning("Reddit fetch failed for r/%s: %s", subreddit, exc)
        return []

    items: list[NewsItem] = []
    for child in children:
        data = child.get("data", {})
        if data.get("stickied"):
            continue
        score = int(data.get("score") or 0)
        if score < min_score:
            continue

        created_utc = data.get("created_utc")
        if not created_utc:
            continue
        published_at = datetime.fromtimestamp(float(created_utc), tz=timezone.utc)
        if published_at < since:
            continue

        post_id = data.get("id")
        title = (data.get("title") or "").strip()
        if not post_id or not title:
            continue

        permalink = data.get("permalink") or ""
        discussion_url = f"https://www.reddit.com{permalink}" if permalink else None
        post_url = data.get("url_overridden_by_dest") or data.get("url") or discussion_url
        if not post_url:
            continue

        image_url = None
        preview = data.get("preview", {}).get("images", [])
        if preview:
            raw_img = preview[0].get("source", {}).get("url", "")
            if raw_img.startswith("http"):
                image_url = raw_img.replace("&amp;", "&")

        snippet = (data.get("selftext") or "")[:400].strip()

        items.append(
            NewsItem(
                uid=f"reddit:{post_id}",
                title=title,
                url=post_url,
                source_kind="reddit",
                source_name=f"r/{subreddit}",
                category=category,
                score=score,
                comments_count=int(data.get("num_comments") or 0),
                published_at=published_at,
                snippet=snippet,
                image_url=image_url,
                discussion_url=discussion_url,
            )
        )
    return items


async def fetch_reddit(
    client: httpx.AsyncClient,
    reddit_cfg: dict[str, Any],
    since: datetime,
) -> list[NewsItem]:
    """Fetch hot posts from all enabled subreddits."""
    if not reddit_cfg.get("enabled", True):
        return []

    min_score = int(reddit_cfg.get("min_score", 40))
    limit = int(reddit_cfg.get("limit_per_sub", 12))
    tasks = []
    for sub in reddit_cfg.get("subreddits", []):
        if not sub.get("enabled", True):
            continue
        tasks.append(
            _fetch_subreddit(
                client=client,
                subreddit=sub["name"],
                category=sub.get("category", "code"),
                since=since,
                limit=limit,
                min_score=min_score,
            )
        )
    if not tasks:
        return []
    results = await asyncio.gather(*tasks)
    return [item for batch in results for item in batch]
