"""GitHub trending / active repositories scraper."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ..models import NewsItem

logger = logging.getLogger(__name__)

GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"


async def fetch_github(
    client: httpx.AsyncClient,
    github_cfg: dict[str, Any],
    github_token: str = "",
) -> list[NewsItem]:
    """Fetch notable recent repositories from GitHub Search API."""
    if not github_cfg.get("enabled", True):
        return []

    since_date = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    min_stars = int(github_cfg.get("min_stars", 80))
    limit = int(github_cfg.get("limit", 15))
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ultra-news-bot/0.1",
    }
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    items: dict[str, NewsItem] = {}
    for raw_query in github_cfg.get("queries", []):
        query = raw_query.format(since_date=since_date)
        params = {"q": query, "sort": "stars", "order": "desc", "per_page": limit}
        try:
            resp = await client.get(GITHUB_SEARCH_URL, params=params, headers=headers)
            resp.raise_for_status()
            repos = resp.json().get("items", [])
        except Exception as exc:
            logger.warning("GitHub search failed for '%s': %s", query, exc)
            continue

        for repo in repos:
            stars = int(repo.get("stargazers_count") or 0)
            if stars < min_stars:
                continue
            full_name = repo.get("full_name")
            html_url = repo.get("html_url")
            if not full_name or not html_url or html_url in items:
                continue

            desc = (repo.get("description") or "").strip()
            lang = repo.get("language") or ""
            snippet = f"[{lang}] {desc}".strip() if lang else desc
            updated_raw = repo.get("pushed_at") or repo.get("created_at")
            published_at = (
                datetime.fromisoformat(updated_raw.replace("Z", "+00:00"))
                if updated_raw
                else datetime.now(timezone.utc)
            )
            topics = " ".join(repo.get("topics") or [])
            category = "ai" if any(k in (desc + " " + topics).lower() for k in ("llm", "ai", "agent", "ml")) else "code"

            items[html_url] = NewsItem(
                uid=f"gh:{repo.get('id', full_name)}",
                title=f"{full_name} — {desc[:120]}" if desc else full_name,
                url=html_url,
                source_kind="github",
                source_name="GitHub",
                category=category,
                score=stars,
                comments_count=int(repo.get("forks_count") or 0),
                published_at=published_at,
                snippet=snippet[:400],
            )
    return list(items.values())
