from __future__ import annotations

from datetime import date
from pathlib import Path

import streamlit as st

from sectorscout.intel.report import build_report_inclusion_summary, generate_intel_daily_report
from sectorscout.ui.data import UIContext


def _show_summary(summary: dict[str, object]) -> None:
    included = summary["included"]
    needs_review = summary["needs_review"]
    excluded = summary["excluded"]
    cols = st.columns(4)
    cols[0].metric("Included external views", included["external_views"])
    cols[1].metric("Workflow items", included["workflow_queue_items"])
    cols[2].metric("Image drafts needing review", needs_review["unreviewed_image_drafts"])
    cols[3].metric("Other-date views excluded", excluded["other_date_external_views"])
    warnings = summary.get("warnings") or []
    if warnings:
        st.warning("\n".join(f"- {warning}" for warning in warnings))
    with st.expander("Report inclusion details"):
        st.json(summary)


def render_daily_report_panel(ctx: UIContext, *, key_prefix: str) -> None:
    st.subheader("Daily Report")
    default_date = ctx.asof_date or date.today()
    report_date = st.date_input("Report date", value=default_date, key=f"{key_prefix}_report_date")
    summary = build_report_inclusion_summary(ctx.config, report_date)
    _show_summary(summary)
    state_key = f"{key_prefix}_generated_report"
    if st.button(f"Generate daily report for {report_date.isoformat()}", key=f"{key_prefix}_generate_report"):
        path = generate_intel_daily_report(ctx.config, report_date)
        report_text = Path(path).read_text(encoding="utf-8")
        st.session_state[state_key] = {"path": str(path), "text": report_text}

    generated = st.session_state.get(state_key)
    if generated:
        path_text = str(generated["path"])
        report_text = str(generated["text"])
        st.success(f"Generated report: {path_text}")
        st.download_button(
            "Export Markdown report",
            data=report_text,
            file_name=Path(path_text).name,
            mime="text/markdown",
            key=f"{key_prefix}_download_report",
        )
        st.text_area("Report preview", value=report_text, height=360, key=f"{key_prefix}_report_preview")
