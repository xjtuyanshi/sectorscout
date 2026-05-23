from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from sectorscout.intel.x_collector import collect_x_recent_search, load_x_sources, x_api_status
from sectorscout.intel.storage import set_trade_view_confirmed
from sectorscout.ui.data import UIContext, parse_json_list, table_df
from sectorscout.ui.workbench import render_ticker_inspector, set_selected_symbol


def _display_symbols(value: object) -> str:
    return ", ".join(str(item) for item in parse_json_list(value))


def _review_label(row: pd.Series) -> str:
    if bool(row.get("requires_review")) and row.get("media_id") and pd.notna(row.get("media_id")):
        return "Unreviewed extraction draft"
    if bool(row.get("requires_review")):
        return "Needs manual review"
    if bool(row.get("user_confirmed")):
        return "User-confirmed overlay context"
    return "Stored overlay context"


def _short_label(row: pd.Series) -> str:
    symbols = _display_symbols(row.get("canonical_symbols_json")) or "context"
    summary = str(row.get("summary") or "").replace("\n", " ")
    if len(summary) > 72:
        summary = summary[:69].rstrip() + "..."
    return f"{row.get('source_id')} | {symbols} | {row.get('direction')} | {summary}"


def _render_intel_table(views: pd.DataFrame) -> None:
    columns = [
        column
        for column in [
            "source_id",
            "platform",
            "direction",
            "timeframe",
            "summary",
            "requires_review",
            "user_confirmed",
            "created_at",
        ]
        if column in views.columns
    ]
    st.dataframe(views[columns].head(80), use_container_width=True, hide_index=True)


def render(ctx: UIContext) -> None:
    st.title("External Intel")
    st.markdown(
        """
        <div class="ss-page-note">
          External context is an overlay only. It does not change SectorScout base scores,
          setup candidates, execution QA, lifecycle QA, or ledger QA.
        </div>
        """,
        unsafe_allow_html=True,
    )
    with st.expander("X API public collection", expanded=False):
        status = x_api_status()
        sources = load_x_sources()
        st.write(
            {
                "status": status["status"],
                "endpoint": status["endpoint"],
                "default_handles": [source.handle for source in sources if source.enabled],
                "boundary": "Official X API only; no logged-in browser capture, cookies, sessions, or private-page automation.",
            }
        )
        handle_text = st.text_input(
            "Handles",
            value=", ".join(source.handle for source in sources if source.enabled),
            help="Comma-separated public X handles.",
        )
        col_x1, col_x2, col_x3 = st.columns([1, 1, 2])
        max_results = col_x1.slider("Max posts", min_value=10, max_value=100, value=50, step=10)
        include_replies = col_x2.checkbox("Include replies", value=False)
        collect_clicked = col_x3.button("Collect public X posts", use_container_width=True)
        if collect_clicked:
            handles = [item.strip() for item in handle_text.split(",") if item.strip()]
            result = collect_x_recent_search(
                ctx.config,
                handles=handles,
                asof_date=ctx.asof_date,
                max_results=max_results,
                include_replies=include_replies,
            )
            if result.status == "COLLECTED":
                st.success(f"Collected {result.posts_seen} public posts; created {result.trade_views} draft overlay views.")
            elif result.status == "SKIPPED":
                st.warning(result.reason)
            else:
                st.error(result.reason or result.status)
            st.json(result.to_dict())

    views = table_df(ctx.config, "intel_trade_views", limit=1000)
    if views.empty:
        st.warning("No external views are stored yet. Use Capture Inbox or reload the Chandler seed.")
        return
    filters = st.columns([1.2, 1.2, 1, 1])
    source_filter = filters[0].multiselect("Source", sorted(views["source_id"].dropna().unique().tolist()))
    direction_filter = filters[1].multiselect("Direction/context", sorted(views["direction"].dropna().unique().tolist()))
    review_filter = filters[2].selectbox("Review status", ["all", "needs_review", "confirmed", "not_confirmed"])
    symbol_query = filters[3].text_input("Symbol", placeholder="NQ, QQQ, NVDA")
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
    sorted_views = views.sort_values("created_at", ascending=False)
    st.markdown('<div class="ss-section-title"><span>Intel Table</span><span>latest first</span></div>', unsafe_allow_html=True)
    if sorted_views.empty:
        st.info("No rows match the current filters.")
        return
    _render_intel_table(sorted_views)

    options = sorted_views["intel_view_id"].astype(str).tolist()
    labels = {str(row["intel_view_id"]): _short_label(row) for _, row in sorted_views.iterrows()}
    selected_id = st.selectbox(
        "Selected context",
        options,
        format_func=lambda value: labels.get(str(value), str(value)),
    )
    selected_row = sorted_views[sorted_views["intel_view_id"].astype(str) == str(selected_id)].iloc[0]
    symbols = parse_json_list(selected_row.get("canonical_symbols_json"))
    if symbols:
        set_selected_symbol(str(symbols[0]))

    detail_col, inspector_col = st.columns([2.2, 1], gap="large")
    with detail_col:
        _render_view_detail(ctx, selected_row)
    with inspector_col:
        render_ticker_inspector(ctx)


def _render_view_detail(ctx: UIContext, row: pd.Series) -> None:
    title = row.get("source_title") or row.get("source_id")
    st.markdown('<div class="ss-section-title"><span>Selected Context</span><span>source attributed</span></div>', unsafe_allow_html=True)
    with st.container(border=True):
        st.subheader(str(title))
        st.caption(
            f"{row.get('platform') or 'unknown'} | {row.get('author') or 'unknown author'} | "
            f"rights={row.get('rights_scope')} | clarity={row.get('extraction_confidence')} | "
            f"status={_review_label(row)}"
        )
        st.code(str(row.get("intel_view_id")), language=None)
        st.write(row.get("summary"))
        cols = st.columns(2)
        cols[0].write(f"Tickers: {_display_symbols(row.get('canonical_symbols_json')) or 'none'}")
        cols[1].write(f"Timeframe: {row.get('timeframe')}")
        cols[0].write(f"Direction/context: {row.get('direction')}")
        cols[1].write(f"Key levels: {_display_symbols(row.get('key_levels_json')) or 'none'}")
        if row.get("trigger_condition"):
            st.write(f"Trigger condition: {row.get('trigger_condition')}")
        if row.get("invalidation_condition"):
            st.write(f"Invalidation: {row.get('invalidation_condition')}")
        if row.get("target_area"):
            st.write(f"Target area: {row.get('target_area')}")
        if row.get("no_trade_condition"):
            st.write(f"No-trade condition: {row.get('no_trade_condition')}")
        if row.get("url"):
            st.link_button("Open public source", str(row.get("url")))
        with st.expander("Source excerpt"):
            st.write(row.get("source_excerpt"))
        confirmed = bool(row.get("user_confirmed"))
        new_value = st.checkbox(
            "Confirm for overlay review",
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
