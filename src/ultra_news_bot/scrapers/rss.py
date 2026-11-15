"""RSS/Atom scraper and OPML feed list parser."""

import asyncio
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional

import feedparser
import httpx
from bs4 import BeautifulSoup

from ..models import NewsItem

logger = logging.getLogger(__name__)


def _parse_entry_date(entry: Any) -> datetime:
    for field in ("published_parsed", "updated_parsed"):
        parsed = entry.get(field)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    for field in ("published", "updated"):
        raw = entry.get(field)
        if raw:
            try:
                dt = parsedate_to_datetime(raw)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except Exception:
                pass
    return datetime.now(timezone.utc)


def _clean_html(raw_html: str) -> str:
    if not raw_html:
        return ""
    text = BeautifulSoup(raw_html, "html.parser").get_text(" ", strip=True)
    return " ".join(text.split())


def _extract_image(entry: Any) -> Optional[str]:
    for media in entry.get("media_content", []) or []:
        url = media.get("url")
        if url and url.startswith("http"):
            return url
    for media in entry.get("media_thumbnail", []) or []:
        url = media.get("url")
        if url and url.startswith("http"):
            return url
    for link in entry.get("links", []) or []:
        if str(link.get("type", "")).startswith("image/") and str(link.get("href", "")).startswith("http"):
            return link["href"]
    return None


async def _fetch_single_feed(
    client: httpx.AsyncClient,
    feed_cfg: dict[str, Any],
    since: datetime,
) -> list[NewsItem]:
    url = feed_cfg["url"]
    name = feed_cfg.get("name", url)
    category = feed_cfg.get("category", "code")
    try:
        resp = await client.get(
            url,
            headers={"User-Agent": "ultra-news-bot/0.1"},
            follow_redirects=True,
        )
        resp.raise_for_status()
        parsed = feedparser.parse(resp.text)
    except Exception as exc:
        logger.warning("RSS fetch failed for %s (%s): %s", name, url, exc)
        return []

    items: list[NewsItem] = []
    for entry in parsed.entries[:25]:
        link = (entry.get("link") or "").strip()
        title = (entry.get("title") or "").strip()
        if not link or not title:
            continue
        published_at = _parse_entry_date(entry)
        if published_at < since:
            continue

        raw_summary = entry.get("summary") or entry.get("description") or ""
        snippet = _clean_html(raw_summary)[:400]
        entry_id = entry.get("id") or link

        items.append(
            NewsItem(
                uid=f"rss:{hash(entry_id)}",
                title=title,
                url=link,
                source_kind="rss",
                source_name=name,
                category=category,
                score=0,
                published_at=published_at,
                snippet=snippet,
                image_url=_extract_image(entry),
            )
        )
    return items


async def fetch_rss_feeds(
    client: httpx.AsyncClient,
    rss_configs: list[dict[str, Any]],
    since: datetime,
) -> list[NewsItem]:
    """Fetch all enabled RSS/Atom feeds concurrently."""
    tasks = [
        _fetch_single_feed(client, cfg, since)
        for cfg in rss_configs
        if cfg.get("enabled", True)
    ]
    if not tasks:
        return []
    results = await asyncio.gather(*tasks)
    return [item for batch in results for item in batch]


def parse_opml(opml_xml: str, default_category: str = "code") -> list[dict[str, Any]]:
    """Parse OPML XML text into a list of RSS config dicts."""
    root = ET.fromstring(opml_xml)
    feeds: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for outline in root.iter("outline"):
        xml_url = outline.attrib.get("xmlUrl") or outline.attrib.get("xmlurl")
        if not xml_url or xml_url in seen_urls:
            continue
        seen_urls.add(xml_url)
        name = (
            outline.attrib.get("title")
            or outline.attrib.get("text")
            or xml_url
        )
        feeds.append(
            {
                "name": name.strip()[:60],
                "url": xml_url.strip(),
                "category": default_category,
                "enabled": True,
            }
        )
    return feeds
