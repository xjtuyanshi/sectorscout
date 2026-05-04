from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from sectorscout.intel.report import generate_intel_daily_report
from sectorscout.intel.workflow import build_research_queue, workflow_summary
from sectorscout.ui.data import UIContext, filtered_count, latest_rows, row_count, table_df


def _metric_grid(items: list[tuple[str, object]]) -> None:
    columns = st.columns(4)
    for index, (label, value) in enumerate(items):
        columns[index % 4].metric(label, value)


def render(ctx: UIContext) -> None:
    st.title("SectorScout Intel Capture")
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
    _metric_grid(metrics)

    st.subheader("Daily Research Workflow")
    st.markdown(
        """
        1. Capture public/manual context.
        2. Confirm extracted views and screenshots.
        3. Check overlap conflicts.
        4. Add notes.
        5. Generate the daily report.
        """
    )
    queue = build_research_queue(ctx.config, asof_date=ctx.asof_date)
    if queue:
        queue_df = pd.DataFrame(queue)
        priority_filter = st.slider("Max queue priority", min_value=5, max_value=60, value=40, step=5)
        visible_queue = queue_df[queue_df["priority"] <= priority_filter]
        st.dataframe(
            visible_queue[
                ["priority", "bucket", "symbol", "source", "page", "reason", "next_step", "object_id"]
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.success("No open workflow items. Capture new context or generate the daily report.")
    report_date = ctx.asof_date or date.today()
    if st.button(f"Generate daily report for {report_date.isoformat()}"):
        path = generate_intel_daily_report(ctx.config, report_date)
        st.success(f"Generated {path}")

    st.subheader("What To Review Next")
    col1, col2 = st.columns(2)
    with col1:
        st.caption("Top themes")
        themes = latest_rows(ctx.config, "theme_scores")
        if not themes.empty:
            st.dataframe(themes.head(8), use_container_width=True)
        else:
            st.write("No theme score rows available yet.")
    with col2:
        st.caption("External context")
        views = table_df(ctx.config, "intel_trade_views")
        if not views.empty:
            st.dataframe(
                views[["source_id", "direction", "timeframe", "summary", "requires_review"]].head(8),
                use_container_width=True,
            )
        else:
            st.write("Chandler seed or manual capture will appear here.")
