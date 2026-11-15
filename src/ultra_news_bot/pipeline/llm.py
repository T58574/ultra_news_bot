"""Two-step Gemini batching router with fallback and quota logging."""

import asyncio
import json
import logging
import re
from typing import Any

from google import genai
from google.genai import types

from ..config import Settings
from ..db import BotDatabase
from ..models import DigestEntry, NewsItem, SelectedItem

logger = logging.getLogger(__name__)

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def _extract_json(text: str) -> Any:
    match = _JSON_BLOCK_RE.search(text)
    raw = match.group(1).strip() if match else text.strip()
    return json.loads(raw)


class GeminiRouter:
    """Executes 1-2 batched Gemini calls per digest with fallback support."""

    def __init__(self, settings: Settings, db: BotDatabase) -> None:
        self.settings = settings
        self.db = db
        self._client = (
            genai.Client(api_key=settings.google_api_key)
            if settings.google_api_key
            else None
        )

    def _model_candidates(self, primary: str) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for name in [primary, *self.settings.gemini_fallback_models]:
            if name and name not in seen:
                seen.add(name)
                ordered.append(name)
        return ordered

    async def _call_json(self, primary_model: str, system_prompt: str, user_prompt: str) -> tuple[Any, str]:
        if self._client is None:
            raise RuntimeError("GOOGLE_API_KEY is not configured")

        last_exc: Exception | None = None
        for model in self._model_candidates(primary_model):
            try:
                response = await asyncio.to_thread(
                    self._client.models.generate_content,
                    model=model,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        temperature=0.2,
                        response_mime_type="application/json",
                    ),
                )
                self.db.record_llm_call(model)
                text = response.text or ""
                return _extract_json(text), model
            except Exception as exc:
                last_exc = exc
                logger.warning("Gemini call failed on model %s: %s", model, exc)
        raise RuntimeError(f"All Gemini models failed: {last_exc}")

    async def select_top_items(
        self,
        candidates: list[NewsItem],
        categories_cfg: dict[str, Any],
        top_n: int,
        recent_titles: list[str] | None = None,
    ) -> tuple[list[SelectedItem], str]:
        """Step 1: pick top_n non-duplicate high-signal items from candidates."""
        cat_desc = ", ".join(
            f"{cid} ({cfg['label']})"
            for cid, cfg in categories_cfg.items()
            if cfg.get("enabled", True)
        )
        compact_list = [
            {
                "uid": item.uid,
                "src": item.source_name,
                "cat": item.category,
                "score": item.score,
                "title": item.title,
                "snippet": item.snippet[:220],
            }
            for item in candidates
        ]

        system_prompt = (
            "You are a senior tech & science news editor. "
            "Select the most impactful, non-duplicate stories for a personal morning/evening briefing. "
            "Merge stories about the exact same event (keep the highest-signal uid). "
            "Balance across active categories when quality permits. "
            f"Active categories: {cat_desc}. "
            'Return strict JSON: {"selected": [{"uid": "...", "category": "...", "reason": "short"}]}'
        )
        recent_block = ""
        if recent_titles:
            recent_block = (
                "\nRecently covered topics (DO NOT select stories about the same events):\n"
                + json.dumps(recent_titles[:25], ensure_ascii=False)
                + "\n"
            )
        user_prompt = (
            f"Pick up to {top_n} best items from these {len(compact_list)} candidates:\n"
            + recent_block
            + json.dumps(compact_list, ensure_ascii=False)
        )

        data, used_model = await self._call_json(
            self.settings.gemini_filter_model, system_prompt, user_prompt
        )
        raw_selected = data.get("selected", []) if isinstance(data, dict) else []
        valid_uids = {item.uid for item in candidates}
        selected: list[SelectedItem] = []
        seen_uids: set[str] = set()
        for entry in raw_selected:
            uid = str(entry.get("uid", ""))
            if uid in valid_uids and uid not in seen_uids:
                seen_uids.add(uid)
                selected.append(
                    SelectedItem(
                        uid=uid,
                        category=str(entry.get("category") or "code"),
                        reason=str(entry.get("reason") or ""),
                    )
                )
        return selected[:top_n], used_model

    async def summarize_digest(
        self,
        items: list[NewsItem],
    ) -> tuple[list[DigestEntry], str]:
        """Step 2: write crisp Russian summaries for the selected items in one call."""
        payload = [
            {
                "uid": item.uid,
                "category": item.category,
                "source": item.source_name,
                "score": item.score,
                "title": item.title,
                "url": item.url,
                "discussion_url": item.discussion_url,
                "text": (item.full_text or item.snippet or item.title)[:2500],
            }
            for item in items
        ]

        system_prompt = (
            "Ты редактор хакерского tech/science дайджеста для опытного разработчика. "
            "Пиши на русском языке, кратко, по делу, без воды и клише. "
            "Для каждой новости выдай цепляющий заголовок и выжимку в 2–3 предложения: "
            "что произошло, технические детали/цифры и почему это важно. "
            'Верни строгий JSON: {"entries": [{"uid": "...", "headline": "...", "summary": "..."}]}'
        )
        user_prompt = json.dumps(payload, ensure_ascii=False)

        data, used_model = await self._call_json(
            self.settings.gemini_summary_model, system_prompt, user_prompt
        )
        raw_entries = data.get("entries", []) if isinstance(data, dict) else []
        by_uid = {item.uid: item for item in items}

        entries: list[DigestEntry] = []
        for raw in raw_entries:
            uid = str(raw.get("uid", ""))
            item = by_uid.get(uid)
            if not item:
                continue
            metric = f"{item.score}↑" if item.score > 0 else "RSS"
            source_label = f"{item.source_name}: {metric}"
            entries.append(
                DigestEntry(
                    uid=uid,
                    category=item.category,
                    headline=str(raw.get("headline") or item.title).strip(),
                    summary=str(raw.get("summary") or item.snippet).strip(),
                    source_label=source_label,
                    url=item.url,
                    discussion_url=item.discussion_url,
                    image_url=item.image_url,
                )
            )
        return entries, used_model
