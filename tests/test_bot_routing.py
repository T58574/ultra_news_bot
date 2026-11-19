"""Tests for one-shot channel routing, Telegram httpx sender, and Gemini backoff/fallback."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ultra_news_bot.bot.app import run_once
from ultra_news_bot.bot.telegram import send_digest_to_chat
from ultra_news_bot.config import Settings
from ultra_news_bot.db import BotDatabase
from ultra_news_bot.models import DigestPayload
from ultra_news_bot.pipeline.llm import (
    GeminiAllModelsFailedError,
    GeminiRouter,
    is_rate_limit_error,
)


def test_digest_target_chat_id() -> None:
    s1 = Settings(TELEGRAM_CHANNEL_ID=" @ultranewschannel ", _env_file=None)
    assert s1.digest_target_chat_id == "@ultranewschannel"
    s2 = Settings(TELEGRAM_CHANNEL_ID="", _env_file=None)
    assert s2.digest_target_chat_id is None


@pytest.mark.asyncio
async def test_gemini_router_records_rate_limit_warning_on_fallback(tmp_path: Path) -> None:
    settings = Settings(
        GOOGLE_API_KEY="test-key",
        GEMINI_FILTER_MODEL="gemini-3.1-flash-lite",
        GEMINI_FALLBACK_MODELS="gemini-3.5-flash-lite,gemini-3.5-flash",
        _env_file=None,
    )
    db = BotDatabase(tmp_path / "bot.db")
    router = GeminiRouter(settings, db)

    ok_resp = MagicMock()
    ok_resp.text = '{"selected": []}'

    calls: list[str] = []

    def fake_generate_content(*, model: str, **kwargs: object) -> MagicMock:
        calls.append(model)
        if model == "gemini-3.1-flash-lite":
            raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")
        return ok_resp

    router._client = MagicMock()
    router._client.models.generate_content = fake_generate_content

    data, used = await router._call_json("gemini-3.1-flash-lite", "sys", "usr")
    assert data == {"selected": []}
    assert used == "gemini-3.5-flash-lite"
    assert calls == ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite"]
    assert len(router.warnings) == 1
    assert "429" in router.warnings[0]


@pytest.mark.asyncio
async def test_gemini_router_falls_back_across_tiers_on_503(tmp_path: Path) -> None:
    settings = Settings(
        GOOGLE_API_KEY="test-key",
        GEMINI_FILTER_MODEL="gemini-3.1-flash-lite",
        GEMINI_FALLBACK_MODELS="gemini-3.5-flash-lite,gemini-3.5-flash,gemini-2.5-flash",
        _env_file=None,
    )
    db = BotDatabase(tmp_path / "bot.db")
    router = GeminiRouter(settings, db)

    ok_resp = MagicMock()
    ok_resp.text = '{"selected": []}'
    calls: list[str] = []

    def fake_generate_content(*, model: str, **kwargs: object) -> MagicMock:
        calls.append(model)
        if model.endswith("-lite"):
            raise RuntimeError("503 UNAVAILABLE: This model is currently experiencing high demand.")
        return ok_resp

    router._client = MagicMock()
    router._client.models.generate_content = fake_generate_content

    data, used = await router._call_json("gemini-3.1-flash-lite", "sys", "usr")
    assert data == {"selected": []}
    assert used == "gemini-3.5-flash"
    assert calls == ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-3.5-flash"]
    assert len(router.warnings) == 2
    assert all("503" in w for w in router.warnings)


@pytest.mark.asyncio
async def test_gemini_router_exponential_backoff_on_503_then_succeeds(tmp_path: Path) -> None:
    settings = Settings(
        GOOGLE_API_KEY="test-key",
        GEMINI_FILTER_MODEL="gemini-3.1-flash-lite",
        GEMINI_FALLBACK_MODELS="gemini-3.5-flash",
        GEMINI_MAX_ATTEMPTS=4,
        GEMINI_BACKOFF_BASE_SECONDS=10.0,
        GEMINI_BACKOFF_MAX_SECONDS=90.0,
        _env_file=None,
    )
    db = BotDatabase(tmp_path / "bot.db")
    router = GeminiRouter(settings, db)

    ok_resp = MagicMock()
    ok_resp.text = '{"selected": []}'
    round_counter = {"count": 0}

    def fake_generate_content(*, model: str, **kwargs: object) -> MagicMock:
        round_counter["count"] += 1
        # Fail first 2 full rounds (4 calls), succeed on 3rd round
        if round_counter["count"] <= 4:
            raise RuntimeError("503 UNAVAILABLE: high demand")
        return ok_resp

    router._client = MagicMock()
    router._client.models.generate_content = fake_generate_content

    sleep_mock = AsyncMock()
    with patch("ultra_news_bot.pipeline.llm.asyncio.sleep", new=sleep_mock):
        with patch("ultra_news_bot.pipeline.llm.random.uniform", return_value=0.0):
            data, used = await router._call_json("gemini-3.1-flash-lite", "sys", "usr")

    assert data == {"selected": []}
    assert used == "gemini-3.1-flash-lite"
    assert [call.args[0] for call in sleep_mock.await_args_list] == [10.0, 20.0]


@pytest.mark.asyncio
async def test_gemini_router_raises_rate_limit_error_when_all_fail(tmp_path: Path) -> None:
    settings = Settings(
        GOOGLE_API_KEY="test-key",
        GEMINI_FILTER_MODEL="gemini-3.1-flash-lite",
        GEMINI_FALLBACK_MODELS="gemini-3.5-flash-lite",
        GEMINI_MAX_ATTEMPTS=3,
        _env_file=None,
    )
    db = BotDatabase(tmp_path / "bot.db")
    router = GeminiRouter(settings, db)

    def fake_generate_content(*, model: str, **kwargs: object) -> MagicMock:
        raise RuntimeError("429 RESOURCE_EXHAUSTED: daily limit reached")

    router._client = MagicMock()
    router._client.models.generate_content = fake_generate_content

    sleep_mock = AsyncMock()
    with patch("ultra_news_bot.pipeline.llm.asyncio.sleep", new=sleep_mock):
        with pytest.raises(GeminiAllModelsFailedError) as exc_info:
            await router._call_json("gemini-3.1-flash-lite", "sys", "usr")

    assert exc_info.value.is_rate_limit is True
    assert is_rate_limit_error(exc_info.value) is True
    assert sleep_mock.await_count == 2


@pytest.mark.asyncio
async def test_send_digest_skips_broken_cover_photo_and_sends_chunks() -> None:
    telegram = MagicMock()
    telegram.send_photo = AsyncMock(side_effect=RuntimeError("bad image"))
    telegram.send_message = AsyncMock()

    await send_digest_to_chat(
        telegram,
        "@ultranewschannel",
        ["<b>Part 1</b>", "<b>Part 2</b>"],
        cover_image_url="https://example.com/broken.jpg",
    )

    assert telegram.send_photo.await_count == 1
    assert telegram.send_message.await_count == 2


@pytest.mark.asyncio
async def test_run_once_sends_to_channel_and_alerts_admin_on_warning_and_failure(
    tmp_path: Path,
) -> None:
    settings = Settings(
        TELEGRAM_CHANNEL_ID="@ultranewschannel",
        ALLOWED_USER_IDS="6330621657",
        _env_file=None,
    )
    db = BotDatabase(tmp_path / "bot.db")
    db.set_state("owner_chat_id", "6330621657")

    telegram = MagicMock()
    telegram.send_message = AsyncMock()
    telegram.send_photo = AsyncMock()

    payload = DigestPayload(
        created_at=datetime(2026, 11, 17, 9, 0, tzinfo=timezone.utc),
        total_scanned=10,
        candidates_sent=5,
        model_filter="gemini-3.1-flash-lite",
        model_summary="gemini-3.5-flash",
        rendered_messages=["<b>News</b>"],
        warnings=[
            "gemini-3.1-flash-lite: упор в лимит (429), использован fallback -> gemini-3.5-flash"
        ],
    )

    with patch("ultra_news_bot.bot.app.run_digest_pipeline", new=AsyncMock(return_value=payload)):
        ok = await run_once(telegram, settings, db)

    assert ok is True
    assert telegram.send_message.await_count == 2
    first_kwargs = telegram.send_message.await_args_list[0].kwargs
    second_kwargs = telegram.send_message.await_args_list[1].kwargs
    assert first_kwargs["chat_id"] == "@ultranewschannel"
    assert second_kwargs["chat_id"] == 6330621657
    assert "GEMINI FALLBACK WARNING" in second_kwargs["text"]

    # Now simulate total failure on rate limit -> admin gets alert, channel gets nothing
    telegram.send_message.reset_mock()
    err = GeminiAllModelsFailedError(
        [("gemini-3.1-flash-lite", RuntimeError("429 RESOURCE_EXHAUSTED"))]
    )
    with patch("ultra_news_bot.bot.app.run_digest_pipeline", new=AsyncMock(side_effect=err)):
        ok_fail = await run_once(telegram, settings, db)

    assert ok_fail is False
    assert telegram.send_message.await_count == 1
    alert_kwargs = telegram.send_message.await_args_list[0].kwargs
    assert alert_kwargs["chat_id"] == 6330621657
    assert "GEMINI RATE LIMIT" in alert_kwargs["text"]
