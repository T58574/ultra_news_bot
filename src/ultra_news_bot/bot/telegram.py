"""Lightweight Telegram Bot API sender using httpx (no aiogram)."""

import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


class TelegramAPIError(RuntimeError):
    """Raised when Telegram Bot API returns an error response."""


class TelegramClient:
    """Minimal async Telegram Bot API client for one-shot digest delivery."""

    def __init__(self, token: str, timeout_seconds: float = 20.0) -> None:
        self._base_url = f"https://api.telegram.org/bot{token}"
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def __aenter__(self) -> "TelegramClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def _post(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        resp = await self._client.post(f"{self._base_url}/{method}", json=payload)
        try:
            data = resp.json()
        except Exception as exc:
            resp.raise_for_status()
            raise TelegramAPIError(f"Telegram {method}: invalid JSON response") from exc

        if resp.status_code >= 400 or not data.get("ok"):
            desc = data.get("description") or resp.text[:300]
            raise TelegramAPIError(f"Telegram {method} failed ({resp.status_code}): {desc}")
        return data

    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        parse_mode: str = "HTML",
        disable_web_page_preview: bool = True,
    ) -> dict[str, Any]:
        return await self._post(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": disable_web_page_preview,
            },
        )

    async def send_photo(
        self,
        chat_id: int | str,
        photo: str,
        caption: Optional[str] = None,
        parse_mode: str = "HTML",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "photo": photo,
            "parse_mode": parse_mode,
        }
        if caption is not None:
            payload["caption"] = caption
        return await self._post("sendPhoto", payload)


async def send_digest_to_chat(
    telegram: TelegramClient,
    chat_id: int | str,
    messages: list[str],
    cover_image_url: Optional[str] = None,
) -> None:
    """Deliver cover photo (if any) and formatted HTML chunks to Telegram."""
    if cover_image_url:
        try:
            await telegram.send_photo(
                chat_id=chat_id,
                photo=cover_image_url,
                caption="<code>⚡ [ NETRUNNER // VISUAL_FEED ]</code>",
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.debug("Cover photo skipped (%s): %s", cover_image_url, exc)

    for chunk in messages:
        await telegram.send_message(
            chat_id=chat_id,
            text=chunk,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
