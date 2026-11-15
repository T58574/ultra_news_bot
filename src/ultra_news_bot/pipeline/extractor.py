"""Full-text and OpenGraph image extractor for selected top items."""

import asyncio
import ipaddress
import logging
import socket
from typing import Optional
from urllib.parse import urljoin, urlsplit

import httpx
import trafilatura
from bs4 import BeautifulSoup

from ..models import NewsItem

logger = logging.getLogger(__name__)


def is_safe_public_url(url: str) -> bool:
    """Reject non-HTTP(S) schemes and local/private network targets."""
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
        return False
    try:
        ip = ipaddress.ip_address(host)
        return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved)
    except ValueError:
        # Hostname, resolve safely without crashing
        try:
            resolved = socket.gethostbyname(host)
            ip = ipaddress.ip_address(resolved)
            return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved)
        except OSError:
            return True


def _extract_og_image(html: str, base_url: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    for attr_name, attr_val in (
        ("property", "og:image"),
        ("name", "twitter:image"),
        ("property", "og:image:url"),
    ):
        tag = soup.find("meta", attrs={attr_name: attr_val})
        if tag and tag.get("content"):
            candidate = urljoin(base_url, str(tag["content"]).strip())
            if candidate.startswith(("http://", "https://")):
                return candidate
    return None


async def _enrich_single_item(client: httpx.AsyncClient, item: NewsItem) -> None:
    if not is_safe_public_url(item.url):
        return
    try:
        resp = await client.get(
            item.url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; UltraNewsBot/0.1)"},
            follow_redirects=True,
        )
        if resp.status_code >= 400:
            return
        content_type = resp.headers.get("content-type", "").lower()
        if "text/html" not in content_type and "xhtml" not in content_type:
            return
        html = resp.text
    except Exception as exc:
        logger.debug("Article fetch failed for %s: %s", item.url, exc)
        return

    if not item.image_url:
        item.image_url = _extract_og_image(html, str(item.url))

    extracted = await asyncio.to_thread(
        trafilatura.extract,
        html,
        include_comments=False,
        include_tables=False,
    )
    if extracted:
        item.full_text = extracted[:3500].strip()


async def enrich_selected_items(
    client: httpx.AsyncClient,
    items: list[NewsItem],
) -> None:
    """Download full text and cover images for the selected top items."""
    await asyncio.gather(*(_enrich_single_item(client, item) for item in items))
