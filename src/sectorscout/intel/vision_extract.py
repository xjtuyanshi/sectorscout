from __future__ import annotations

import base64
import json
import os
import urllib.request

from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database
from sectorscout.intel.models import TradeViewDraft
from sectorscout.intel.storage import (
    insert_image_observation,
    insert_trade_view,
    set_media_status,
    trade_view_exists_for_media,
)
from sectorscout.intel.symbol_normalize import normalize_symbols
from sectorscout.intel.text_extract import (
    extract_direction,
    extract_invalidation_condition,
    extract_no_trade_condition,
    extract_setup_types,
    extract_target_area,
    extract_trigger_condition,
    normalize_text,
)


VISION_PROMPT = (
    "Extract only visible chart or screenshot research context. Return compact JSON with "
    "symbols, timeframe, visible_levels, visible_annotations, extracted_text, inferred_context, "
    "and extraction_confidence. Do not invent price levels."
)


def _load_media(config: SectorScoutConfig, media_id: str) -> dict:
    with connect_database(config.database.path) as connection:
        row = connection.execute(
            """
            SELECT media_id, raw_item_id, source_id, source_url, local_path,
                   mime_type, rights_scope, metadata_json
            FROM intel_media_items
            WHERE media_id = ?
            """,
            [media_id],
        ).fetchone()
    if row is None:
        raise ValueError(f"Unknown media_id: {media_id}")
    return {
        "media_id": row[0],
        "raw_item_id": row[1],
        "source_id": row[2],
        "source_url": row[3],
        "local_path": row[4],
        "mime_type": row[5],
        "rights_scope": row[6],
        "metadata": json.loads(row[7] or "{}"),
    }


def _call_openai_vision(media: dict) -> dict:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("missing_vision_provider")
    image_bytes = open(media["local_path"], "rb").read()
    data_url = "data:%s;base64,%s" % (
        media["mime_type"],
        base64.b64encode(image_bytes).decode("ascii"),
    )
    body = {
        "model": os.environ.get("OPENAI_VISION_MODEL", "gpt-4.1-mini"),
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": VISION_PROMPT},
                    {"type": "input_image", "image_url": data_url, "detail": "high"},
                ],
            }
        ],
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    output_text = payload.get("output_text")
    if not output_text:
        parts: list[str] = []
        for item in payload.get("output", []):
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    parts.append(content.get("text", ""))
        output_text = "\n".join(parts)
    try:
        return json.loads(output_text)
    except Exception:
        return {
            "symbols": [],
            "timeframe": "unknown",
            "visible_levels": [],
            "visible_annotations": [],
            "extracted_text": output_text or "",
            "inferred_context": "Vision provider returned unstructured text.",
            "extraction_confidence": "low",
        }


def _list_of_strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _create_image_trade_view(config: SectorScoutConfig, media: dict, result: dict) -> None:
    if trade_view_exists_for_media(config, str(media["media_id"]), "vision_provider_v1"):
        return
    raw_symbols = _list_of_strings(result.get("symbols"))
    canonical_symbols = normalize_symbols(raw_symbols)
    visible_levels = _list_of_strings(result.get("visible_levels"))
    annotations = _list_of_strings(result.get("visible_annotations"))
    extracted_text = str(result.get("extracted_text") or "")
    inferred_context = str(result.get("inferred_context") or "")
    combined_text = normalize_text(" ".join([extracted_text, inferred_context, " ".join(annotations)]))
    summary_bits: list[str] = []
    if canonical_symbols:
        summary_bits.append("Image context mentions " + ", ".join(canonical_symbols[:8]))
    if visible_levels:
        summary_bits.append("Visible levels: " + ", ".join(visible_levels[:8]))
    if annotations:
        summary_bits.append("Visible annotations: " + ", ".join(annotations[:8]))
    summary = ". ".join(summary_bits) or "Image-derived context captured for user review."
    draft = TradeViewDraft(
        source_id=str(media["source_id"]),
        source_type="image_capture",
        source_title="Image/chart capture",
        author=None,
        platform="manual_image",
        url=media.get("source_url"),
        asof_date=None,
        published_at=None,
        collected_at=None,
        captured_at=None,
        raw_symbols=raw_symbols,
        canonical_symbols=canonical_symbols,
        asset_class="unknown",
        timeframe=str(result.get("timeframe") or "unknown"),
        direction=extract_direction(combined_text) if combined_text else "unknown",
        setup_type=extract_setup_types(combined_text) if combined_text else (annotations or ["image_context"]),
        key_levels=visible_levels,
        trigger_condition=extract_trigger_condition(combined_text) if combined_text else None,
        invalidation_condition=extract_invalidation_condition(combined_text) if combined_text else None,
        target_area=extract_target_area(combined_text) if combined_text else None,
        no_trade_condition=extract_no_trade_condition(combined_text) if combined_text else None,
        risk_notes=None,
        summary=summary,
        source_excerpt=(combined_text or summary)[:700],
        extraction_method="vision_provider_v1",
        extraction_confidence=str(result.get("extraction_confidence") or "low"),
        rights_scope=str(media.get("rights_scope") or "manual_private"),
        requires_review=True,
        user_confirmed=False,
        media_id=str(media["media_id"]),
    )
    insert_trade_view(config, raw_item_id=media.get("raw_item_id"), draft=draft)


def extract_image_observation(config: SectorScoutConfig, media_id: str) -> str:
    media = _load_media(config, media_id)
    if not os.environ.get("OPENAI_API_KEY"):
        observation_id = insert_image_observation(
            config,
            media_id=media_id,
            raw_item_id=media["raw_item_id"],
            source_id=media["source_id"],
            extracted_text=None,
            symbols=[],
            timeframe="unknown",
            visible_levels=[],
            visible_annotations=[],
            inferred_context="Vision provider is not configured.",
            extraction_provider="none",
            extraction_confidence="low",
            requires_review=True,
        )
        set_media_status(config, media_id, "pending_vision_provider")
        return observation_id

    if media["rights_scope"] != "public" and not media["metadata"].get("vision_provider_consent"):
        observation_id = insert_image_observation(
            config,
            media_id=media_id,
            raw_item_id=media["raw_item_id"],
            source_id=media["source_id"],
            extracted_text=None,
            symbols=[],
            timeframe="unknown",
            visible_levels=[],
            visible_annotations=[],
            inferred_context="Vision provider processing requires explicit user consent for non-public captures.",
            extraction_provider="none",
            extraction_confidence="low",
            requires_review=True,
        )
        set_media_status(config, media_id, "pending_vision_consent")
        return observation_id

    try:
        result = _call_openai_vision(media)
        symbols = normalize_symbols(_list_of_strings(result.get("symbols")))
        observation_id = insert_image_observation(
            config,
            media_id=media_id,
            raw_item_id=media["raw_item_id"],
            source_id=media["source_id"],
            extracted_text=result.get("extracted_text"),
            symbols=symbols,
            timeframe=str(result.get("timeframe") or "unknown"),
            visible_levels=[str(level) for level in result.get("visible_levels", [])],
            visible_annotations=[str(item) for item in result.get("visible_annotations", [])],
            inferred_context=result.get("inferred_context"),
            extraction_provider="openai_responses",
            extraction_confidence=str(result.get("extraction_confidence") or "low"),
            requires_review=True,
        )
        _create_image_trade_view(config, media, result)
        set_media_status(config, media_id, "draft_observation_created")
        return observation_id
    except Exception as exc:
        observation_id = insert_image_observation(
            config,
            media_id=media_id,
            raw_item_id=media["raw_item_id"],
            source_id=media["source_id"],
            extracted_text=None,
            symbols=[],
            timeframe="unknown",
            visible_levels=[],
            visible_annotations=[],
            inferred_context=f"Vision extraction error: {exc}",
            extraction_provider="openai_responses",
            extraction_confidence="low",
            requires_review=True,
        )
        set_media_status(config, media_id, "vision_error")
        return observation_id
