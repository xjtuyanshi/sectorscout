from __future__ import annotations

import pandas as pd
import streamlit as st

from sectorscout.intel.overlap import compute_overlap
from sectorscout.ui.data import UIContext
from sectorscout.ui.workbench import friendly_overlap_label, render_ticker_inspector, set_selected_symbol


def _friendly_overlap_df(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Symbol": row.get("symbol"),
                "Plain status": friendly_overlap_label(str(row.get("overlap_label") or "")),
                "Sector or theme": row.get("theme") or "-",
                "SectorScout context": row.get("internal_status") or "-",
                "SectorScout score": row.get("internal_score"),
                "External context": row.get("external_bias") or "none",
                "External sources": row.get("external_sources") or "-",
                "What to do": _next_step_hint(str(row.get("overlap_label") or "")),
            }
            for _, row in df.iterrows()
        ]
    )


def _next_step_hint(label: str) -> str:
    return {
        "CONFIRMED": "Review the setup details; external intel is only supporting context.",
        "CONFLICT": "Read both sides and write a manual note before relying on the setup.",
        "EXTERNAL_ONLY": "Decide whether it belongs on your watchlist or should remain outside context.",
        "INTERNAL_ONLY": "Optional: look for outside context if this symbol matters today.",
        "WATCH_ONLY": "Keep it on the radar; there is not enough context yet.",
        "NEEDS_REVIEW": "Confirm the capture or extraction before using it in your review.",
    }.get(label, "Review manually.")


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
    left, right = st.columns([2.3, 1.1], gap="medium")
    with left:
        st.dataframe(_friendly_overlap_df(df), use_container_width=True, hide_index=True)
        if not df.empty:
            selected_symbol = st.selectbox("Inspect symbol", df["symbol"].astype(str).tolist())
            set_selected_symbol(selected_symbol)
    with right:
        render_ticker_inspector(ctx)
