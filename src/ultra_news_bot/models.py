"""Domain models for news items, sources, and digest output."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


SourceKind = Literal["hn", "reddit", "github", "rss"]


class NewsItem(BaseModel):
    """Normalized news candidate collected from any scraper."""

    uid: str
    title: str
    url: str
    source_kind: SourceKind
    source_name: str
    category: str = "code"
    score: int = 0
    comments_count: int = 0
    published_at: datetime
    snippet: str = ""
    full_text: str = ""
    image_url: Optional[str] = None
    discussion_url: Optional[str] = None
    rank_score: float = 0.0


class SelectedItem(BaseModel):
    """LLM-selected item from Step 1 batch filtering."""

    uid: str
    category: str
    reason: str = ""


class DigestEntry(BaseModel):
    """Final summarized item rendered into the netrunner digest."""

    uid: str
    category: str
    headline: str
    summary: str
    source_label: str
    url: str
    discussion_url: Optional[str] = None
    image_url: Optional[str] = None


class DigestPayload(BaseModel):
    """Complete generated digest ready for Telegram delivery."""

    created_at: datetime
    total_scanned: int
    candidates_sent: int
    model_filter: str
    model_summary: str
    cover_image_url: Optional[str] = None
    entries: list[DigestEntry] = Field(default_factory=list)
    rendered_messages: list[str] = Field(default_factory=list)
