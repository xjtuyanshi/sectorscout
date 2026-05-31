from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from sectorscout.hindsight import (
    historical_pattern_summary,
    load_hindsight_cases,
    latest_hindsight_results,
    scan_hindsight_cases,
    seed_hindsight_cases,
)
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
    "hindsight_case_studies",
    "hindsight_scan_results",
]


def render(ctx: UIContext) -> None:
    st.title("Historical Pattern Discovery Lab")
    st.markdown(
        """
        <div class="ss-page-note">
          This is the separate place for studying historical leaders and asking:
          which industry themes and technical structures showed up before the move?
          The live research dashboard stays focused on daily review, capture, and QA visibility.
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.subheader("Why this is separate")
    st.write(
        "The goal is not to admire winners after the fact. The goal is to turn historical leaders into testable "
        "industry and technical patterns, then replay those patterns with point-in-time data, as-of universe "
        "membership, reproducible config, and explicit rule versions."
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

    st.subheader("Historical leader cases")
    case_objects = load_hindsight_cases()
    cases = pd.DataFrame([case.to_dict() for case in case_objects])
    st.dataframe(cases, use_container_width=True, hide_index=True)
    action_cols = st.columns([1, 1, 2])
    if action_cols[0].button("Seed cases", use_container_width=True):
        count = seed_hindsight_cases(ctx.config)
        st.success(f"Seeded {count} hindsight cases.")
    if action_cols[1].button("Run scan", use_container_width=True):
        results = scan_hindsight_cases(ctx.config)
        st.success(f"Scanned {len(results)} hindsight cases.")
        st.dataframe([_display_result(result.to_dict()) for result in results], use_container_width=True, hide_index=True)
    action_cols[2].caption("Case-study diagnostics only. This does not validate a strategy or change SectorScout scores.")

    st.subheader("Latest scan results")
    latest = latest_hindsight_results(ctx.config)
    if latest.empty:
        st.info("No hindsight scan results yet. Seed cases and run a scan after historical price data is loaded.")
    else:
        st.dataframe(_display_results_frame(latest), use_container_width=True, hide_index=True)

    st.subheader("Pattern discovery summary")
    summary = historical_pattern_summary(case_objects, latest)
    st.caption("Industry patterns show what theme/category to study. Technical patterns show what chart/price behavior to test.")
    st.dataframe(summary["industry_patterns"], use_container_width=True, hide_index=True)
    st.dataframe(summary["technical_patterns"], use_container_width=True, hide_index=True)

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


def _display_result(row: dict) -> dict:
    return {
        "Symbol": row.get("symbol"),
        "Case": row.get("label"),
        "Window": f"{row.get('scan_start')} to {row.get('scan_end')}",
        "Price rows": row.get("price_rows"),
        "Max gain %": _round_or_none(row.get("max_gain_pct")),
        "Max drawdown %": _round_or_none(row.get("max_drawdown_pct")),
        "RS near start": _round_or_none(row.get("rs_percentile_start")),
        "Hindsight score": _round_or_none(row.get("hindsight_score")),
        "Data quality": row.get("data_quality"),
        "Notes": row.get("notes"),
    }


def _display_results_frame(frame: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame([_display_result(row.to_dict()) for _, row in frame.iterrows()])


def _round_or_none(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), 2)
