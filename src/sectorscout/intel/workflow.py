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


QUEUE_LABELS = {
    "due_follow_up": "Follow-up due",
    "image_review": "Review captured chart/image",
    "external_view_review": "Confirm external note",
    "overlap_conflict": "Possible disagreement",
    "overlap_needs_review": "Unclear overlap",
    "overlap_external_only": "External mention not in SectorScout",
    "overlap_internal_only": "SectorScout item needs outside context",
    "hindsight_pattern_review": "Review historical pattern observation",
}


HINDSIGHT_DONE_STATUSES = {
    "confirmed_hypothesis",
    "rejected_hypothesis",
    "not_applicable",
}


def friendly_bucket_label(bucket: str) -> str:
    return QUEUE_LABELS.get(bucket, bucket.replace("_", " ").title())


def friendly_queue_rows(queue: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in queue:
        rows.append(
            {
                "Review order": item.get("priority"),
                "What this means": friendly_bucket_label(str(item.get("bucket") or "")),
                "Symbol or item": item.get("symbol") or item.get("object_id") or "-",
                "Where to review": item.get("page") or "-",
                "Why it matters": item.get("reason") or "-",
                "Suggested next step": item.get("next_step") or "-",
            }
        )
    return rows


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


def _as_date(value: object) -> date | None:
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "date"):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _latest_review_marks(config: SectorScoutConfig) -> dict[tuple[str, str], dict[str, Any]]:
    rows = _safe_df(
        config,
        """
        SELECT object_type, object_id, review_status, follow_up_date, reviewed_at
        FROM intel_review_marks
        QUALIFY row_number() OVER (
            PARTITION BY object_type, object_id
            ORDER BY reviewed_at DESC
        ) = 1
        """,
    )
    marks: dict[tuple[str, str], dict[str, Any]] = {}
    for _, row in rows.iterrows():
        key = (str(row["object_type"]), str(row["object_id"]))
        marks[key] = row.to_dict()
    return marks


def _is_deferred_or_dismissed(
    latest_marks: dict[tuple[str, str], dict[str, Any]],
    *,
    object_type: str,
    object_id: str,
    asof_date: date | None,
) -> bool:
    mark = latest_marks.get((object_type, object_id))
    if not mark:
        return False
    if str(mark.get("review_status") or "") == "expired" and _as_date(mark.get("follow_up_date")) is None:
        return True
    follow_up = _as_date(mark.get("follow_up_date"))
    if follow_up is None:
        return False
    if asof_date is None:
        return True
    return follow_up > asof_date


def _external_view_items(
    config: SectorScoutConfig,
    latest_marks: dict[tuple[str, str], dict[str, Any]],
    asof_date: date | None,
) -> list[ResearchQueueItem]:
    asof_filter = "AND (asof_date IS NULL OR asof_date <= ?)" if asof_date is not None else ""
    params = [asof_date] if asof_date is not None else []
    rows = _safe_df(
        config,
        f"""
        SELECT intel_view_id, source_id, source_title, canonical_symbols_json,
               direction, timeframe, summary
        FROM intel_trade_views
        WHERE requires_review = true
          AND user_confirmed = false
          AND superseded_by_view_id IS NULL
          {asof_filter}
        ORDER BY created_at DESC
        """,
        params,
    )
    items: list[ResearchQueueItem] = []
    for _, row in rows.iterrows():
        object_id = str(row["intel_view_id"])
        if _is_deferred_or_dismissed(
            latest_marks,
            object_type="intel_view",
            object_id=object_id,
            asof_date=asof_date,
        ):
            continue
        symbols = _symbols_from_json(row.get("canonical_symbols_json"))
        items.append(
            ResearchQueueItem(
                priority=20,
                bucket="external_view_review",
                object_type="intel_view",
                object_id=object_id,
                symbol=symbols,
                source=str(row.get("source_id") or ""),
                page="External Intel",
                reason=(
                    "An external note was captured, but it has not been reviewed by you yet. "
                    f"Context: {row.get('direction')} / {row.get('timeframe')}."
                ),
                next_step="Open the excerpt, check the symbols, levels, and conditions, then confirm or write a note.",
            )
        )
    return items


def _image_review_items(
    config: SectorScoutConfig,
    latest_marks: dict[tuple[str, str], dict[str, Any]],
    asof_date: date | None,
) -> list[ResearchQueueItem]:
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
        object_id = str(row["observation_id"])
        if _is_deferred_or_dismissed(
            latest_marks,
            object_type="image_observation",
            object_id=object_id,
            asof_date=asof_date,
        ):
            continue
        status = str(row.get("extraction_status") or "needs_review")
        if status == "pending_vision_consent":
            next_step = "Keep the image local and annotate it manually, or explicitly allow vision processing."
        elif status == "pending_vision_provider":
            next_step = "Review the image manually because no vision provider is configured."
        else:
            next_step = "Check the extracted fields, edit anything unclear, then save the image-derived view."
        items.append(
            ResearchQueueItem(
                priority=10,
                bucket="image_review",
                object_type="image_observation",
                object_id=object_id,
                symbol=_symbols_from_json(row.get("symbols_json")),
                source=str(row.get("source_id") or ""),
                page="Vision Review",
                reason=(
                    "A chart or screenshot is waiting for human review. "
                    f"Status: {status}; provider: {row.get('extraction_provider')}."
                ),
                next_step=next_step,
            )
        )
    return items


def _overlap_items(
    config: SectorScoutConfig,
    latest_marks: dict[tuple[str, str], dict[str, Any]],
    asof_date: date | None,
) -> list[ResearchQueueItem]:
    items: list[ResearchQueueItem] = []
    for row in compute_overlap(config, asof_date=asof_date):
        object_id = str(row.get("symbol") or "")
        if _is_deferred_or_dismissed(
            latest_marks,
            object_type="overlap_row",
            object_id=object_id,
            asof_date=asof_date,
        ):
            continue
        label = str(row.get("overlap_label") or "")
        if label == "CONFLICT":
            priority = 5
            reason = (
                "SectorScout has internal context for this symbol, but external intel points to a different or riskier view."
            )
            next_step = "Compare the internal setup context with the external risk note, then write your review."
        elif label == "NEEDS_REVIEW":
            priority = 15
            reason = "This overlap is unclear because the extraction is ambiguous or image-derived context is not confirmed."
            next_step = "Review the source or image, then clarify the symbol, direction/context, and timeframe."
        elif label == "EXTERNAL_ONLY":
            priority = 40
            reason = (
                "An external source mentioned this symbol, but SectorScout does not currently rank or watch it for this data date."
            )
            next_step = "Decide whether to add it to your watchlist seed or leave it as outside context."
        elif label == "INTERNAL_ONLY":
            priority = 60
            reason = "SectorScout has this symbol internally, but no captured external source mentions it yet."
            next_step = "Optional: look for outside context if this internal item matters for today’s review."
        else:
            continue
        items.append(
            ResearchQueueItem(
                priority=priority,
                bucket=f"overlap_{label.lower()}",
                object_type="overlap_row",
                object_id=object_id,
                symbol=object_id,
                source=str(row.get("external_sources") or ""),
                page="Internal vs External Overlap",
                reason=reason,
                next_step=next_step,
            )
        )
    return items


def _hindsight_pattern_items(
    config: SectorScoutConfig,
    latest_marks: dict[tuple[str, str], dict[str, Any]],
    asof_date: date | None,
) -> list[ResearchQueueItem]:
    rows = _safe_df(
        config,
        """
        SELECT observation_id, symbol, observation_group, pattern_name,
               observation_value, status, evidence, source, requires_review
        FROM hindsight_pattern_observations
        WHERE requires_review = true
        QUALIFY dense_rank() OVER (ORDER BY generated_at_utc DESC, scan_id DESC) = 1
        ORDER BY symbol, observation_group, pattern_name
        """,
    )
    items: list[ResearchQueueItem] = []
    for _, row in rows.iterrows():
        object_id = str(row["observation_id"])
        mark = latest_marks.get(("hindsight_pattern_observation", object_id))
        if mark and str(mark.get("review_status") or "") in HINDSIGHT_DONE_STATUSES:
            continue
        if _is_deferred_or_dismissed(
            latest_marks,
            object_type="hindsight_pattern_observation",
            object_id=object_id,
            asof_date=asof_date,
        ):
            continue
        group = str(row.get("observation_group") or "")
        priority = {
            "manual_or_llm_required": 25,
            "industry": 30,
            "technical": 45,
        }.get(group, 50)
        items.append(
            ResearchQueueItem(
                priority=priority,
                bucket="hindsight_pattern_review",
                object_type="hindsight_pattern_observation",
                object_id=object_id,
                symbol=str(row.get("symbol") or ""),
                source=str(row.get("source") or ""),
                page="Historical Pattern Discovery",
                reason=(
                    f"Historical observation needs review: {row.get('pattern_name')} "
                    f"({row.get('status')}). Evidence: {row.get('evidence')}"
                ),
                next_step=(
                    "Confirm whether this is a useful research hypothesis, reject it, "
                    "or mark that more point-in-time evidence is needed."
                ),
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
        QUALIFY row_number() OVER (
            PARTITION BY object_type, object_id
            ORDER BY reviewed_at DESC
        ) = 1
        ORDER BY follow_up_date ASC, reviewed_at DESC
        """,
    )
    items: list[ResearchQueueItem] = []
    for _, row in rows.iterrows():
        follow_up = _as_date(row.get("follow_up_date"))
        if follow_up is None or follow_up > asof_date:
            continue
        items.append(
            ResearchQueueItem(
                priority=5,
                bucket="due_follow_up",
                object_type=str(row.get("object_type") or "review_mark"),
                object_id=str(row.get("object_id") or row.get("review_id")),
                symbol=str(row.get("object_id") or "") if row.get("object_type") == "ticker" else None,
                source=None,
                page="Notes / Review",
                reason=f"You scheduled a follow-up for {row.get('follow_up_date')} with status {row.get('review_status')}.",
                next_step="Update the review status, add what changed, or set the next follow-up date.",
            )
        )
    return items


def build_research_queue(config: SectorScoutConfig, *, asof_date: date | None = None) -> list[dict[str, Any]]:
    ensure_intel_tables(config)
    latest_marks = _latest_review_marks(config)
    items: list[ResearchQueueItem] = []
    items.extend(_due_follow_up_items(config, asof_date))
    items.extend(_image_review_items(config, latest_marks, asof_date))
    items.extend(_external_view_items(config, latest_marks, asof_date))
    items.extend(_overlap_items(config, latest_marks, asof_date))
    items.extend(_hindsight_pattern_items(config, latest_marks, asof_date))
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
