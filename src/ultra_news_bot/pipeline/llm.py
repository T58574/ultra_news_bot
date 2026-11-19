"""Two-step Gemini batching router with fallback and quota logging."""

import asyncio
import json
import logging
import random
import re
from typing import Any

from google import genai
from google.genai import types

from ..config import Settings
from ..db import BotDatabase
from ..models import DigestEntry, NewsItem, SelectedItem

logger = logging.getLogger(__name__)

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def is_rate_limit_error(exc: Exception) -> bool:
    """Return True if the exception indicates an API rate limit or quota exhaustion."""
    code = getattr(exc, "code", None)
    if code == 429:
        return True
    msg = str(exc).lower()
    return any(
        token in msg
        for token in ("429", "resource_exhausted", "quota", "rate limit", "rate_limit")
    )


def is_transient_error(exc: Exception) -> bool:
    """Return True for temporary overload or rate-limit errors worth retrying once."""
    if is_rate_limit_error(exc):
        return True
    code = getattr(exc, "code", None)
    if code in (500, 502, 503, 504):
        return True
    msg = str(exc).lower()
    return any(token in msg for token in ("503", "unavailable", "high demand", "overloaded"))


class GeminiAllModelsFailedError(RuntimeError):
    """Raised when every candidate Gemini model fails for a pipeline step."""

    def __init__(self, errors: list[tuple[str, Exception]]) -> None:
        self.errors = errors
        self.is_rate_limit = any(is_rate_limit_error(exc) for _, exc in errors)
        summary = "; ".join(f"{model}: {exc}" for model, exc in errors)
        super().__init__(f"All Gemini models failed: {summary}")


def _extract_json(text: str) -> Any:
    match = _JSON_BLOCK_RE.search(text)
    raw = match.group(1).strip() if match else text.strip()
    return json.loads(raw)


class GeminiRouter:
    """Executes 1-2 batched Gemini calls per digest with fallback support."""

    def __init__(self, settings: Settings, db: BotDatabase) -> None:
        self.settings = settings
        self.db = db
        self.warnings: list[str] = []
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

    def _backoff_delay(self, attempt: int) -> float:
        base = max(0.1, float(self.settings.gemini_backoff_base_seconds))
        cap = max(base, float(self.settings.gemini_backoff_max_seconds))
        delay = min(cap, base * (2**attempt))
        return delay + random.uniform(0.0, min(2.0, base * 0.25))

    async def _call_json(self, primary_model: str, system_prompt: str, user_prompt: str) -> tuple[Any, str]:
        if self._client is None:
            raise RuntimeError("GOOGLE_API_KEY is not configured")

        candidates = self._model_candidates(primary_model)
        max_attempts = max(1, int(self.settings.gemini_max_attempts))
        errors: list[tuple[str, Exception]] = []
        rate_limited_models: list[str] = []
        overloaded_models: list[str] = []

        for attempt in range(max_attempts):
            errors = []
            for model in candidates:
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
                    parsed = _extract_json(text)
                    for failed_model in rate_limited_models:
                        if failed_model != model:
                            self.warnings.append(
                                f"{failed_model}: упор в лимит (429), использован fallback -> {model}"
                            )
                    for failed_model in overloaded_models:
                        if failed_model != model and failed_model not in rate_limited_models:
                            self.warnings.append(
                                f"{failed_model}: перегрузка (503/5xx), использован fallback -> {model}"
                            )
                    return parsed, model
                except Exception as exc:
                    errors.append((model, exc))
                    if is_rate_limit_error(exc) and model not in rate_limited_models:
                        rate_limited_models.append(model)
                    elif is_transient_error(exc) and model not in overloaded_models:
                        overloaded_models.append(model)
                    logger.warning(
                        "Gemini call failed on model %s (attempt %d/%d): %s",
                        model,
                        attempt + 1,
                        max_attempts,
                        exc,
                    )

            has_more_attempts = attempt + 1 < max_attempts
            if has_more_attempts and any(is_transient_error(exc) for _, exc in errors):
                delay = self._backoff_delay(attempt)
                logger.info(
                    "Transient Gemini failure across %d candidates (attempt %d/%d); retrying in %.1fs",
                    len(candidates),
                    attempt + 1,
                    max_attempts,
                    delay,
                )
                await asyncio.sleep(delay)
            else:
                break

        raise GeminiAllModelsFailedError(errors)

    async def select_top_items(
        self,
        candidates: list[NewsItem],
        categories_cfg: dict[str, Any],
        top_n: int,
        recent_titles: list[str] | None = None,
        edition_focus: str = "",
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
            "You are a senior tech, UX, product, and digital marketing news editor. "
            "Select the most impactful, non-duplicate stories for a UX/product marketer and tech generalist. "
            "Prioritize AI products & major model launches, UX/UI research, product design, tech marketing & growth, "
            "big tech strategy, consumer gadgets, and accessible science/space breakthroughs. "
            "SKIP niche developer tooling, compilers, CI/testing internals, low-level exploits, router hacks, "
            "and obscure CLI utilities unless they have major mainstream product impact. "
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
        focus_block = f"Edition focus: {edition_focus}\n" if edition_focus else ""
        user_prompt = (
            f"Pick up to {top_n} best items from these {len(compact_list)} candidates:\n"
            + focus_block
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
            "Ты редактор технологического дайджеста для UX-специалиста и продуктового маркетолога. "
            "Пиши на русском языке, живым и понятным языком, без хардкорного программистского жаргона и воды. "
            "Для каждой новости выдай:\n"
            "- headline: цепляющий заголовок;\n"
            "- summary: выжимку в 2–3 предложения (что произошло и как это влияет на пользователей, продукт или рынок);\n"
            "- insight: одно короткое предложение (до 15 слов) с практическим выводом для UX, продукта или маркетинга.\n"
            'Верни строгий JSON: {"entries": [{"uid": "...", "headline": "...", "summary": "...", "insight": "..."}]}'
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
                    insight=str(raw.get("insight") or "").strip(),
                    source_label=source_label,
                    url=item.url,
                    discussion_url=item.discussion_url,
                    image_url=item.image_url,
                )
            )
        return entries, used_model
