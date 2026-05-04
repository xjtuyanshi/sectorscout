from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from sectorscout.intel.storage import set_trade_view_confirmed
from sectorscout.ui.data import UIContext, parse_json_list, table_df


def _display_symbols(value: object) -> str:
    return ", ".join(str(item) for item in parse_json_list(value))


def render(ctx: UIContext) -> None:
    st.title("External Intel")
    st.caption("External context is an overlay only. It does not change SectorScout base scores.")
    views = table_df(ctx.config, "intel_trade_views", limit=1000)
    if views.empty:
        st.warning("No external views are stored yet. Use Capture Inbox or reload the Chandler seed.")
        return
    col1, col2, col3 = st.columns(3)
    source_filter = col1.multiselect("Source", sorted(views["source_id"].dropna().unique().tolist()))
    direction_filter = col2.multiselect("Direction/context", sorted(views["direction"].dropna().unique().tolist()))
    review_filter = col3.selectbox("Review status", ["all", "needs_review", "confirmed", "not_confirmed"])
    symbol_query = st.text_input("Symbol filter", placeholder="NQ, QQQ, NVDA...")
    if source_filter:
        views = views[views["source_id"].isin(source_filter)]
    if direction_filter:
        views = views[views["direction"].isin(direction_filter)]
    if review_filter == "needs_review":
        views = views[views["requires_review"] == True]  # noqa: E712
    elif review_filter == "confirmed":
        views = views[views["user_confirmed"] == True]  # noqa: E712
    elif review_filter == "not_confirmed":
        views = views[views["user_confirmed"] == False]  # noqa: E712
    if symbol_query.strip():
        wanted = symbol_query.strip().upper()
        views = views[
            views["canonical_symbols_json"].apply(
                lambda value: wanted in [str(item).upper() for item in parse_json_list(value)]
            )
        ]
    for _, row in views.sort_values("created_at", ascending=False).iterrows():
        title = row.get("source_title") or row.get("source_id")
        with st.container(border=True):
            st.subheader(str(title))
            st.caption(
                f"{row.get('platform') or 'unknown'} | {row.get('author') or 'unknown author'} | "
                f"rights={row.get('rights_scope')} | clarity={row.get('extraction_confidence')}"
            )
            st.code(str(row.get("intel_view_id")), language=None)
            st.write(row.get("summary"))
            cols = st.columns(3)
            cols[0].write(f"Tickers: {_display_symbols(row.get('canonical_symbols_json')) or 'none'}")
            cols[1].write(f"Timeframe: {row.get('timeframe')}")
            cols[2].write(f"Direction/context: {row.get('direction')}")
            st.write(f"Key levels: {_display_symbols(row.get('key_levels_json')) or 'none'}")
            if row.get("trigger_condition"):
                st.write(f"Trigger condition: {row.get('trigger_condition')}")
            if row.get("invalidation_condition"):
                st.write(f"Invalidation: {row.get('invalidation_condition')}")
            if row.get("target_area"):
                st.write(f"Target area: {row.get('target_area')}")
            if row.get("no_trade_condition"):
                st.write(f"No-trade condition: {row.get('no_trade_condition')}")
            with st.expander("Source excerpt"):
                st.write(row.get("source_excerpt"))
            confirmed = bool(row.get("user_confirmed"))
            new_value = st.checkbox(
                "User confirmed for overlay review",
                value=confirmed,
                key=f"confirm_{row.get('intel_view_id')}",
            )
            if new_value != confirmed:
                set_trade_view_confirmed(ctx.config, str(row.get("intel_view_id")), new_value)
                st.rerun()
            media_id = row.get("media_id")
            if media_id and pd.notna(media_id):
                media = table_df(ctx.config, "intel_media_items")
                media_row = media[media["media_id"] == media_id] if not media.empty else pd.DataFrame()
                if not media_row.empty:
                    path = Path(str(media_row.iloc[0]["local_path"]))
                    if path.exists():
                        st.image(str(path), use_container_width=True)
