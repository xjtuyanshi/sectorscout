from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from sectorscout.intel.models import TradeViewDraft
from sectorscout.intel.storage import (
    insert_trade_view,
    mark_image_observation_reviewed,
    mark_media_trade_views_superseded,
    set_trade_view_confirmed,
)
from sectorscout.intel.symbol_normalize import normalize_symbols
from sectorscout.ui.data import UIContext, parse_json_list, table_df


def render(ctx: UIContext) -> None:
    st.title("Vision Review")
    observations = table_df(ctx.config, "intel_image_observations", limit=1000)
    media = table_df(ctx.config, "intel_media_items", limit=1000)
    if observations.empty:
        st.info("No image observations yet. Upload a screenshot or chart image in Capture Inbox.")
        return
    pending = observations[observations["requires_review"] == True]  # noqa: E712
    st.metric("Images needing review", len(pending))
    for _, obs in pending.sort_values("created_at", ascending=False).iterrows():
        media_row = media[media["media_id"] == obs["media_id"]] if not media.empty else pd.DataFrame()
        with st.container(border=True):
            st.subheader(f"Observation {obs['observation_id']}")
            if not media_row.empty:
                path = Path(str(media_row.iloc[0]["local_path"]))
                if path.exists():
                    st.image(str(path), use_container_width=True)
            st.write(obs.get("inferred_context"))
            symbols_default = ", ".join(parse_json_list(obs.get("symbols_json")))
            levels_default = ", ".join(parse_json_list(obs.get("visible_levels_json")))
            annotations_default = ", ".join(parse_json_list(obs.get("visible_annotations_json")))
            with st.form(f"vision_form_{obs['observation_id']}"):
                symbols = st.text_input("Symbols", value=symbols_default)
                timeframe = st.text_input("Timeframe", value=str(obs.get("timeframe") or "unknown"))
                direction = st.selectbox("Direction/context", ["unknown", "bullish", "bearish", "neutral", "conditional", "mixed"])
                levels = st.text_input("Key levels", value=levels_default)
                trigger = st.text_input("Trigger condition")
                invalidation = st.text_input("Invalidation")
                target = st.text_input("Target area")
                no_trade = st.text_input("No-trade condition")
                summary = st.text_area("Summary", value=obs.get("extracted_text") or obs.get("inferred_context") or "")
                submitted = st.form_submit_button("Save confirmed image-derived view")
            if submitted:
                raw_symbols = [item.strip().upper() for item in symbols.split(",") if item.strip()]
                key_levels = [item.strip() for item in levels.split(",") if item.strip()]
                canonical_symbols = normalize_symbols(raw_symbols)
                draft = TradeViewDraft(
                    source_id=str(obs["source_id"]),
                    source_type="image_capture",
                    source_title="Image capture review",
                    author=None,
                    platform="manual_image",
                    url=None,
                    asof_date=ctx.asof_date.isoformat() if ctx.asof_date else None,
                    published_at=None,
                    collected_at=None,
                    captured_at=None,
                    raw_symbols=raw_symbols,
                    canonical_symbols=canonical_symbols,
                    asset_class="unknown",
                    timeframe=timeframe,
                    direction=direction,
                    setup_type=[item.strip() for item in annotations_default.split(",") if item.strip()] or ["image_context"],
                    key_levels=key_levels,
                    trigger_condition=trigger or None,
                    invalidation_condition=invalidation or None,
                    target_area=target or None,
                    no_trade_condition=no_trade or None,
                    risk_notes=None,
                    summary=summary or "Image-derived context confirmed by user.",
                    source_excerpt=summary[:700] if summary else "Image-derived context.",
                    extraction_method="vision_review_manual",
                    extraction_confidence=str(obs.get("extraction_confidence") or "low"),
                    rights_scope=str(media_row.iloc[0]["rights_scope"]) if not media_row.empty else "manual_private",
                    requires_review=False,
                    user_confirmed=True,
                    media_id=str(obs["media_id"]),
                )
                view_id = insert_trade_view(ctx.config, raw_item_id=obs.get("raw_item_id"), draft=draft)
                set_trade_view_confirmed(ctx.config, view_id, True)
                mark_media_trade_views_superseded(ctx.config, str(obs["media_id"]), view_id)
                mark_image_observation_reviewed(ctx.config, str(obs["observation_id"]))
                st.success(f"Saved confirmed view {view_id}.")
                st.rerun()
