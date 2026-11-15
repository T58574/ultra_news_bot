"""Algorithmic deduplication and pre-LLM ranking."""

import math
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..db import BotDatabase
from ..models import NewsItem

_TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "ref",
    "source",
    "fbclid",
    "gclid",
}
_WORD_RE = re.compile(r"[a-z0-9а-яё]{3,}", re.IGNORECASE)


def normalize_url(url: str) -> str:
    """Normalize URL for cross-source deduplication."""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path.rstrip("/") or "/"
    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in _TRACKING_PARAMS
    ]
    query = urlencode(sorted(query_pairs))
    return urlunsplit((parts.scheme.lower() or "https", host, path, query, ""))


def _title_tokens(title: str) -> set[str]:
    return set(_WORD_RE.findall(title.lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _compute_rank(item: NewsItem, categories_cfg: dict[str, Any], now: datetime) -> float:
    age_hours = max(0.0, (now - item.published_at).total_seconds() / 3600.0)
    freshness = max(0.0, 24.0 - age_hours) * 0.25

    # RSS feeds often have score=0, give them a baseline so they compete fairly with HN/Reddit
    if item.source_kind == "rss":
        engagement = 3.5
    else:
        engagement = math.log1p(max(0, item.score)) * 1.4 + math.log1p(max(0, item.comments_count)) * 0.6

    text_lower = f"{item.title} {item.snippet}".lower()
    kw_bonus = 0.0
    matched_cat = item.category
    best_hits = 0
    for cat_id, cat_cfg in categories_cfg.items():
        if not cat_cfg.get("enabled", True):
            continue
        hits = sum(1 for kw in cat_cfg.get("keywords", []) if kw.lower() in text_lower)
        if hits > best_hits:
            best_hits = hits
            matched_cat = cat_id
    if best_hits > 0:
        kw_bonus = min(3.0, best_hits * 1.1)
        item.category = matched_cat

    return round(engagement + freshness + kw_bonus, 3)


def deduplicate_and_rank(
    items: list[NewsItem],
    categories_cfg: dict[str, Any],
    db: BotDatabase,
    max_candidates: int = 70,
) -> list[NewsItem]:
    """Filter seen URLs, collapse duplicates, score, and cap candidates for LLM."""
    now = datetime.now(timezone.utc)
    enabled_cats = {k for k, v in categories_cfg.items() if v.get("enabled", True)}

    seen_urls_in_batch: set[str] = set()
    kept_tokens: list[set[str]] = []
    unique_items: list[NewsItem] = []

    for item in items:
        norm_url = normalize_url(item.url)
        if norm_url in seen_urls_in_batch or db.is_seen(norm_url):
            continue

        tokens = _title_tokens(item.title)
        if any(_jaccard(tokens, prev) >= 0.72 for prev in kept_tokens):
            continue

        item.rank_score = _compute_rank(item, categories_cfg, now)
        if enabled_cats and item.category not in enabled_cats:
            continue

        seen_urls_in_batch.add(norm_url)
        kept_tokens.append(tokens)
        unique_items.append(item)

    unique_items.sort(key=lambda x: x.rank_score, reverse=True)
    return unique_items[:max_candidates]
