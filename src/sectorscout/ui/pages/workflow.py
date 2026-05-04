from __future__ import annotations

import pandas as pd
import streamlit as st

from sectorscout.intel.workflow import build_research_queue, workflow_summary
from sectorscout.ui.data import UIContext


def render(ctx: UIContext) -> None:
    st.title("Research Workflow")
    st.caption("A prioritized queue for capture, review, overlap triage, follow-ups, and report preparation.")
    summary = workflow_summary(ctx.config, asof_date=ctx.asof_date)
    cols = st.columns(5)
    cols[0].metric("Queue items", summary["total"])
    cols[1].metric("Urgent", summary["urgent"])
    cols[2].metric("Needs review", summary["needs_review"])
    cols[3].metric("Conflicts", summary["conflicts"])
    cols[4].metric("Follow-ups due", summary["follow_ups_due"])

    queue = build_research_queue(ctx.config, asof_date=ctx.asof_date)
    if not queue:
        st.success("No open workflow items. Capture new context or generate a report.")
        return

    df = pd.DataFrame(queue)
    col1, col2, col3 = st.columns(3)
    buckets = col1.multiselect("Bucket", sorted(df["bucket"].unique().tolist()))
    pages = col2.multiselect("Page", sorted(df["page"].unique().tolist()))
    max_priority = col3.slider("Max priority", min_value=5, max_value=60, value=60, step=5)
    if buckets:
        df = df[df["bucket"].isin(buckets)]
    if pages:
        df = df[df["page"].isin(pages)]
    df = df[df["priority"] <= max_priority]
    st.dataframe(
        df[["priority", "bucket", "symbol", "source", "page", "reason", "next_step", "object_type", "object_id"]],
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("Workflow Notes")
    st.write(
        "Use this queue as the daily research checklist. It is intentionally separate from SectorScout scoring: "
        "clearing this queue improves review quality and traceability, not base scores."
    )
