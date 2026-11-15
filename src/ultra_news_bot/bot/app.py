"""Bot entrypoint with APScheduler for morning/evening digests."""

import asyncio
import logging
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..config import Settings
from ..db import BotDatabase
from ..pipeline import run_digest_pipeline
from .handlers import build_router, send_digest_to_chat

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _parse_hhmm(raw: str, default: tuple[int, int]) -> tuple[int, int]:
    try:
        hh, mm = raw.strip().split(":", 1)
        return int(hh), int(mm)
    except Exception:
        return default


async def _scheduled_digest_job(bot: Bot, settings: Settings, db: BotDatabase) -> None:
    if db.get_state("schedule_enabled", "1") != "1":
        logger.info("Scheduled digest skipped (disabled in bot state)")
        return

    chat_id_raw = db.get_state("owner_chat_id")
    if not chat_id_raw:
        allowed = sorted(settings.allowed_user_ids)
        if not allowed:
            logger.warning("No owner_chat_id yet; press /start in Telegram first")
            return
        chat_id = allowed[0]
    else:
        chat_id = int(chat_id_raw)

    logger.info("Running scheduled digest for chat_id=%s", chat_id)
    try:
        payload = await run_digest_pipeline(settings, db)
        await send_digest_to_chat(
            bot, chat_id, payload.rendered_messages, payload.cover_image_url
        )
    except Exception:
        logger.exception("Scheduled digest failed")


async def _run() -> None:
    settings = Settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing in .env")

    db = BotDatabase(settings.db_path)
    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher()
    dp.include_router(build_router(settings, db))

    tz = ZoneInfo(settings.timezone)
    scheduler = AsyncIOScheduler(timezone=tz)

    m_h, m_m = _parse_hhmm(settings.morning_digest_time, (9, 0))
    e_h, e_m = _parse_hhmm(settings.evening_digest_time, (20, 0))

    scheduler.add_job(
        _scheduled_digest_job,
        CronTrigger(hour=m_h, minute=m_m, timezone=tz),
        args=[bot, settings, db],
        id="morning_digest",
    )
    scheduler.add_job(
        _scheduled_digest_job,
        CronTrigger(hour=e_h, minute=e_m, timezone=tz),
        args=[bot, settings, db],
        id="evening_digest",
    )
    scheduler.start()
    logger.info(
        "Ultra News Bot started. Schedule: %02d:%02d & %02d:%02d (%s)",
        m_h,
        m_m,
        e_h,
        e_m,
        settings.timezone,
    )

    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
