from __future__ import annotations

import streamlit as st

from sectorscout.intel.public_sources import load_public_sources
from sectorscout.ui.data import UIContext, row_count, system_status, table_df, table_exists


TABLES = [
    "symbols",
    "daily_prices",
    "theme_members",
    "theme_scores",
    "stock_scores",
    "signals",
    "execution_decisions",
    "simulated_positions",
    "exit_decisions",
    "trade_ledger",
    "lifecycle_qa",
    "intel_raw_items",
    "intel_media_items",
    "intel_image_observations",
    "intel_trade_views",
    "intel_review_marks",
    "intel_notes",
]


def render(ctx: UIContext) -> None:
    st.title("Settings / Source Status")
    status = system_status(ctx.config)
    st.subheader("SectorScout")
    st.json(status)
    st.subheader("Source Status")
    st.write(
        {
            "public_web": "enabled",
            "manual_capture": "enabled",
            "vision_provider": "configured" if status["openai_vision_provider"] else "missing",
            "x_api": status["x_api_status"],
            "discord_manual_capture": "enabled",
            "discord_official_bot": "not implemented in MVP",
            "compliance": [
                "no self-bot",
                "no personal token scraping",
                "no cookie/session scraping",
                "no browser-login scraping",
                "no private channel auto-crawling",
            ],
        }
    )
    st.subheader("Row Counts")
    st.dataframe(
        [{"table": table, "available": table_exists(ctx.config, table), "rows": row_count(ctx.config, table)} for table in TABLES],
        use_container_width=True,
    )
    st.subheader("Configured Public Sources")
    sources = load_public_sources()
    if sources:
        st.dataframe([source.to_dict() for source in sources], use_container_width=True)
    else:
        st.write("No public source registry found.")
    st.subheader("Data Quality")
    dq = table_df(ctx.config, "data_quality_daily", limit=100)
    if dq.empty:
        st.write("No data quality rows available.")
    else:
        st.dataframe(dq, use_container_width=True)
