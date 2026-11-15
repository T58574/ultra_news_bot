"""SQLite persistence for seen URLs, digest cache, LLM quota tracking, and user settings."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class BotDatabase:
    """Lightweight SQLite store for personal bot state."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS seen_urls (
                    url_key TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    seen_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS digests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    cover_image_url TEXT,
                    messages_json TEXT NOT NULL,
                    total_scanned INTEGER NOT NULL,
                    selected_count INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS llm_usage (
                    day TEXT NOT NULL,
                    model TEXT NOT NULL,
                    requests INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (day, model)
                );

                CREATE TABLE IF NOT EXISTS bot_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    def is_seen(self, url_key: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM seen_urls WHERE url_key = ?", (url_key,)
            ).fetchone()
            return row is not None

    def mark_seen(self, items: list[tuple[str, str]]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO seen_urls (url_key, title, seen_at) VALUES (?, ?, ?)",
                [(url_key, title, now) for url_key, title in items],
            )

    def count_seen(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM seen_urls").fetchone()
            return int(row["cnt"]) if row else 0

    def record_llm_call(self, model: str) -> None:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO llm_usage (day, model, requests)
                VALUES (?, ?, 1)
                ON CONFLICT(day, model) DO UPDATE SET requests = requests + 1
                """,
                (day, model),
            )

    def get_today_usage(self) -> dict[str, int]:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT model, requests FROM llm_usage WHERE day = ? ORDER BY requests DESC",
                (day,),
            ).fetchall()
            return {row["model"]: int(row["requests"]) for row in rows}

    def save_digest(
        self,
        created_at: datetime,
        cover_image_url: Optional[str],
        messages: list[str],
        total_scanned: int,
        selected_count: int,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO digests (created_at, cover_image_url, messages_json, total_scanned, selected_count)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    created_at.isoformat(),
                    cover_image_url,
                    json.dumps(messages, ensure_ascii=False),
                    total_scanned,
                    selected_count,
                ),
            )

    def get_latest_digest(self) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT created_at, cover_image_url, messages_json, total_scanned, selected_count
                FROM digests
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
            if not row:
                return None
            return {
                "created_at": row["created_at"],
                "cover_image_url": row["cover_image_url"],
                "messages": json.loads(row["messages_json"]),
                "total_scanned": row["total_scanned"],
                "selected_count": row["selected_count"],
            }

    def set_state(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO bot_state (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def get_state(self, key: str, default: str = "") -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM bot_state WHERE key = ?", (key,)
            ).fetchone()
            return str(row["value"]) if row else default
