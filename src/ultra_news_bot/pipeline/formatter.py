"""Netrunner-style Telegram HTML formatter with safe message splitting."""

import html
from collections import defaultdict
from datetime import datetime
from typing import Any

from ..models import DigestEntry

MAX_MESSAGE_LEN = 3900


def render_digest_messages(
    entries: list[DigestEntry],
    categories_cfg: dict[str, Any],
    created_at: datetime,
    total_scanned: int,
    candidates_sent: int,
    model_filter: str,
    model_summary: str,
) -> list[str]:
    """Render digest entries into Telegram-safe HTML messages."""
    ts_str = created_at.strftime("%d.%m // %H:%M")
    header = (
        f"⚡ <b>[ NETRUNNER // FEED :: {html.escape(ts_str)} ]</b>\n"
        f"<code>&gt; SCAN: {total_scanned} | POOL: {candidates_sent} | "
        f"TOP: {len(entries)}</code>\n"
        f"<code>&gt; CORE: {html.escape(model_filter)} -&gt; {html.escape(model_summary)}</code>\n"
    )

    if not entries:
        return [
            header
            + "\n<code>[!] Новых значимых сигналов за период не обнаружено.</code>"
        ]

    grouped: dict[str, list[DigestEntry]] = defaultdict(list)
    for entry in entries:
        grouped[entry.category].append(entry)

    blocks: list[str] = [header]
    for cat_id, cat_entries in grouped.items():
        cat_meta = categories_cfg.get(cat_id, {})
        emoji = cat_meta.get("emoji", "◈")
        label = cat_meta.get("label", cat_id.upper())
        blocks.append(f"\n<b>┌─[ {emoji} {html.escape(label)} ]──────────</b>")

        for entry in cat_entries:
            headline = html.escape(entry.headline)
            summary = html.escape(entry.summary)
            src_label = html.escape(entry.source_label)
            url = html.escape(entry.url, quote=True)

            links = [f'<a href="{url}">источник</a>']
            if entry.discussion_url and entry.discussion_url != entry.url:
                disc_url = html.escape(entry.discussion_url, quote=True)
                links.append(f'<a href="{disc_url}">тред</a>')

            card = (
                f"▸ <b>{headline}</b>\n"
                f"<blockquote>{summary}</blockquote>\n"
                f"└─ <code>{{ {src_label} }}</code> • {' • '.join(links)}"
            )
            blocks.append(card)

    messages: list[str] = []
    current = ""
    for block in blocks:
        candidate = f"{current}\n{block}" if current else block
        if len(candidate) <= MAX_MESSAGE_LEN:
            current = candidate
        else:
            if current:
                messages.append(current)
            current = block[:MAX_MESSAGE_LEN]
    if current:
        messages.append(current)
    return messages
