from __future__ import annotations

from datetime import date

import streamlit as st

from sectorscout.ui.data import UIContext, row_count, table_exists


HISTORICAL_TABLES = [
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
]


def render(ctx: UIContext) -> None:
    st.title("Historical Research Lab")
    st.markdown(
        """
        <div class="ss-page-note">
          This is the separate place for historical replay and rule validation work.
          The live research dashboard stays focused on daily review, capture, and QA visibility.
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.subheader("Why this is separate")
    st.write(
        "Historical analysis should use point-in-time data, as-of universe membership, reproducible config, "
        "and explicit rule versions. Keeping it separate prevents external intel or dashboard convenience "
        "features from changing SectorScout base scores."
    )

    st.subheader("Data readiness")
    st.dataframe(
        [
            {
                "dataset": table,
                "available": table_exists(ctx.config, table),
                "rows": row_count(ctx.config, table),
                "why_it_matters": _table_meaning(table),
            }
            for table in HISTORICAL_TABLES
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("Replay scope draft")
    start = st.date_input("Start date", value=date(2024, 1, 1))
    end = st.date_input("End date", value=ctx.asof_date or date.today())
    st.write(
        {
            "selected_start": start.isoformat(),
            "selected_end": end.isoformat(),
            "current_status": "Design scaffold only. No formal validation results are calculated in this MVP screen.",
        }
    )

    st.subheader("Next implementation steps")
    st.dataframe(
        [
            {
                "step": "Historical replay runner",
                "output": "One row per historical candidate/setup decision with config and data provenance.",
            },
            {
                "step": "Lifecycle trace viewer",
                "output": "A readable event log showing candidate, trigger candidate, simulated next-open decision, lifecycle, and exit reason.",
            },
            {
                "step": "Data coverage diagnostics",
                "output": "Missing sessions, provider mix, stale universe membership, and price-path gaps by date and symbol.",
            },
            {
                "step": "Validation module",
                "output": "Separate statistical validation after replay correctness is proven.",
            },
        ],
        use_container_width=True,
        hide_index=True,
    )


def _table_meaning(table: str) -> str:
    return {
        "symbols": "Universe source and symbol metadata.",
        "daily_prices": "Historical price path required for replay.",
        "theme_members": "Point-in-time theme membership.",
        "theme_scores": "Historical theme ranking inputs.",
        "stock_scores": "Historical candidate ranking outputs.",
        "signals": "Historical setup candidates.",
        "execution_decisions": "Simulated next-open decision QA.",
        "simulated_positions": "Lifecycle state rows.",
        "exit_decisions": "Exit decision trace rows.",
        "trade_ledger": "Row-level lifecycle QA ledger.",
        "lifecycle_qa": "Lifecycle coverage and warning rows.",
    }.get(table, "Supporting dataset.")
