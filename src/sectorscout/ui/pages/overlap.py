from __future__ import annotations

from html import escape

import pandas as pd
import streamlit as st

from sectorscout.intel.overlap import compute_overlap
from sectorscout.ui.data import UIContext
from sectorscout.ui.workbench import (
    display_cell,
    friendly_external_context,
    friendly_internal_status,
    friendly_overlap_label,
    overlap_next_step_hint,
    overlap_rows_dataframe,
    render_ticker_inspector,
    set_selected_symbol,
)


def _render_plain_review_rows(rows: list[dict]) -> None:
    st.markdown('<div class="ss-section-title"><span>Plain-English Review List</span><span>top rows</span></div>', unsafe_allow_html=True)
    for row in rows[:10]:
        label = str(row.get("overlap_label") or "")
        symbol = escape(display_cell(row.get("symbol")))
        theme = escape(display_cell(row.get("theme"), "No theme in snapshot"))
        meaning = escape(friendly_overlap_label(label))
        internal = escape(friendly_internal_status(row.get("internal_status"), row.get("setup_status")))
        outside = escape(friendly_external_context(row.get("external_bias")))
        sources = escape(display_cell(row.get("external_sources")))
        next_step = escape(overlap_next_step_hint(label))
        st.markdown(
            f"""
            <div class="ss-panel" style="margin-bottom: 8px;">
              <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;">
                <div>
                  <div style="font-weight:780;font-size:1.05rem;">{symbol}</div>
                  <div class="ss-muted">{theme}</div>
                </div>
                <div>{meaning}</div>
              </div>
              <div class="ss-kv">
                <div>SectorScout</div><div>{internal}</div>
                <div>Outside</div><div>{outside}</div>
                <div>Sources</div><div>{sources}</div>
                <div>Next</div><div>{next_step}</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render(ctx: UIContext) -> None:
    st.title("Internal vs External Overlap")
    st.caption(
        "This page answers one question: does outside research mention the same symbols SectorScout is watching? "
        "External context is overlay-only and does not change base scores."
    )
    rows = compute_overlap(ctx.config, asof_date=ctx.asof_date)
    if not rows:
        st.info("No overlap rows yet. Capture external context or add internal seed rows.")
        return
    df = pd.DataFrame(rows)
    status_options = sorted(df["overlap_label"].unique().tolist())
    status_label_to_value = {friendly_overlap_label(label): label for label in status_options}
    selected_statuses = st.multiselect("Status", list(status_label_to_value))
    if selected_statuses:
        df = df[df["overlap_label"].isin([status_label_to_value[label] for label in selected_statuses])]
    records = df.to_dict("records")
    _render_plain_review_rows(records)
    with st.expander("Detailed overlap table", expanded=False):
        st.dataframe(overlap_rows_dataframe(records), use_container_width=True, hide_index=True)
    if not df.empty:
        selected_symbol = st.selectbox("Inspect symbol", df["symbol"].astype(str).tolist())
        set_selected_symbol(selected_symbol)
    render_ticker_inspector(ctx)
