from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


DIRECTION_VALUES = {"bullish", "bearish", "neutral", "conditional", "mixed", "unknown"}
TIMEFRAME_VALUES = {
    "intraday",
    "15m",
    "1h",
    "2h",
    "4h",
    "daily",
    "weekly",
    "monthly",
    "multi_timeframe",
    "unknown",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ManualCapture:
    source_id: str
    author: str | None
    platform: str
    guild_name: str | None
    channel: str | None
    published_at: str | None
    captured_at: str | None
    url: str | None
    tags: list[str]
    rights_scope: str
    raw_text: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TradeViewDraft:
    source_id: str
    source_type: str
    source_title: str | None
    author: str | None
    platform: str | None
    url: str | None
    asof_date: str | None
    published_at: str | None
    collected_at: str | None
    captured_at: str | None
    raw_symbols: list[str]
    canonical_symbols: list[str]
    asset_class: str
    timeframe: str
    direction: str
    setup_type: list[str]
    key_levels: list[str]
    trigger_condition: str | None
    invalidation_condition: str | None
    target_area: str | None
    no_trade_condition: str | None
    risk_notes: str | None
    summary: str
    source_excerpt: str
    extraction_method: str
    extraction_confidence: str
    rights_scope: str
    requires_review: bool = False
    user_confirmed: bool = False
    media_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MediaItemInput:
    raw_item_id: str | None
    source_id: str
    source_url: str | None
    local_path: Path
    mime_type: str
    width: int | None
    height: int | None
    rights_scope: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PublicSource:
    source_id: str
    name: str
    url: str
    enabled: bool = True
    source_type: str = "public_web"
    tags: list[str] = field(default_factory=list)
    rights_scope: str = "public"
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
