"""Aiogram handlers for the personal netrunner news terminal."""

import html
import logging
from typing import Optional

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from ..config import Settings, load_sources_config, save_sources_config
from ..db import BotDatabase
from ..pipeline import run_digest_pipeline
from ..pipeline.extractor import is_safe_public_url
from .keyboards import (
    BTN_DIGEST,
    BTN_SCHEDULE,
    BTN_SOURCES,
    BTN_STATUS,
    BTN_TOPICS,
    digest_actions_keyboard,
    main_menu_keyboard,
    schedule_keyboard,
    sources_keyboard,
    topics_keyboard,
)

logger = logging.getLogger(__name__)


class AddRssState(StatesGroup):
    waiting_for_url = State()


def _is_authorized(user_id: Optional[int], settings: Settings) -> bool:
    allowed = settings.allowed_user_ids
    if not allowed:
        return True
    return user_id in allowed


async def send_digest_to_chat(
    bot: Bot,
    chat_id: int,
    messages: list[str],
    cover_image_url: Optional[str] = None,
) -> None:
    """Deliver cover photo (if any) and formatted HTML chunks to Telegram."""
    if cover_image_url:
        try:
            await bot.send_photo(
                chat_id=chat_id,
                photo=cover_image_url,
                caption="<code>⚡ [ NETRUNNER // VISUAL_FEED ]</code>",
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.debug("Cover photo skipped (%s): %s", cover_image_url, exc)

    for chunk in messages:
        await bot.send_message(
            chat_id=chat_id,
            text=chunk,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


def build_router(settings: Settings, db: BotDatabase) -> Router:
    router = Router()

    @router.message(CommandStart())
    async def cmd_start(message: Message) -> None:
        if not _is_authorized(message.from_user.id if message.from_user else None, settings):
            await message.answer("<code>[403] ACCESS DENIED</code>", parse_mode="HTML")
            return

        if message.chat:
            db.set_state("owner_chat_id", str(message.chat.id))
            if not db.get_state("schedule_enabled"):
                db.set_state("schedule_enabled", "1")

        uid = message.from_user.id if message.from_user else "?"
        await message.answer(
            f"⚡ <b>[ NETRUNNER // TERMINAL ONLINE ]</b>\n"
            f"<code>&gt; OPERATOR_ID: {uid}</code>\n"
            f"<code>&gt; FILTER: {html.escape(settings.gemini_filter_model)}</code>\n"
            f"<code>&gt; SUMMARY: {html.escape(settings.gemini_summary_model)}</code>\n\n"
            f"Используй кнопки меню ниже для управления лентой.",
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
        )

    @router.message(F.text == BTN_DIGEST)
    async def menu_digest(message: Message) -> None:
        if not _is_authorized(message.from_user.id if message.from_user else None, settings):
            return
        has_cache = db.get_latest_digest() is not None
        await message.answer(
            "📡 <b>[ ДАЙДЖЕСТ // УПРАВЛЕНИЕ ]</b>\n"
            "<blockquote>Собрать свежую сводку из HN, Reddit, GitHub и RSS "
            "(расход: 2 запроса к Gemini) или открыть последний кеш?</blockquote>",
            parse_mode="HTML",
            reply_markup=digest_actions_keyboard(has_cache),
        )

    @router.callback_query(F.data == "digest:cached")
    async def cb_digest_cached(callback: CallbackQuery) -> None:
        if not _is_authorized(callback.from_user.id, settings):
            await callback.answer("403", show_alert=True)
            return
        await callback.answer("Загружаю кеш...")
        latest = db.get_latest_digest()
        if not latest or not callback.message:
            await callback.message.answer("<code>[!] Кеш пуст. Запусти свежую сборку.</code>", parse_mode="HTML")
            return
        await send_digest_to_chat(
            callback.bot,
            callback.message.chat.id,
            latest["messages"],
            latest.get("cover_image_url"),
        )

    @router.callback_query(F.data == "digest:run")
    async def cb_digest_run(callback: CallbackQuery) -> None:
        if not _is_authorized(callback.from_user.id, settings):
            await callback.answer("403", show_alert=True)
            return
        await callback.answer("Сканирую источники...")
        if not callback.message:
            return
        status_msg = await callback.message.answer(
            "<code>⏳ [ SCANNING SOURCES -&gt; RANKING -&gt; GEMINI BATCH... ]</code>",
            parse_mode="HTML",
        )
        try:
            payload = await run_digest_pipeline(settings, db)
            await status_msg.delete()
            await send_digest_to_chat(
                callback.bot,
                callback.message.chat.id,
                payload.rendered_messages,
                payload.cover_image_url,
            )
        except Exception as exc:
            logger.exception("Manual digest failed")
            await status_msg.edit_text(
                f"<code>[ERR] Сбой пайплайна: {html.escape(str(exc)[:300])}</code>",
                parse_mode="HTML",
            )

    @router.message(F.text == BTN_TOPICS)
    async def menu_topics(message: Message) -> None:
        if not _is_authorized(message.from_user.id if message.from_user else None, settings):
            return
        cfg = load_sources_config(settings.sources_path)
        await message.answer(
            "🧩 <b>[ ТЕМЫ // ФИЛЬТРЫ ]</b>\n"
            "<blockquote>Включай и выключай категории новостей одним нажатием:</blockquote>",
            parse_mode="HTML",
            reply_markup=topics_keyboard(cfg.get("categories", {})),
        )

    @router.callback_query(F.data.startswith("topic:toggle:"))
    async def cb_topic_toggle(callback: CallbackQuery) -> None:
        if not _is_authorized(callback.from_user.id, settings):
            await callback.answer("403", show_alert=True)
            return
        cat_id = callback.data.split(":", 2)[2]
        cfg = load_sources_config(settings.sources_path)
        cats = cfg.get("categories", {})
        if cat_id in cats:
            cats[cat_id]["enabled"] = not cats[cat_id].get("enabled", True)
            save_sources_config(settings.sources_path, cfg)
        await callback.answer("Обновлено")
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=topics_keyboard(cats))

    @router.message(F.text == BTN_SOURCES)
    async def menu_sources(message: Message) -> None:
        if not _is_authorized(message.from_user.id if message.from_user else None, settings):
            return
        cfg = load_sources_config(settings.sources_path)
        await message.answer(
            "🎛 <b>[ ИСТОЧНИКИ // КАНАЛЫ ]</b>\n"
            "<blockquote>Нажми на источник чтобы вкл/выкл, или добавь свой RSS:</blockquote>",
            parse_mode="HTML",
            reply_markup=sources_keyboard(cfg),
        )

    @router.callback_query(F.data.startswith("src:toggle:"))
    async def cb_src_toggle(callback: CallbackQuery) -> None:
        if not _is_authorized(callback.from_user.id, settings):
            await callback.answer("403", show_alert=True)
            return
        key = callback.data.split(":", 2)[2]
        cfg = load_sources_config(settings.sources_path)
        if key in cfg and isinstance(cfg[key], dict):
            cfg[key]["enabled"] = not cfg[key].get("enabled", True)
            save_sources_config(settings.sources_path, cfg)
        await callback.answer("Обновлено")
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=sources_keyboard(cfg))

    @router.callback_query(F.data.startswith("rss:toggle:"))
    async def cb_rss_toggle(callback: CallbackQuery) -> None:
        if not _is_authorized(callback.from_user.id, settings):
            await callback.answer("403", show_alert=True)
            return
        idx_str = callback.data.split(":", 2)[2]
        cfg = load_sources_config(settings.sources_path)
        rss_list = cfg.get("rss", [])
        if idx_str.isdigit() and 0 <= int(idx_str) < len(rss_list):
            idx = int(idx_str)
            rss_list[idx]["enabled"] = not rss_list[idx].get("enabled", True)
            save_sources_config(settings.sources_path, cfg)
        await callback.answer("Обновлено")
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=sources_keyboard(cfg))

    @router.callback_query(F.data == "rss:add")
    async def cb_rss_add(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_authorized(callback.from_user.id, settings):
            await callback.answer("403", show_alert=True)
            return
        await state.set_state(AddRssState.waiting_for_url)
        await callback.answer()
        if callback.message:
            await callback.message.answer(
                "<code>&gt; Пришли URL RSS/Atom ленты (например: https://example.com/feed.xml)\n"
                "&gt; Или отправь 'отмена'.</code>",
                parse_mode="HTML",
            )

    @router.message(AddRssState.waiting_for_url)
    async def handle_rss_url(message: Message, state: FSMContext) -> None:
        if not _is_authorized(message.from_user.id if message.from_user else None, settings):
            return
        text = (message.text or "").strip()
        if text.lower() in {"отмена", "cancel", "/cancel"}:
            await state.clear()
            await message.answer("<code>[x] Отменено.</code>", parse_mode="HTML")
            return
        if not is_safe_public_url(text):
            await message.answer(
                "<code>[!] Некорректный или небезопасный URL. Нужен публичный http/https.</code>",
                parse_mode="HTML",
            )
            return

        cfg = load_sources_config(settings.sources_path)
        rss_list = cfg.setdefault("rss", [])
        if any(r.get("url") == text for r in rss_list):
            await state.clear()
            await message.answer("<code>[!] Этот RSS уже есть в списке.</code>", parse_mode="HTML")
            return

        host = text.split("/")[2]
        rss_list.append({"name": host, "url": text, "category": "code", "enabled": True})
        save_sources_config(settings.sources_path, cfg)
        await state.clear()
        await message.answer(
            f"<code>[+] Добавлен RSS: {html.escape(host)}</code>",
            parse_mode="HTML",
        )

    @router.message(F.text == BTN_SCHEDULE)
    async def menu_schedule(message: Message) -> None:
        if not _is_authorized(message.from_user.id if message.from_user else None, settings):
            return
        enabled = db.get_state("schedule_enabled", "1") == "1"
        await message.answer(
            f"⏱ <b>[ РАСПИСАНИЕ // CRON ]</b>\n"
            f"<code>&gt; TZ: {html.escape(settings.timezone)}</code>\n"
            f"<blockquote>Автоматическая отправка утреннего и вечернего дайджеста:</blockquote>",
            parse_mode="HTML",
            reply_markup=schedule_keyboard(
                enabled, settings.morning_digest_time, settings.evening_digest_time
            ),
        )

    @router.callback_query(F.data == "sched:toggle")
    async def cb_sched_toggle(callback: CallbackQuery) -> None:
        if not _is_authorized(callback.from_user.id, settings):
            await callback.answer("403", show_alert=True)
            return
        current = db.get_state("schedule_enabled", "1") == "1"
        new_val = not current
        db.set_state("schedule_enabled", "1" if new_val else "0")
        await callback.answer("ВКЛ" if new_val else "ВЫКЛ")
        if callback.message:
            await callback.message.edit_reply_markup(
                reply_markup=schedule_keyboard(
                    new_val, settings.morning_digest_time, settings.evening_digest_time
                )
            )

    @router.callback_query(F.data == "sched:noop")
    async def cb_sched_noop(callback: CallbackQuery) -> None:
        await callback.answer("Время меняется через .env (MORNING_DIGEST_TIME / EVENING_DIGEST_TIME)")

    @router.message(F.text == BTN_STATUS)
    async def menu_status(message: Message) -> None:
        if not _is_authorized(message.from_user.id if message.from_user else None, settings):
            return
        usage = db.get_today_usage()
        usage_lines = "\n".join(
            f"  • {html.escape(model)}: {count} req" for model, count in usage.items()
        ) or "  • 0 запросов сегодня"
        seen_count = db.count_seen()
        sched = "ON" if db.get_state("schedule_enabled", "1") == "1" else "OFF"
        await message.answer(
            f"📊 <b>[ СИСТЕМА // ТЕЛЕМЕТРИЯ ]</b>\n"
            f"<code>&gt; FILTER_MODEL : {html.escape(settings.gemini_filter_model)}</code>\n"
            f"<code>&gt; SUMMARY_MODEL: {html.escape(settings.gemini_summary_model)}</code>\n"
            f"<code>&gt; SEEN_URLS_DB : {seen_count}</code>\n"
            f"<code>&gt; AUTO_DIGEST  : {sched}</code>\n\n"
            f"<b>Расход Gemini API за сегодня (UTC):</b>\n<code>{usage_lines}</code>",
            parse_mode="HTML",
        )

    return router
