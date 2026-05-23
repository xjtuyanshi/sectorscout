from __future__ import annotations

import pandas as pd
import streamlit as st

from sectorscout.intel.overlap import compute_overlap
from sectorscout.ui.data import UIContext
from sectorscout.ui.workbench import render_ticker_inspector, set_selected_symbol


def render(ctx: UIContext) -> None:
    st.title("Internal vs External Overlap")
    st.caption("External context is displayed as an overlay and does not change SectorScout scores.")
    rows = compute_overlap(ctx.config, asof_date=ctx.asof_date)
    if not rows:
        st.info("No overlap rows yet. Capture external context or add internal seed rows.")
        return
    df = pd.DataFrame(rows)
    labels = st.multiselect("Labels", sorted(df["overlap_label"].unique().tolist()))
    if labels:
        df = df[df["overlap_label"].isin(labels)]
    left, right = st.columns([2.3, 1.1], gap="medium")
    with left:
        st.dataframe(df, use_container_width=True, hide_index=True)
        if not df.empty:
            selected_symbol = st.selectbox("Inspect symbol", df["symbol"].astype(str).tolist())
            set_selected_symbol(selected_symbol)
    with right:
        render_ticker_inspector(ctx)
