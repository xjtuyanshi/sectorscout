from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from sectorscout.intel.storage import insert_review_mark
from sectorscout.intel.workflow import build_research_queue, friendly_bucket_label, friendly_queue_rows, workflow_summary
from sectorscout.ui.data import UIContext
from sectorscout.ui.report_panel import render_daily_report_panel


def _valid_follow_up(value: str) -> tuple[bool, str | None]:
    if not value.strip():
        return True, None
    try:
        parsed = date.fromisoformat(value.strip())
    except ValueError:
        return False, None
    return True, parsed.isoformat()


def render(ctx: UIContext) -> None:
    st.title("Research Checklist")
    st.caption(
        "Plain-language tasks that help you review captures, screenshots, overlaps, follow-ups, and reports. "
        "These tasks do not change SectorScout scores."
    )
    summary = workflow_summary(ctx.config, asof_date=ctx.asof_date)
    cols = st.columns(5)
    cols[0].metric("Open items", summary["total"])
    cols[1].metric("Highest priority", summary["urgent"])
    cols[2].metric("Needs your review", summary["needs_review"])
    cols[3].metric("Possible disagreements", summary["conflicts"])
    cols[4].metric("Follow-ups due", summary["follow_ups_due"])

    queue = build_research_queue(ctx.config, asof_date=ctx.asof_date)
    if not queue:
        st.success("No open workflow items. Capture new context or generate a report.")
        return

    df = pd.DataFrame(queue)
    col1, col2, col3 = st.columns(3)
    bucket_options = sorted(df["bucket"].unique().tolist())
    bucket_label_to_value = {friendly_bucket_label(bucket): bucket for bucket in bucket_options}
    bucket_labels = col1.multiselect("Task type", list(bucket_label_to_value))
    pages = col2.multiselect("Page", sorted(df["page"].unique().tolist()))
    max_priority = col3.slider("Max priority", min_value=5, max_value=60, value=60, step=5)
    if bucket_labels:
        df = df[df["bucket"].isin([bucket_label_to_value[label] for label in bucket_labels])]
    if pages:
        df = df[df["page"].isin(pages)]
    df = df[df["priority"] <= max_priority]
    if df.empty:
        st.info("No queue items match the current filters.")
    else:
        display_df = pd.DataFrame(friendly_queue_rows(df.to_dict("records")))
        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
        )

        st.subheader("Review Selected Item")
        option_rows = df.to_dict("records")
        labels = [
            f"P{row['priority']} {friendly_bucket_label(row['bucket'])}: {row.get('symbol') or row['object_id']}"
            for row in option_rows
        ]
        selected_label = st.selectbox("Queue item", labels)
        selected = option_rows[labels.index(selected_label)]
        st.caption(f"Internal reference: {selected['object_type']} / {selected['object_id']}")
        with st.form("workflow_review_form"):
            review_status = st.selectbox(
                "Review status",
                ["needs_more_data", "unclear", "not_triggered", "triggered", "worked", "failed", "expired"],
            )
            notes = st.text_area("Review note", value=selected["next_step"])
            plan = st.text_area("Research plan")
            follow_up = st.text_input("Follow-up date", placeholder="YYYY-MM-DD")
            submitted = st.form_submit_button("Save review mark")
        if submitted:
            valid_follow_up, parsed_follow_up = _valid_follow_up(follow_up)
            if not valid_follow_up:
                st.error("Follow-up date must use YYYY-MM-DD.")
            else:
                insert_review_mark(
                    ctx.config,
                    object_type=str(selected["object_type"]),
                    object_id=str(selected["object_id"]),
                    review_status=review_status,
                    notes=notes or None,
                    personal_plan=plan or None,
                    follow_up_date=parsed_follow_up,
                )
                st.success("Saved review mark. Future follow-up dates defer this item until due.")
                st.rerun()

    render_daily_report_panel(ctx, key_prefix="workflow")

    st.subheader("Workflow Notes")
    st.write(
        "Use this queue as the daily research checklist. It is intentionally separate from SectorScout scoring: "
        "clearing this queue improves review quality and traceability, not base scores."
    )
