from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

import pandas as pd

from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database
from sectorscout.intel.overlap import compute_overlap
from sectorscout.intel.storage import ensure_intel_tables


@dataclass(frozen=True)
class ResearchQueueItem:
    priority: int
    bucket: str
    object_type: str
    object_id: str
    symbol: str | None
    source: str | None
    page: str
    reason: str
    next_step: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _table_exists(config: SectorScoutConfig, table: str) -> bool:
    with connect_database(config.database.path) as connection:
        row = connection.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'main' AND table_name = ?
            """,
            [table],
        ).fetchone()
    return row is not None


def _safe_df(config: SectorScoutConfig, sql: str, params: list | None = None) -> pd.DataFrame:
    if " FROM " in sql:
        table_name = sql.split(" FROM ", 1)[1].strip().split()[0]
        if table_name and not _table_exists(config, table_name):
            return pd.DataFrame()
    with connect_database(config.database.path) as connection:
        return connection.execute(sql, params or []).fetchdf()


def _symbols_from_json(value: object) -> str | None:
    try:
        import json

        symbols = json.loads(str(value or "[]"))
    except Exception:
        symbols = []
    if not symbols:
        return None
    return ", ".join(str(symbol).upper() for symbol in symbols[:6])


def _external_view_items(config: SectorScoutConfig) -> list[ResearchQueueItem]:
    rows = _safe_df(
        config,
        """
        SELECT intel_view_id, source_id, source_title, canonical_symbols_json,
               direction, timeframe, summary
        FROM intel_trade_views
        WHERE requires_review = true
          AND user_confirmed = false
          AND superseded_by_view_id IS NULL
        ORDER BY created_at DESC
        """,
    )
    items: list[ResearchQueueItem] = []
    for _, row in rows.iterrows():
        symbols = _symbols_from_json(row.get("canonical_symbols_json"))
        items.append(
            ResearchQueueItem(
                priority=20,
                bucket="external_view_review",
                object_type="intel_view",
                object_id=str(row["intel_view_id"]),
                symbol=symbols,
                source=str(row.get("source_id") or ""),
                page="External Intel",
                reason=f"External view needs user confirmation: {row.get('direction')} / {row.get('timeframe')}",
                next_step="Open the source excerpt, verify symbols/levels/conditions, then confirm or add a review note.",
            )
        )
    return items


def _image_review_items(config: SectorScoutConfig) -> list[ResearchQueueItem]:
    rows = _safe_df(
        config,
        """
        SELECT io.observation_id, io.source_id, io.symbols_json, io.timeframe,
               io.extraction_provider, mi.extraction_status
        FROM intel_image_observations io
        LEFT JOIN intel_media_items mi ON mi.media_id = io.media_id
        WHERE io.requires_review = true
        ORDER BY io.created_at DESC
        """,
    )
    items: list[ResearchQueueItem] = []
    for _, row in rows.iterrows():
        status = str(row.get("extraction_status") or "needs_review")
        if status == "pending_vision_consent":
            next_step = "Either keep the image local and annotate it manually, or explicitly allow provider processing."
        elif status == "pending_vision_provider":
            next_step = "Review the image manually because no vision provider is configured."
        else:
            next_step = "Check the extracted fields, edit anything unclear, then save a confirmed image-derived view."
        items.append(
            ResearchQueueItem(
                priority=10,
                bucket="image_review",
                object_type="image_observation",
                object_id=str(row["observation_id"]),
                symbol=_symbols_from_json(row.get("symbols_json")),
                source=str(row.get("source_id") or ""),
                page="Vision Review",
                reason=f"Image observation requires review; status={status}, provider={row.get('extraction_provider')}",
                next_step=next_step,
            )
        )
    return items


def _overlap_items(config: SectorScoutConfig) -> list[ResearchQueueItem]:
    items: list[ResearchQueueItem] = []
    for row in compute_overlap(config):
        label = str(row.get("overlap_label") or "")
        if label == "CONFLICT":
            priority = 5
            next_step = "Compare internal setup context against external risk context and write a manual review note."
        elif label == "NEEDS_REVIEW":
            priority = 15
            next_step = "Resolve ambiguous extraction or unconfirmed image context before relying on this overlay."
        elif label == "EXTERNAL_ONLY":
            priority = 40
            next_step = "Decide whether this belongs on the watchlist seed or should remain external-only context."
        elif label == "INTERNAL_ONLY":
            priority = 60
            next_step = "Optional: look for external context only if the internal candidate is important today."
        else:
            continue
        items.append(
            ResearchQueueItem(
                priority=priority,
                bucket=f"overlap_{label.lower()}",
                object_type="overlap_row",
                object_id=str(row.get("symbol") or ""),
                symbol=str(row.get("symbol") or ""),
                source=str(row.get("external_sources") or ""),
                page="Internal vs External Overlap",
                reason=f"{label}: internal={row.get('internal_status')} external={row.get('external_bias')}",
                next_step=next_step,
            )
        )
    return items


def _due_follow_up_items(config: SectorScoutConfig, asof_date: date | None) -> list[ResearchQueueItem]:
    if asof_date is None or not _table_exists(config, "intel_review_marks"):
        return []
    rows = _safe_df(
        config,
        """
        SELECT review_id, object_type, object_id, review_status, follow_up_date
        FROM intel_review_marks
        WHERE follow_up_date IS NOT NULL
          AND follow_up_date <= ?
        ORDER BY follow_up_date ASC, reviewed_at DESC
        """,
        [asof_date],
    )
    items: list[ResearchQueueItem] = []
    for _, row in rows.iterrows():
        items.append(
            ResearchQueueItem(
                priority=5,
                bucket="due_follow_up",
                object_type=str(row.get("object_type") or "review_mark"),
                object_id=str(row.get("object_id") or row.get("review_id")),
                symbol=str(row.get("object_id") or "") if row.get("object_type") == "ticker" else None,
                source=None,
                page="Notes / Review",
                reason=f"Follow-up due {row.get('follow_up_date')} for {row.get('review_status')}",
                next_step="Update the review status, add what changed, or set the next follow-up date.",
            )
        )
    return items


def build_research_queue(config: SectorScoutConfig, *, asof_date: date | None = None) -> list[dict[str, Any]]:
    ensure_intel_tables(config)
    items: list[ResearchQueueItem] = []
    items.extend(_due_follow_up_items(config, asof_date))
    items.extend(_image_review_items(config))
    items.extend(_external_view_items(config))
    items.extend(_overlap_items(config))
    rows = [item.to_dict() for item in items]
    return sorted(rows, key=lambda row: (row["priority"], row["bucket"], str(row.get("symbol") or "")))


def workflow_summary(config: SectorScoutConfig, *, asof_date: date | None = None) -> dict[str, int]:
    queue = build_research_queue(config, asof_date=asof_date)
    summary: dict[str, int] = {
        "total": len(queue),
        "urgent": sum(1 for item in queue if int(item["priority"]) <= 10),
        "needs_review": sum(1 for item in queue if "review" in str(item["bucket"])),
        "conflicts": sum(1 for item in queue if item["bucket"] == "overlap_conflict"),
        "follow_ups_due": sum(1 for item in queue if item["bucket"] == "due_follow_up"),
    }
    return summary
