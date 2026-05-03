from __future__ import annotations

import hashlib
import json
import mimetypes
import shutil
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database
from sectorscout.intel.models import MediaItemInput, TradeViewDraft


INTEL_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS intel_raw_items (
    raw_item_id VARCHAR PRIMARY KEY,
    source_id VARCHAR NOT NULL,
    source_type VARCHAR NOT NULL,
    title VARCHAR,
    author VARCHAR,
    platform VARCHAR,
    url VARCHAR,
    published_at TIMESTAMPTZ,
    collected_at TIMESTAMPTZ NOT NULL,
    captured_at TIMESTAMPTZ,
    asof_date DATE,
    raw_text VARCHAR NOT NULL,
    normalized_text VARCHAR NOT NULL,
    content_hash VARCHAR NOT NULL,
    rights_scope VARCHAR NOT NULL,
    collection_method VARCHAR NOT NULL,
    metadata_json VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS intel_media_items (
    media_id VARCHAR PRIMARY KEY,
    raw_item_id VARCHAR,
    source_id VARCHAR NOT NULL,
    source_url VARCHAR,
    local_path VARCHAR NOT NULL,
    mime_type VARCHAR NOT NULL,
    width INTEGER,
    height INTEGER,
    content_hash VARCHAR NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    rights_scope VARCHAR NOT NULL,
    extraction_status VARCHAR NOT NULL,
    metadata_json VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS intel_image_observations (
    observation_id VARCHAR PRIMARY KEY,
    media_id VARCHAR NOT NULL,
    raw_item_id VARCHAR,
    source_id VARCHAR NOT NULL,
    extracted_text VARCHAR,
    symbols_json VARCHAR NOT NULL,
    timeframe VARCHAR NOT NULL,
    visible_levels_json VARCHAR NOT NULL,
    visible_annotations_json VARCHAR NOT NULL,
    inferred_context VARCHAR,
    extraction_provider VARCHAR NOT NULL,
    extraction_confidence VARCHAR NOT NULL,
    requires_review BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS intel_trade_views (
    intel_view_id VARCHAR PRIMARY KEY,
    raw_item_id VARCHAR,
    media_id VARCHAR,
    source_id VARCHAR NOT NULL,
    source_type VARCHAR NOT NULL,
    source_title VARCHAR,
    author VARCHAR,
    platform VARCHAR,
    url VARCHAR,
    asof_date DATE,
    published_at TIMESTAMPTZ,
    collected_at TIMESTAMPTZ,
    captured_at TIMESTAMPTZ,
    raw_symbols_json VARCHAR NOT NULL,
    canonical_symbols_json VARCHAR NOT NULL,
    asset_class VARCHAR NOT NULL,
    timeframe VARCHAR NOT NULL,
    direction VARCHAR NOT NULL,
    setup_type_json VARCHAR NOT NULL,
    key_levels_json VARCHAR NOT NULL,
    trigger_condition VARCHAR,
    invalidation_condition VARCHAR,
    target_area VARCHAR,
    no_trade_condition VARCHAR,
    risk_notes VARCHAR,
    summary VARCHAR NOT NULL,
    source_excerpt VARCHAR NOT NULL,
    extraction_method VARCHAR NOT NULL,
    extraction_confidence VARCHAR NOT NULL,
    rights_scope VARCHAR NOT NULL,
    requires_review BOOLEAN NOT NULL,
    user_confirmed BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS intel_review_marks (
    review_id VARCHAR PRIMARY KEY,
    object_type VARCHAR NOT NULL,
    object_id VARCHAR NOT NULL,
    review_status VARCHAR NOT NULL,
    triggered_at TIMESTAMPTZ,
    reviewed_at TIMESTAMPTZ NOT NULL,
    notes VARCHAR,
    personal_plan VARCHAR,
    follow_up_date DATE
);

CREATE TABLE IF NOT EXISTS intel_notes (
    note_id VARCHAR PRIMARY KEY,
    object_type VARCHAR,
    object_id VARCHAR,
    symbol VARCHAR,
    asof_date DATE,
    note_text VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
"""


def ensure_intel_dirs(root: str | Path = "data/intel") -> None:
    base = Path(root)
    for name in ["fixtures", "manual", "captures", "media", "reports"]:
        (base / name).mkdir(parents=True, exist_ok=True)


def ensure_intel_tables(config: SectorScoutConfig) -> None:
    with connect_database(config.database.path) as connection:
        connection.execute(INTEL_SCHEMA_SQL)


def stable_hash_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def stable_hash_text(text: str) -> str:
    return stable_hash_bytes(text.encode("utf-8"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True)


def _parse_date(value: str | date | None) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def insert_raw_item(
    config: SectorScoutConfig,
    *,
    source_id: str,
    source_type: str,
    raw_text: str,
    normalized_text: str,
    title: str | None = None,
    author: str | None = None,
    platform: str | None = None,
    url: str | None = None,
    published_at: str | None = None,
    captured_at: str | None = None,
    asof_date: str | date | None = None,
    rights_scope: str = "manual_private",
    collection_method: str = "manual_capture",
    metadata: dict | None = None,
) -> str:
    ensure_intel_tables(config)
    content_hash = stable_hash_text(normalized_text or raw_text)
    with connect_database(config.database.path) as connection:
        existing = None
        if url:
            existing = connection.execute(
                "SELECT raw_item_id FROM intel_raw_items WHERE source_id = ? AND url = ? LIMIT 1",
                [source_id, url],
            ).fetchone()
        if existing is None:
            existing = connection.execute(
                """
                SELECT raw_item_id
                FROM intel_raw_items
                WHERE source_id = ? AND content_hash = ?
                LIMIT 1
                """,
                [source_id, content_hash],
            ).fetchone()
        if existing:
            return str(existing[0])
        raw_item_id = str(uuid4())
        connection.execute(
            """
            INSERT INTO intel_raw_items (
                raw_item_id, source_id, source_type, title, author, platform,
                url, published_at, collected_at, captured_at, asof_date,
                raw_text, normalized_text, content_hash, rights_scope,
                collection_method, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                raw_item_id,
                source_id,
                source_type,
                title,
                author,
                platform,
                url,
                published_at,
                _now(),
                captured_at,
                _parse_date(asof_date),
                raw_text,
                normalized_text,
                content_hash,
                rights_scope,
                collection_method,
                _json(metadata or {}),
            ],
        )
    return raw_item_id


def insert_trade_view(
    config: SectorScoutConfig,
    *,
    raw_item_id: str | None,
    draft: TradeViewDraft,
) -> str:
    ensure_intel_tables(config)
    intel_view_id = str(uuid4())
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            INSERT INTO intel_trade_views (
                intel_view_id, raw_item_id, media_id, source_id, source_type,
                source_title, author, platform, url, asof_date, published_at,
                collected_at, captured_at, raw_symbols_json,
                canonical_symbols_json, asset_class, timeframe, direction,
                setup_type_json, key_levels_json, trigger_condition,
                invalidation_condition, target_area, no_trade_condition,
                risk_notes, summary, source_excerpt, extraction_method,
                extraction_confidence, rights_scope, requires_review,
                user_confirmed, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                intel_view_id,
                raw_item_id,
                draft.media_id,
                draft.source_id,
                draft.source_type,
                draft.source_title,
                draft.author,
                draft.platform,
                draft.url,
                _parse_date(draft.asof_date),
                draft.published_at,
                draft.collected_at,
                draft.captured_at,
                _json(draft.raw_symbols),
                _json(draft.canonical_symbols),
                draft.asset_class,
                draft.timeframe,
                draft.direction,
                _json(draft.setup_type),
                _json(draft.key_levels),
                draft.trigger_condition,
                draft.invalidation_condition,
                draft.target_area,
                draft.no_trade_condition,
                draft.risk_notes,
                draft.summary,
                draft.source_excerpt,
                draft.extraction_method,
                draft.extraction_confidence,
                draft.rights_scope,
                draft.requires_review,
                draft.user_confirmed,
                _now(),
            ],
        )
    return intel_view_id


def trade_view_exists(config: SectorScoutConfig, raw_item_id: str, extraction_method: str) -> bool:
    ensure_intel_tables(config)
    with connect_database(config.database.path) as connection:
        row = connection.execute(
            """
            SELECT intel_view_id
            FROM intel_trade_views
            WHERE raw_item_id = ? AND extraction_method = ?
            LIMIT 1
            """,
            [raw_item_id, extraction_method],
        ).fetchone()
    return row is not None


def trade_view_exists_for_media(config: SectorScoutConfig, media_id: str, extraction_method: str) -> bool:
    ensure_intel_tables(config)
    with connect_database(config.database.path) as connection:
        row = connection.execute(
            """
            SELECT intel_view_id
            FROM intel_trade_views
            WHERE media_id = ? AND extraction_method = ?
            LIMIT 1
            """,
            [media_id, extraction_method],
        ).fetchone()
    return row is not None


def insert_image_observation(
    config: SectorScoutConfig,
    *,
    media_id: str,
    raw_item_id: str | None,
    source_id: str,
    extracted_text: str | None,
    symbols: list[str],
    timeframe: str,
    visible_levels: list[str],
    visible_annotations: list[str],
    inferred_context: str | None,
    extraction_provider: str,
    extraction_confidence: str,
    requires_review: bool = True,
) -> str:
    ensure_intel_tables(config)
    observation_id = str(uuid4())
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            INSERT INTO intel_image_observations (
                observation_id, media_id, raw_item_id, source_id, extracted_text,
                symbols_json, timeframe, visible_levels_json,
                visible_annotations_json, inferred_context, extraction_provider,
                extraction_confidence, requires_review, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                observation_id,
                media_id,
                raw_item_id,
                source_id,
                extracted_text,
                _json(symbols),
                timeframe,
                _json(visible_levels),
                _json(visible_annotations),
                inferred_context,
                extraction_provider,
                extraction_confidence,
                requires_review,
                _now(),
            ],
        )
    return observation_id


def _image_size(path: Path) -> tuple[int | None, int | None]:
    try:
        from PIL import Image

        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except Exception:
        return None, None


def save_media_file(
    config: SectorScoutConfig,
    source_path: str | Path,
    *,
    raw_item_id: str | None,
    source_id: str,
    source_url: str | None = None,
    rights_scope: str = "manual_private",
    media_root: str | Path = "data/intel/media",
    metadata: dict | None = None,
) -> str:
    ensure_intel_dirs()
    ensure_intel_tables(config)
    source = Path(source_path)
    payload = source.read_bytes()
    content_hash = stable_hash_bytes(payload)
    suffix = source.suffix.lower() or ".bin"
    dest = Path(media_root) / f"{content_hash}{suffix}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        shutil.copyfile(source, dest)
    mime_type = mimetypes.guess_type(dest.name)[0] or "application/octet-stream"
    width, height = _image_size(dest)
    with connect_database(config.database.path) as connection:
        existing = connection.execute(
            "SELECT media_id FROM intel_media_items WHERE content_hash = ? LIMIT 1",
            [content_hash],
        ).fetchone()
        if existing:
            return str(existing[0])
        media_id = str(uuid4())
        connection.execute(
            """
            INSERT INTO intel_media_items (
                media_id, raw_item_id, source_id, source_url, local_path,
                mime_type, width, height, content_hash, captured_at,
                rights_scope, extraction_status, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                media_id,
                raw_item_id,
                source_id,
                source_url,
                str(dest),
                mime_type,
                width,
                height,
                content_hash,
                _now(),
                rights_scope,
                "pending_vision_provider",
                _json(metadata or {}),
            ],
        )
    return media_id


def save_media_bytes(
    config: SectorScoutConfig,
    payload: bytes,
    *,
    filename: str,
    raw_item_id: str | None,
    source_id: str,
    source_url: str | None = None,
    rights_scope: str = "manual_private",
    media_root: str | Path = "data/intel/media",
    metadata: dict | None = None,
) -> str:
    ensure_intel_dirs()
    temp_root = Path(media_root).parent / "captures"
    temp_root.mkdir(parents=True, exist_ok=True)
    temp_path = temp_root / filename
    temp_path.write_bytes(payload)
    return save_media_file(
        config,
        temp_path,
        raw_item_id=raw_item_id,
        source_id=source_id,
        source_url=source_url,
        rights_scope=rights_scope,
        media_root=media_root,
        metadata=metadata,
    )


def set_media_status(config: SectorScoutConfig, media_id: str, status: str) -> None:
    ensure_intel_tables(config)
    with connect_database(config.database.path) as connection:
        connection.execute(
            "UPDATE intel_media_items SET extraction_status = ? WHERE media_id = ?",
            [status, media_id],
        )


def insert_review_mark(
    config: SectorScoutConfig,
    *,
    object_type: str,
    object_id: str,
    review_status: str,
    notes: str | None = None,
    personal_plan: str | None = None,
    follow_up_date: str | date | None = None,
    triggered_at: str | None = None,
) -> str:
    ensure_intel_tables(config)
    review_id = str(uuid4())
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            INSERT INTO intel_review_marks (
                review_id, object_type, object_id, review_status, triggered_at,
                reviewed_at, notes, personal_plan, follow_up_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                review_id,
                object_type,
                object_id,
                review_status,
                triggered_at,
                _now(),
                notes,
                personal_plan,
                _parse_date(follow_up_date),
            ],
        )
    return review_id


def insert_note(
    config: SectorScoutConfig,
    *,
    note_text: str,
    object_type: str | None = None,
    object_id: str | None = None,
    symbol: str | None = None,
    asof_date: str | date | None = None,
) -> str:
    ensure_intel_tables(config)
    note_id = str(uuid4())
    now = _now()
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            INSERT INTO intel_notes (
                note_id, object_type, object_id, symbol, asof_date,
                note_text, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                note_id,
                object_type,
                object_id,
                symbol.upper() if symbol else None,
                _parse_date(asof_date),
                note_text,
                now,
                now,
            ],
        )
    return note_id


def set_trade_view_confirmed(config: SectorScoutConfig, intel_view_id: str, confirmed: bool) -> None:
    ensure_intel_tables(config)
    with connect_database(config.database.path) as connection:
        connection.execute(
            "UPDATE intel_trade_views SET user_confirmed = ?, requires_review = ? WHERE intel_view_id = ?",
            [confirmed, not confirmed, intel_view_id],
        )


def mark_image_observation_reviewed(config: SectorScoutConfig, observation_id: str) -> None:
    ensure_intel_tables(config)
    with connect_database(config.database.path) as connection:
        row = connection.execute(
            "SELECT media_id FROM intel_image_observations WHERE observation_id = ?",
            [observation_id],
        ).fetchone()
        connection.execute(
            "UPDATE intel_image_observations SET requires_review = false WHERE observation_id = ?",
            [observation_id],
        )
        if row is not None:
            connection.execute(
                "UPDATE intel_media_items SET extraction_status = 'user_confirmed' WHERE media_id = ?",
                [row[0]],
            )
