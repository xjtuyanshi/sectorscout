from __future__ import annotations

import pandas as pd
import streamlit as st

from sectorscout.intel.workflow import build_research_queue, workflow_summary
from sectorscout.ui.data import UIContext, filtered_count, latest_rows, row_count, table_df
from sectorscout.ui.report_panel import render_daily_report_panel
from sectorscout.ui.workbench import render_symbol_rail, render_ticker_inspector


def _metric_grid(items: list[tuple[str, object]]) -> None:
    columns = st.columns(6)
    for index, (label, value) in enumerate(items):
        columns[index % 6].metric(label, value)


def render(ctx: UIContext) -> None:
    st.title("Research Workbench")
    st.info(
        "SectorScout is a post-market daily/weekly research and QA system. "
        "This dashboard does not provide investment advice, does not auto-trade, "
        "and does not report strategy performance."
    )
    market = latest_rows(ctx.config, "market_regime")
    risk_state = market.iloc[0]["risk_state"] if not market.empty and "risk_state" in market else "unknown"
    workflow = workflow_summary(ctx.config, asof_date=ctx.asof_date)
    metrics = [
        ("As-of date", ctx.asof_date.isoformat() if ctx.asof_date else "fixture/seed"),
        ("Market regime", risk_state),
        ("Workflow queue", workflow["total"]),
        ("Urgent review items", workflow["urgent"]),
        ("Universe rows", row_count(ctx.config, "symbols")),
        ("Theme score rows", row_count(ctx.config, "theme_scores")),
        ("Stock score rows", row_count(ctx.config, "stock_scores")),
        ("Setup candidate rows", row_count(ctx.config, "signals")),
        ("External views", row_count(ctx.config, "intel_trade_views")),
        ("Media captures", row_count(ctx.config, "intel_media_items")),
        ("Pending image review", filtered_count(ctx.config, "intel_image_observations", "requires_review = true")),
        ("Review marks", row_count(ctx.config, "intel_review_marks")),
        ("Research notes", row_count(ctx.config, "intel_notes")),
        ("Trade ledger QA rows", row_count(ctx.config, "trade_ledger")),
        ("Data quality rows", row_count(ctx.config, "data_quality_daily")),
    ]
    _metric_grid(metrics[:12])

    left, center, right = st.columns([1.12, 2.45, 1.35], gap="medium")
    with left:
        selected_symbol = render_symbol_rail(ctx)
        st.markdown('<div class="ss-section-title"><span>Review Queue</span><span>priority</span></div>', unsafe_allow_html=True)
        queue = build_research_queue(ctx.config, asof_date=ctx.asof_date)
        if queue:
            queue_df = pd.DataFrame(queue)
            visible_queue = queue_df[queue_df["priority"] <= 40]
            st.dataframe(
                visible_queue[
                    ["priority", "bucket", "symbol", "page", "reason"]
                ].head(12),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.success("No open workflow items.")

    with center:
        st.markdown('<div class="ss-section-title"><span>Market Board</span><span>ranked research context</span></div>', unsafe_allow_html=True)
        tabs = st.tabs(["Themes", "Stocks", "Setups", "Overlap", "Report"])
        with tabs[0]:
            themes = latest_rows(ctx.config, "theme_scores")
            if not themes.empty:
                columns = [col for col in ["theme_id", "theme_score", "breadth", "members_count", "asof_date"] if col in themes]
                sort_column = "theme_score" if "theme_score" in themes else columns[0]
                st.dataframe(themes[columns].sort_values(sort_column, ascending=False), use_container_width=True, hide_index=True)
            else:
                st.write("No theme score rows available yet.")
        with tabs[1]:
            stocks = latest_rows(ctx.config, "stock_scores")
            if not stocks.empty:
                columns = [
                    col
                    for col in [
                        "symbol",
                        "theme_id",
                        "stock_opportunity_score",
                        "technical_score",
                        "fundamental_score",
                        "rs_percentile",
                        "state",
                    ]
                    if col in stocks
                ]
                sort_column = "stock_opportunity_score" if "stock_opportunity_score" in stocks else columns[0]
                st.dataframe(stocks[columns].sort_values(sort_column, ascending=False), use_container_width=True, hide_index=True)
            else:
                st.write("No stock score rows available yet.")
        with tabs[2]:
            setups = latest_rows(ctx.config, "signals")
            if not setups.empty:
                columns = [
                    col
                    for col in [
                        "symbol",
                        "theme_id",
                        "setup_type",
                        "state",
                        "action_category",
                        "market_gate_reason",
                        "portfolio_risk_reason",
                    ]
                    if col in setups
                ]
                st.dataframe(setups[columns], use_container_width=True, hide_index=True)
            else:
                st.write("No setup candidates available yet.")
        with tabs[3]:
            from sectorscout.intel.overlap import compute_overlap

            overlap = pd.DataFrame(compute_overlap(ctx.config, asof_date=ctx.asof_date))
            if not overlap.empty:
                st.dataframe(overlap, use_container_width=True, hide_index=True)
            else:
                st.write("No overlap rows yet.")
        with tabs[4]:
            render_daily_report_panel(ctx, key_prefix="overview")

    with right:
        render_ticker_inspector(ctx, selected_symbol)
        st.markdown('<div class="ss-section-title"><span>External Tape</span><span>latest</span></div>', unsafe_allow_html=True)
        views = table_df(ctx.config, "intel_trade_views")
        if not views.empty:
            columns = [col for col in ["source_id", "direction", "timeframe", "summary", "requires_review"] if col in views]
            st.dataframe(views.sort_values("created_at", ascending=False)[columns].head(8), use_container_width=True, hide_index=True)
        else:
            st.write("Chandler seed, X API, public pages, and manual captures will appear here.")
