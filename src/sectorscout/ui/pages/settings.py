from __future__ import annotations

import streamlit as st

from sectorscout.demo import DEMO_ASOF_DATE, demo_readiness
from sectorscout.intel.public_sources import load_public_sources
from sectorscout.intel.x_collector import load_x_sources, x_api_status
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
    "hindsight_case_studies",
    "hindsight_scan_results",
    "hindsight_pattern_observations",
    "hindsight_event_ledger",
    "hindsight_evidence_items",
    "hindsight_replay_gates",
    "hindsight_observation_links",
    "hindsight_hypotheses",
    "hindsight_hypothesis_case_results",
]


def render(ctx: UIContext) -> None:
    st.title("Settings / Source Status")
    status = system_status(ctx.config)
    st.subheader("Demo Readiness")
    readiness_date = ctx.asof_date or DEMO_ASOF_DATE
    readiness = demo_readiness(ctx.config, asof_date=readiness_date)
    ready = all(item["ok"] for item in readiness)
    if ready:
        st.success(f"Demo is ready for {readiness_date.isoformat()}.")
    else:
        st.warning("Demo is not fully ready. Run: sectorscout demo-init --config config.yaml")
    st.dataframe(readiness, use_container_width=True, hide_index=True)
    st.subheader("SectorScout")
    st.json(status)
    st.subheader("Source Status")
    x_status = x_api_status()
    st.write(
        {
            "public_web": "enabled",
            "manual_capture": "enabled",
            "vision_provider": "configured" if status["openai_vision_provider"] else "missing",
            "x_api": x_status,
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
    st.subheader("Configured X Sources")
    st.dataframe([source.to_dict() for source in load_x_sources()], use_container_width=True)
    st.subheader("Data Quality")
    dq = table_df(ctx.config, "data_quality_daily", limit=100)
    if dq.empty:
        st.write("No data quality rows available.")
    else:
        st.dataframe(dq, use_container_width=True)
