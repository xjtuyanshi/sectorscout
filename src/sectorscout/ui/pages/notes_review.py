from __future__ import annotations

import streamlit as st

from sectorscout.intel.storage import insert_review_mark
from sectorscout.ui.data import UIContext, table_df


def render(ctx: UIContext) -> None:
    st.title("Notes / Review")
    st.caption("Manual journaling and QA review. External views are not scored here.")
    object_type = st.selectbox(
        "Object type",
        ["intel_view", "setup_candidate", "execution_qa", "lifecycle_qa", "ledger_qa", "overlap_row"],
    )
    object_id = st.text_input("Object ID or symbol")
    review_status = st.selectbox(
        "Review status",
        ["not_triggered", "triggered", "worked", "failed", "unclear", "expired", "needs_more_data"],
    )
    notes = st.text_area("Notes")
    plan = st.text_area("Research plan")
    follow_up = st.text_input("Follow-up date", placeholder="YYYY-MM-DD")
    if st.button("Save review mark", disabled=not object_id.strip()):
        review_id = insert_review_mark(
            ctx.config,
            object_type=object_type,
            object_id=object_id,
            review_status=review_status,
            notes=notes or None,
            personal_plan=plan or None,
            follow_up_date=follow_up or None,
        )
        st.success(f"Saved review mark {review_id}.")
    marks = table_df(ctx.config, "intel_review_marks", limit=500)
    if not marks.empty:
        st.dataframe(marks.sort_values("reviewed_at", ascending=False), use_container_width=True)
    notes = table_df(ctx.config, "intel_notes", limit=500)
    if not notes.empty:
        st.subheader("Saved Research Notes")
        st.dataframe(notes.sort_values("updated_at", ascending=False), use_container_width=True)
