"""End-to-end digest pipeline orchestrator."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx

from ..config import Settings, load_sources_config
from ..db import BotDatabase
from ..models import DigestPayload, NewsItem
from ..scrapers import fetch_github, fetch_hackernews, fetch_reddit, fetch_rss_feeds
from .extractor import enrich_selected_items
from .formatter import render_digest_messages
from .llm import GeminiRouter
from .ranker import deduplicate_and_rank, normalize_url

logger = logging.getLogger(__name__)


async def run_digest_pipeline(settings: Settings, db: BotDatabase) -> DigestPayload:
    """Collect sources, rank algorithmically, run 2-step Gemini batch, and format."""
    sources_cfg = load_sources_config(settings.sources_path)
    categories_cfg = sources_cfg.get("categories", {})
    since = datetime.now(timezone.utc) - timedelta(hours=settings.lookback_hours)

    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        hn_cfg = sources_cfg.get("hackernews", {})
        hn_task = (
            fetch_hackernews(
                client,
                since=since,
                limit=int(hn_cfg.get("limit", 40)),
                min_score=int(hn_cfg.get("min_score", 50)),
            )
            if hn_cfg.get("enabled", True)
            else asyncio.sleep(0, result=[])
        )
        reddit_task = fetch_reddit(client, sources_cfg.get("reddit", {}), since=since)
        github_task = fetch_github(
            client, sources_cfg.get("github", {}), github_token=settings.github_token
        )
        rss_task = fetch_rss_feeds(client, sources_cfg.get("rss", []), since=since)

        hn_items, reddit_items, github_items, rss_items = await asyncio.gather(
            hn_task, reddit_task, github_task, rss_task
        )
        all_items: list[NewsItem] = [*hn_items, *reddit_items, *github_items, *rss_items]
        logger.info("Collected %d raw items", len(all_items))

        candidates = deduplicate_and_rank(
            all_items,
            categories_cfg=categories_cfg,
            db=db,
            max_candidates=settings.max_candidates_for_llm,
        )

        now = datetime.now(timezone.utc)
        if not candidates:
            messages = render_digest_messages(
                entries=[],
                categories_cfg=categories_cfg,
                created_at=now,
                total_scanned=len(all_items),
                candidates_sent=0,
                model_filter=settings.gemini_filter_model,
                model_summary=settings.gemini_summary_model,
            )
            return DigestPayload(
                created_at=now,
                total_scanned=len(all_items),
                candidates_sent=0,
                model_filter=settings.gemini_filter_model,
                model_summary=settings.gemini_summary_model,
                rendered_messages=messages,
            )

        router = GeminiRouter(settings, db)
        selected_meta, used_filter_model = await router.select_top_items(
            candidates,
            categories_cfg=categories_cfg,
            top_n=settings.top_items_to_summarize,
        )
        by_uid = {item.uid: item for item in candidates}
        selected_items: list[NewsItem] = []
        for meta in selected_meta:
            item = by_uid.get(meta.uid)
            if item:
                item.category = meta.category
                selected_items.append(item)

        if not selected_items:
            selected_items = candidates[: settings.top_items_to_summarize]

        await enrich_selected_items(client, selected_items)
        entries, used_summary_model = await router.summarize_digest(selected_items)

        cover_image_url = next((e.image_url for e in entries if e.image_url), None)
        messages = render_digest_messages(
            entries=entries,
            categories_cfg=categories_cfg,
            created_at=now,
            total_scanned=len(all_items),
            candidates_sent=len(candidates),
            model_filter=used_filter_model,
            model_summary=used_summary_model,
        )

        db.mark_seen([(normalize_url(item.url), item.title) for item in selected_items])
        db.save_digest(
            created_at=now,
            cover_image_url=cover_image_url,
            messages=messages,
            total_scanned=len(all_items),
            selected_count=len(entries),
        )

        return DigestPayload(
            created_at=now,
            total_scanned=len(all_items),
            candidates_sent=len(candidates),
            model_filter=used_filter_model,
            model_summary=used_summary_model,
            cover_image_url=cover_image_url,
            entries=entries,
            rendered_messages=messages,
        )
