from __future__ import annotations

import pandas as pd
import streamlit as st

from sectorscout.intel.overlap import compute_overlap
from sectorscout.ui.data import UIContext


def render(ctx: UIContext) -> None:
    st.title("Internal vs External Overlap")
    st.caption("External context is displayed as an overlay and does not change SectorScout scores.")
    rows = compute_overlap(ctx.config)
    if not rows:
        st.info("No overlap rows yet. Capture external context or add internal seed rows.")
        return
    df = pd.DataFrame(rows)
    labels = st.multiselect("Labels", sorted(df["overlap_label"].unique().tolist()))
    if labels:
        df = df[df["overlap_label"].isin(labels)]
    st.dataframe(df, use_container_width=True)
