"""One-shot digest runner intended for systemd timer / cron or manual CLI runs."""

import asyncio
import html
import logging
import sys

from ..config import Settings
from ..db import BotDatabase
from ..pipeline import run_digest_pipeline
from ..pipeline.llm import GeminiAllModelsFailedError, is_rate_limit_error
from .telegram import TelegramClient, send_digest_to_chat

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
# Prevent httpx from logging full Telegram Bot API URLs containing TELEGRAM_BOT_TOKEN
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def _resolve_admin_chat_id(settings: Settings, db: BotDatabase) -> int | None:
    chat_id_raw = db.get_state("owner_chat_id")
    if chat_id_raw:
        return int(chat_id_raw)
    allowed = sorted(settings.allowed_user_ids)
    return allowed[0] if allowed else None


async def _notify_admin(
    telegram: TelegramClient,
    admin_chat_id: int | None,
    text: str,
) -> None:
    if admin_chat_id is None:
        return
    try:
        await telegram.send_message(chat_id=admin_chat_id, text=text, parse_mode="HTML")
    except Exception:
        logger.exception("Failed to send admin alert to chat_id=%s", admin_chat_id)


async def run_once(
    telegram: TelegramClient,
    settings: Settings,
    db: BotDatabase,
) -> bool:
    """Run a single digest pipeline and deliver results. Returns True on success."""
    admin_chat_id = _resolve_admin_chat_id(settings, db)
    target_chat_id: int | str | None = settings.digest_target_chat_id or admin_chat_id
    if target_chat_id is None:
        logger.error("No TELEGRAM_CHANNEL_ID or ALLOWED_USER_IDS configured")
        return False

    logger.info("Running one-shot digest for target=%s", target_chat_id)
    try:
        payload = await run_digest_pipeline(settings, db)
        await send_digest_to_chat(
            telegram,
            target_chat_id,
            payload.rendered_messages,
            payload.cover_image_url,
        )
        if payload.warnings:
            warn_text = "\n".join(f"• {w}" for w in payload.warnings)
            await _notify_admin(
                telegram,
                admin_chat_id,
                f"⚠️ <b>[ALERT // GEMINI FALLBACK WARNING]</b>\n"
                f"<code>Дайджест отправлен в {html.escape(str(target_chat_id))}, "
                f"но были срабатывания фолбека/лимитов:\n{html.escape(warn_text)}</code>",
            )
        logger.info(
            "Digest delivered to %s (scanned=%d, selected=%d, models=%s/%s)",
            target_chat_id,
            payload.total_scanned,
            len(payload.entries),
            payload.model_filter,
            payload.model_summary,
        )
        return True
    except Exception as exc:
        logger.exception("Digest pipeline failed")
        is_limit = (
            isinstance(exc, GeminiAllModelsFailedError) and exc.is_rate_limit
        ) or is_rate_limit_error(exc)
        header = (
            "⚠️ <b>[ALERT // GEMINI RATE LIMIT]</b>\n"
            if is_limit
            else "⚠️ <b>[ALERT // DIGEST FAILED]</b>\n"
        )
        reason = (
            "Дайджест не отправлен: упор в лимиты Gemini API."
            if is_limit
            else "Дайджест не отправлен из-за сбоя пайплайна."
        )
        await _notify_admin(
            telegram,
            admin_chat_id,
            f"{header}<code>{reason}\n{html.escape(str(exc)[:600])}</code>",
        )
        return False


async def _run() -> int:
    settings = Settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing in .env")

    db = BotDatabase(settings.db_path)
    async with TelegramClient(
        token=settings.telegram_bot_token,
        timeout_seconds=settings.http_timeout_seconds,
    ) as telegram:
        ok = await run_once(telegram, settings, db)
    return 0 if ok else 1


def main() -> None:
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
