from __future__ import annotations

from datetime import date
from pathlib import Path

from sectorscout.config import SectorScoutConfig
from sectorscout.intel.manual_inbox import parse_manual_file, parse_manual_markdown
from sectorscout.intel.storage import (
    ensure_intel_dirs,
    insert_raw_item,
    insert_trade_view,
    save_media_file,
    trade_view_exists,
)
from sectorscout.intel.text_extract import extract_trade_view, normalize_text
from sectorscout.intel.vision_extract import extract_image_observation


def capture_text(
    config: SectorScoutConfig,
    raw_text: str,
    *,
    source_id: str = "manual_capture",
    platform: str = "other",
    author: str | None = None,
    title: str | None = None,
    url: str | None = None,
    rights_scope: str = "manual_private",
    asof_date: date | None = None,
    metadata: dict | None = None,
) -> tuple[str, str | None]:
    normalized = normalize_text(raw_text)
    raw_item_id = insert_raw_item(
        config,
        source_id=source_id,
        source_type="manual_capture",
        title=title,
        author=author,
        platform=platform,
        url=url,
        asof_date=asof_date,
        raw_text=raw_text,
        normalized_text=normalized,
        rights_scope=rights_scope,
        collection_method="manual_capture",
        metadata=metadata or {},
    )
    view_id = None
    if not trade_view_exists(config, raw_item_id, "rule_text_v1"):
        draft = extract_trade_view(
            raw_text,
            source_id=source_id,
            source_type="manual_capture",
            source_title=title,
            author=author,
            platform=platform,
            url=url,
            asof_date=asof_date,
            rights_scope=rights_scope,
            requires_review=True,
        )
        view_id = insert_trade_view(config, raw_item_id=raw_item_id, draft=draft)
    return raw_item_id, view_id


def capture_markdown_file(
    config: SectorScoutConfig,
    path: str | Path,
    *,
    asof_date: date | None = None,
) -> tuple[str, str | None]:
    capture = parse_manual_file(path)
    return capture_text(
        config,
        capture.raw_text,
        source_id=capture.source_id,
        platform=capture.platform,
        author=capture.author,
        title=Path(path).stem,
        url=capture.url,
        rights_scope=capture.rights_scope,
        asof_date=asof_date,
        metadata=capture.metadata,
    )


def capture_markdown_text(
    config: SectorScoutConfig,
    markdown: str,
    *,
    asof_date: date | None = None,
) -> tuple[str, str | None]:
    capture = parse_manual_markdown(markdown)
    return capture_text(
        config,
        capture.raw_text,
        source_id=capture.source_id,
        platform=capture.platform,
        author=capture.author,
        url=capture.url,
        rights_scope=capture.rights_scope,
        asof_date=asof_date,
        metadata=capture.metadata,
    )


def capture_image_file(
    config: SectorScoutConfig,
    path: str | Path,
    *,
    source_id: str = "manual_image",
    rights_scope: str = "manual_private",
    raw_item_id: str | None = None,
) -> str:
    ensure_intel_dirs()
    media_id = save_media_file(
        config,
        path,
        raw_item_id=raw_item_id,
        source_id=source_id,
        rights_scope=rights_scope,
    )
    extract_image_observation(config, media_id)
    return media_id
