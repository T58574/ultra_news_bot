"""Reply and Inline keyboards for the netrunner Telegram interface."""

from typing import Any

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

BTN_DIGEST = "📡 ДАЙДЖЕСТ"
BTN_SOURCES = "🎛 ИСТОЧНИКИ"
BTN_TOPICS = "🧩 ТЕМЫ"
BTN_SCHEDULE = "⏱ РАСПИСАНИЕ"
BTN_STATUS = "📊 СТАТУС"


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_DIGEST), KeyboardButton(text=BTN_SOURCES)],
            [KeyboardButton(text=BTN_TOPICS), KeyboardButton(text=BTN_SCHEDULE)],
            [KeyboardButton(text=BTN_STATUS)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выбери модуль терминала...",
    )


def digest_actions_keyboard(has_cache: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="⚡ Собрать свежий (2 LLM-запроса)", callback_data="digest:run")]
    ]
    if has_cache:
        rows.append(
            [InlineKeyboardButton(text="📦 Показать последний (0 запросов)", callback_data="digest:cached")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def topics_keyboard(categories_cfg: dict[str, Any]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for cat_id, cfg in categories_cfg.items():
        enabled = cfg.get("enabled", True)
        state = "ON" if enabled else "OFF"
        mark = "🟢" if enabled else "⚫"
        emoji = cfg.get("emoji", "◈")
        label = cfg.get("label", cat_id)
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{mark} {emoji} {label} [{state}]",
                    callback_data=f"topic:toggle:{cat_id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sources_keyboard(sources_cfg: dict[str, Any]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for key, label in (
        ("hackernews", "Hacker News"),
        ("reddit", "Reddit"),
        ("github", "GitHub"),
    ):
        enabled = sources_cfg.get(key, {}).get("enabled", True)
        mark = "🟢" if enabled else "⚫"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{mark} {label} [{'ON' if enabled else 'OFF'}]",
                    callback_data=f"src:toggle:{key}",
                )
            ]
        )

    for idx, rss in enumerate(sources_cfg.get("rss", [])):
        enabled = rss.get("enabled", True)
        mark = "🟢" if enabled else "⚫"
        name = rss.get("name", rss.get("url", f"RSS #{idx}"))[:28]
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{mark} RSS: {name}",
                    callback_data=f"rss:toggle:{idx}",
                )
            ]
        )

    rows.append([InlineKeyboardButton(text="➕ Добавить RSS по ссылке", callback_data="rss:add")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def schedule_keyboard(enabled: bool, morning: str, evening: str) -> InlineKeyboardMarkup:
    state_label = "🟢 Авто-дайджест: ВКЛ" if enabled else "⚫ Авто-дайджест: ВЫКЛ"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=state_label, callback_data="sched:toggle")],
            [
                InlineKeyboardButton(
                    text=f"☀ Утро: {morning}  |  🌙 Вечер: {evening}",
                    callback_data="sched:noop",
                )
            ],
        ]
    )
