from __future__ import annotations

import pandas as pd
import streamlit as st

from sectorscout.intel.workflow import build_research_queue, friendly_queue_rows, workflow_summary
from sectorscout.ui.data import UIContext, data_freshness_status, filtered_count, latest_rows, row_count, table_df
from sectorscout.ui.report_panel import render_daily_report_panel
from sectorscout.ui.workbench import (
    get_selected_symbol,
    render_symbol_focus_control,
    render_ticker_inspector,
    symbol_rows_dataframe,
)


def _metric_grid(items: list[tuple[str, object]]) -> None:
    columns = st.columns(4)
    for index, (label, value) in enumerate(items):
        columns[index % 4].metric(label, value)


def _visible_columns(frame: pd.DataFrame, preferred: list[str]) -> list[str]:
    return [column for column in preferred if column in frame.columns]


def _render_focus_strip(ctx: UIContext, selected_symbol: str | None, workflow: dict[str, int]) -> None:
    asof = ctx.asof_date.isoformat() if ctx.asof_date else "fixture/seed"
    symbol = selected_symbol or "none"
    st.markdown(
        f"""
        <div class="ss-focus-strip">
          <div class="ss-focus-title">Current research focus: {symbol}</div>
          <div class="ss-focus-meta">
            <span class="ss-pill ss-pill-blue">as-of <strong>{asof}</strong></span>
            <span class="ss-pill ss-pill-amber">queue <strong>{workflow["total"]}</strong></span>
            <span class="ss-pill ss-pill-purple">image review <strong>{filtered_count(ctx.config, "intel_image_observations", "requires_review = true")}</strong></span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_review_queue(ctx: UIContext) -> None:
    st.markdown('<div class="ss-section-title"><span>Research Checklist</span><span>highest priority first</span></div>', unsafe_allow_html=True)
    queue = build_research_queue(ctx.config, asof_date=ctx.asof_date)
    if not queue:
        st.success("No open workflow items.")
        return
    queue_df = pd.DataFrame(friendly_queue_rows(queue))
    st.dataframe(queue_df.head(30), use_container_width=True, hide_index=True)
    top = queue_df.iloc[0]
    with st.container(border=True):
        st.markdown("**Top item in plain English**")
        st.write(f"**{top['What this means']}** for **{top['Symbol or item']}**")
        st.write(top["Why it matters"])
        st.write(f"Next: {top['Suggested next step']}")


def _render_watchlist(ctx: UIContext) -> None:
    st.markdown('<div class="ss-section-title"><span>Watchlist</span><span>internal candidates + external overlay</span></div>', unsafe_allow_html=True)
    rows = symbol_rows_dataframe(ctx)
    if rows.empty:
        st.markdown('<div class="ss-empty-note">Capture context or load demo data to populate the watchlist.</div>', unsafe_allow_html=True)
        return
    st.dataframe(rows, use_container_width=True, hide_index=True)


def _render_market_board(ctx: UIContext) -> None:
    tabs = st.tabs(["Themes", "Stocks", "Setups", "Overlap"])
    with tabs[0]:
        themes = latest_rows(ctx.config, "theme_scores")
        if themes.empty:
            st.write("No theme score rows available yet.")
        else:
            columns = _visible_columns(themes, ["theme_id", "theme_score", "breadth", "members_count", "asof_date"])
            if columns:
                sort_column = "theme_score" if "theme_score" in columns else columns[0]
                st.dataframe(themes[columns].sort_values(sort_column, ascending=False), use_container_width=True, hide_index=True)
            else:
                st.dataframe(themes, use_container_width=True, hide_index=True)
    with tabs[1]:
        stocks = latest_rows(ctx.config, "stock_scores")
        if stocks.empty:
            st.write("No stock score rows available yet.")
        else:
            columns = _visible_columns(
                stocks,
                [
                    "symbol",
                    "theme_id",
                    "stock_opportunity_score",
                    "technical_score",
                    "fundamental_score",
                    "rs_percentile",
                    "state",
                ],
            )
            if columns:
                sort_column = "stock_opportunity_score" if "stock_opportunity_score" in columns else columns[0]
                st.dataframe(stocks[columns].sort_values(sort_column, ascending=False), use_container_width=True, hide_index=True)
            else:
                st.dataframe(stocks, use_container_width=True, hide_index=True)
    with tabs[2]:
        setups = latest_rows(ctx.config, "signals")
        if setups.empty:
            st.write("No setup candidates available yet.")
        else:
            columns = _visible_columns(
                setups,
                [
                    "symbol",
                    "theme_id",
                    "setup_type",
                    "state",
                    "action_category",
                    "market_gate_reason",
                    "portfolio_risk_reason",
                ],
            )
            st.dataframe(setups[columns] if columns else setups, use_container_width=True, hide_index=True)
    with tabs[3]:
        from sectorscout.intel.overlap import compute_overlap

        overlap = pd.DataFrame(compute_overlap(ctx.config, asof_date=ctx.asof_date))
        if overlap.empty:
            st.write("No overlap rows yet.")
        else:
            st.dataframe(overlap, use_container_width=True, hide_index=True)


def _render_external_context(ctx: UIContext) -> None:
    views = table_df(ctx.config, "intel_trade_views")
    if views.empty:
        st.write("Chandler seed, X API, public pages, and manual captures will appear here.")
        return
    columns = _visible_columns(
        views,
        [
            "source_id",
            "platform",
            "direction",
            "timeframe",
            "summary",
            "requires_review",
            "user_confirmed",
            "created_at",
        ],
    )
    st.dataframe(views.sort_values("created_at", ascending=False)[columns].head(40), use_container_width=True, hide_index=True)


def render(ctx: UIContext) -> None:
    st.title("Research Workbench")
    st.markdown(
        """
        <div class="ss-page-note">
          SectorScout is a post-market daily/weekly research and QA system.
          This dashboard does not provide investment advice, does not auto-trade,
          and does not report strategy performance.
        </div>
        """,
        unsafe_allow_html=True,
    )
    market = latest_rows(ctx.config, "market_regime")
    risk_state = market.iloc[0]["risk_state"] if not market.empty and "risk_state" in market else "unknown"
    workflow = workflow_summary(ctx.config, asof_date=ctx.asof_date)
    freshness = data_freshness_status(ctx.asof_date)
    metrics = [
        ("Local data date", ctx.asof_date.isoformat() if ctx.asof_date else "fixture/seed"),
        ("Today", freshness["today"].isoformat()),
        ("Data freshness", freshness["status"]),
        ("Market regime", risk_state),
        ("Open review items", workflow["total"]),
        ("Highest priority items", workflow["urgent"]),
        ("Universe rows", row_count(ctx.config, "symbols")),
        ("External views", row_count(ctx.config, "intel_trade_views")),
        ("Media captures", row_count(ctx.config, "intel_media_items")),
        ("Pending image review", filtered_count(ctx.config, "intel_image_observations", "requires_review = true")),
        ("Review marks", row_count(ctx.config, "intel_review_marks")),
        ("Research notes", row_count(ctx.config, "intel_notes")),
        ("Trade ledger QA rows", row_count(ctx.config, "trade_ledger")),
        ("Data quality rows", row_count(ctx.config, "data_quality_daily")),
    ]
    _metric_grid(metrics[:8])
    if freshness["status"] == "Stale local snapshot":
        st.warning(freshness["message"])
    else:
        st.info(freshness["message"])

    selector_col, focus_col = st.columns([1, 3], gap="medium")
    with selector_col:
        selected_symbol = render_symbol_focus_control(ctx, label="Symbol focus")
    with focus_col:
        _render_focus_strip(ctx, selected_symbol or get_selected_symbol(ctx), workflow)

    tabs = st.tabs(["Checklist", "Watchlist", "Market Board", "External Context", "Ticker Detail", "Report"])
    with tabs[0]:
        _render_review_queue(ctx)
    with tabs[1]:
        _render_watchlist(ctx)
    with tabs[2]:
        _render_market_board(ctx)
    with tabs[3]:
        _render_external_context(ctx)
    with tabs[4]:
        render_ticker_inspector(ctx, selected_symbol)
    with tabs[5]:
        render_daily_report_panel(ctx, key_prefix="overview")
