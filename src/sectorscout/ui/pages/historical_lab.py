from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from sectorscout.db import connect_database
from sectorscout.hindsight import (
    build_hindsight_replay_gates,
    fetch_hindsight_prices,
    historical_pattern_summary,
    latest_hindsight_events,
    load_hindsight_cases,
    latest_hindsight_pattern_observations,
    latest_hindsight_replay_gates,
    latest_hindsight_results,
    scan_hindsight_cases,
    seed_hindsight_events,
    seed_hindsight_cases,
)
from sectorscout.intel.storage import insert_review_mark
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
    "hindsight_pattern_observations",
    "hindsight_event_ledger",
    "hindsight_replay_gates",
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
    st.info(
        "Research guardrail: evidence and price data must be visible before the claimed decision point. "
        "Date-only events stay blocked until event timing and first tradable date are resolved."
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
    if action_cols[1].button("Fetch public prices", use_container_width=True):
        fetched = fetch_hindsight_prices(ctx.config)
        st.success(f"Fetched public price history for {len(fetched)} cases.")
        st.dataframe(fetched, use_container_width=True, hide_index=True)
    if action_cols[2].button("Run scan", use_container_width=True):
        results = scan_hindsight_cases(ctx.config)
        st.success(f"Scanned {len(results)} hindsight cases.")
        st.dataframe([_display_result(result.to_dict()) for result in results], use_container_width=True, hide_index=True)
    st.caption("Case-study diagnostics only. This does not validate a strategy or change SectorScout scores.")

    st.subheader("Event Ledger")
    event_cols = st.columns([1, 2])
    if event_cols[0].button("Seed event ledger", use_container_width=True):
        count = seed_hindsight_events(ctx.config)
        st.success(f"Seeded {count} event ledger rows.")
    if event_cols[1].button("Build gate explain rows", use_container_width=True):
        gates = build_hindsight_replay_gates(ctx.config)
        st.success(f"Built {len(gates)} gate explain rows.")
    events = latest_hindsight_events(ctx.config)
    if events.empty:
        st.info("No event ledger rows yet. Seed event ledger before interpreting event-timed patterns.")
    else:
        st.caption(
            "Each event row is the audit spine for timing, source quality, first tradable date, and PIT evidence."
        )
        st.dataframe(_display_events_frame(events), use_container_width=True, hide_index=True)

    st.subheader("Gate Explain Panel")
    gates = latest_hindsight_replay_gates(ctx.config)
    if gates.empty:
        st.info("No gate explain rows yet. Build gates after seeding the event ledger.")
    else:
        st.caption("DATA GAP is not a failure. It means the required evidence or price rows are not available yet.")
        st.dataframe(_display_gates_frame(gates), use_container_width=True, hide_index=True)

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

    st.subheader("Pattern observations")
    observations = latest_hindsight_pattern_observations(ctx.config)
    if observations.empty:
        st.info("No pattern observations yet. Run a scan to create industry, technical, and manual-review observations.")
    else:
        st.caption(
            "These rows are hypothesis observations. Industry and catalyst fields require review; daily OHLCV fields are rule-derived."
        )
        review_marks = _latest_observation_review_map(ctx)
        st.dataframe(_display_observations_frame(observations, review_marks), use_container_width=True, hide_index=True)
        _render_observation_review_form(ctx, observations, review_marks)

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
        "Largest advance in case window %": _round_or_none(row.get("max_gain_pct")),
        "Largest pullback in case window %": _round_or_none(row.get("max_drawdown_pct")),
        "RS near start": _round_or_none(row.get("rs_percentile_start")),
        "Pattern flags score": _round_or_none(row.get("hindsight_score")),
        "Data quality": row.get("data_quality"),
        "Notes": row.get("notes"),
    }


def _display_events_frame(frame: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Symbol": row.get("symbol"),
                "Event type": _friendly_text(row.get("event_type")),
                "Event date": row.get("event_date"),
                "Market session": _friendly_text(row.get("market_session")),
                "First tradable date": row.get("first_tradable_date") or "Unresolved",
                "Timing status": row.get("timing_status"),
                "Evidence type": _friendly_text(row.get("evidence_type")),
                "Source quality": _friendly_text(row.get("source_quality")),
                "Evidence summary": row.get("evidence_summary"),
                "Needs review": bool(row.get("requires_review")),
            }
            for _, row in frame.iterrows()
        ]
    )


def _display_gates_frame(frame: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Symbol": row.get("symbol"),
                "Gate lane": _friendly_text(row.get("gate_group")),
                "Gate": row.get("gate_name"),
                "Status": row.get("gate_status"),
                "Computed value": row.get("computed_value"),
                "Threshold": row.get("threshold"),
                "Rows": f"{row.get('available_rows')}/{row.get('required_rows')}",
                "Reason": row.get("reason"),
                "Missing detail": row.get("missing_detail") or "-",
                "Formula": row.get("formula"),
                "Data used": row.get("data_used"),
            }
            for _, row in frame.iterrows()
        ]
    )


def _display_results_frame(frame: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame([_display_result(row.to_dict()) for _, row in frame.iterrows()])


def _display_observations_frame(frame: pd.DataFrame, review_marks: dict[str, dict]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Symbol": row.get("symbol"),
                "Pattern lane": _friendly_observation_group(row.get("observation_group")),
                "Observation": row.get("pattern_name"),
                "Observed value": row.get("observation_value"),
                "Review status": _friendly_observation_status(row.get("status")),
                "Latest user review": _friendly_user_review(
                    review_marks.get(str(row.get("observation_id")), {}).get("review_status")
                ),
                "Needs review": bool(row.get("requires_review")),
                "Evidence": row.get("evidence"),
                "Source": row.get("source"),
            }
            for _, row in frame.iterrows()
        ]
    )


def _render_observation_review_form(
    ctx: UIContext,
    observations: pd.DataFrame,
    review_marks: dict[str, dict],
) -> None:
    reviewable = observations[observations["requires_review"] == True]  # noqa: E712
    if reviewable.empty:
        return
    st.subheader("Review a Pattern Observation")
    labels = [
        f"{row['symbol']} / {_friendly_observation_group(row['observation_group'])} / {row['pattern_name']}"
        for _, row in reviewable.iterrows()
    ]
    selected_label = st.selectbox("Observation", labels)
    selected = reviewable.iloc[labels.index(selected_label)].to_dict()
    object_id = str(selected["observation_id"])
    latest_mark = review_marks.get(object_id)
    if latest_mark:
        st.caption(
            f"Latest user review: {_friendly_user_review(latest_mark.get('review_status'))}; "
            f"follow-up: {latest_mark.get('follow_up_date') or '-'}"
        )
    st.write(f"Evidence: {selected.get('evidence')}")
    with st.form("hindsight_observation_review_form"):
        review_status = st.selectbox(
            "Review decision",
            [
                "needs_more_data",
                "confirmed_hypothesis",
                "rejected_hypothesis",
                "unclear",
                "not_applicable",
            ],
        )
        notes = st.text_area(
            "Review note",
            placeholder="Why this should or should not become a future research hypothesis.",
        )
        plan = st.text_area(
            "Research plan",
            placeholder="What data or source would prove this was visible at the time?",
        )
        follow_up = st.text_input("Follow-up date", placeholder="YYYY-MM-DD")
        submitted = st.form_submit_button("Save observation review")
    if submitted:
        parsed_follow_up = _parse_optional_date(follow_up)
        if parsed_follow_up is False:
            st.error("Follow-up date must use YYYY-MM-DD.")
            return
        review_id = insert_review_mark(
            ctx.config,
            object_type="hindsight_pattern_observation",
            object_id=object_id,
            review_status=review_status,
            notes=notes or None,
            personal_plan=plan or None,
            follow_up_date=parsed_follow_up,
        )
        st.success(f"Saved pattern observation review {review_id}.")
        st.rerun()


def _friendly_observation_group(value: object) -> str:
    return {
        "industry": "Industry / theme",
        "technical": "Technical / OHLCV",
        "manual_or_llm_required": "Manual or LLM review",
    }.get(str(value or ""), str(value or "-").replace("_", " ").title())


def _friendly_observation_status(value: object) -> str:
    return {
        "hypothesis_seed": "Hypothesis seed",
        "observed_hypothesis_feature": "Observed in case window",
        "not_observed_in_case_window": "Not observed in case window",
        "needs_historical_data": "Needs historical data",
        "needs_manual_review": "Needs manual review",
        "outcome_only_not_predictive": "Outcome descriptor only",
    }.get(str(value or ""), str(value or "-").replace("_", " ").title())


def _friendly_user_review(value: object) -> str:
    return {
        "confirmed_hypothesis": "Confirmed hypothesis",
        "rejected_hypothesis": "Rejected hypothesis",
        "needs_more_data": "Needs more data",
        "unclear": "Unclear",
        "not_applicable": "Not applicable",
    }.get(str(value or ""), "Not reviewed")


def _friendly_text(value: object) -> str:
    return str(value or "-").replace("_", " ").title()


def _latest_observation_review_map(ctx: UIContext) -> dict[str, dict]:
    if not table_exists(ctx.config, "intel_review_marks"):
        return {}
    with connect_database(ctx.config.database.path) as connection:
        rows = connection.execute(
            """
            SELECT object_id, review_status, notes, personal_plan, follow_up_date, reviewed_at
            FROM intel_review_marks
            WHERE object_type = 'hindsight_pattern_observation'
            QUALIFY row_number() OVER (
                PARTITION BY object_id
                ORDER BY reviewed_at DESC
            ) = 1
            """
        ).fetchdf()
    return {str(row["object_id"]): row.to_dict() for _, row in rows.iterrows()}


def _parse_optional_date(value: str) -> str | None | bool:
    if not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        return False


def _round_or_none(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), 2)
