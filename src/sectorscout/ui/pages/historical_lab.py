from __future__ import annotations

import json
from datetime import date
from html import escape

import pandas as pd
import streamlit as st

from sectorscout.db import connect_database
from sectorscout.hindsight import (
    build_hindsight_hypothesis_registry,
    build_hindsight_observation_links,
    build_hindsight_replay_gates,
    fetch_hindsight_prices,
    historical_pattern_summary,
    latest_hindsight_evidence,
    latest_hindsight_events,
    latest_hindsight_hypotheses,
    latest_hindsight_hypothesis_case_results,
    latest_hindsight_observation_links,
    load_hindsight_cases,
    latest_hindsight_pattern_observations,
    latest_hindsight_replay_gates,
    latest_hindsight_results,
    scan_hindsight_cases,
    seed_hindsight_evidence,
    seed_hindsight_events,
    seed_hindsight_cases,
)
from sectorscout.hindsight_playbook import generate_hindsight_pattern_playbook
from sectorscout.hindsight_workflow import run_hindsight_refresh
from sectorscout.intel.storage import insert_review_mark
from sectorscout.ui.data import UIContext, row_count, table_exists
from sectorscout.ui.hindsight_presenter import (
    build_case_story_cards,
    build_case_readiness_rows,
    build_case_pattern_map_rows,
    build_gate_review_rows,
    build_hindsight_readout_summary_cards,
    build_methodology_guardrail_rows,
    build_pattern_story_cards,
    build_pattern_insight_rows,
    build_status_summary_rows,
)


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
    "hindsight_evidence_items",
    "hindsight_replay_gates",
    "hindsight_observation_links",
    "hindsight_hypotheses",
    "hindsight_hypothesis_case_results",
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
        "Default official event seeds resolve known first-tradable sessions; date-only custom events stay blocked."
    )
    _render_lab_refresh(ctx)
    st.subheader("Methodology guardrails")
    st.dataframe(build_methodology_guardrail_rows(), use_container_width=True, hide_index=True)

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
    st.caption(
        "Case roles separate anchor leaders, downstream nodes, peer controls, and negative controls. "
        "Control DATA GAP means the control is not yet evaluable under PIT rules; it is not treated as a failed setup."
    )
    action_cols = st.columns([1, 1, 2])
    if action_cols[0].button("Seed cases", use_container_width=True):
        count = seed_hindsight_cases(ctx.config)
        st.success(f"Seeded {count} hindsight cases.")
    if action_cols[1].button("Fetch public prices", use_container_width=True):
        fetched = fetch_hindsight_prices(ctx.config)
        case_count = sum(1 for row in fetched if row.get("symbol_type") == "case")
        benchmark_count = sum(1 for row in fetched if row.get("symbol_type") == "benchmark")
        st.success(
            f"Fetched public price history for {case_count} cases and {benchmark_count} fixed benchmarks."
        )
        st.dataframe(fetched, use_container_width=True, hide_index=True)
    if action_cols[2].button("Run scan", use_container_width=True):
        results = scan_hindsight_cases(ctx.config)
        st.success(f"Scanned {len(results)} hindsight cases.")
        st.dataframe([_display_result(result.to_dict()) for result in results], use_container_width=True, hide_index=True)
    st.caption(
        "Case-study diagnostics only. Price fetch includes a pre-event lookback window and fixed benchmark "
        "symbols so technical gates can inspect what was visible before the case anchor."
    )

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

    st.subheader("Evidence Ledger")
    if st.button("Seed evidence ledger", use_container_width=True):
        count = seed_hindsight_evidence(ctx.config)
        st.success(f"Seeded {count} evidence rows.")
    evidence = latest_hindsight_evidence(ctx.config)
    if evidence.empty:
        st.info("No evidence ledger rows yet. Seed evidence after the event ledger exists.")
    else:
        st.caption(
            "Industry and fundamental claims must have source, timestamp, and replay usability before they support a pattern."
        )
        st.dataframe(_display_evidence_frame(evidence), use_container_width=True, hide_index=True)

    st.subheader("Gate Explain Panel")
    gates = latest_hindsight_replay_gates(ctx.config)
    if gates.empty:
        st.info("No gate explain rows yet. Build gates after seeding the event ledger.")
    else:
        st.caption("DATA GAP is not a failure. It means the required evidence or price rows are not available yet.")
        _render_hindsight_review_board(events, gates)
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
    _render_hypothesis_registry(ctx)

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
        _render_observation_link_panel(ctx)
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


def _render_hindsight_review_board(events: pd.DataFrame, gates: pd.DataFrame) -> None:
    st.markdown("#### Case Readiness")
    st.write(
        "This board translates the audit gates into plain review states: what can be interpreted now, "
        "what is blocked, and what data should be fixed next."
    )
    readiness_rows = build_case_readiness_rows(events, gates)
    if readiness_rows:
        st.dataframe(readiness_rows, use_container_width=True, hide_index=True)

    status_rows = build_status_summary_rows(gates)
    if status_rows:
        st.markdown("#### Status Guide")
        st.dataframe(status_rows, use_container_width=True, hide_index=True)

    review_rows = build_gate_review_rows(gates)
    if not review_rows:
        return
    st.markdown("#### Gate Review Guide")
    symbols = ["All"] + sorted({str(row["Symbol"]) for row in review_rows})
    selected_symbol = st.selectbox("Gate guide symbol", symbols)
    filtered_rows = (
        review_rows
        if selected_symbol == "All"
        else [row for row in review_rows if str(row["Symbol"]) == selected_symbol]
    )
    st.dataframe(filtered_rows, use_container_width=True, hide_index=True)


def _render_lab_refresh(ctx: UIContext) -> None:
    with st.expander("Refresh lab + playbook", expanded=True):
        st.write(
            "Runs the full historical lab pipeline: cases, event ledger, evidence ledger, optional public price "
            "refresh, case scan, replay gates, observation links, hypothesis registry, and Markdown playbook export."
        )
        st.info(
            "Case-study research refresh only. This does not modify SectorScout base scores or report validation results."
        )
        controls = st.columns([1, 1, 1, 1])
        fetch_prices = controls[0].checkbox("Fetch public prices", value=True)
        include_benchmarks = controls[1].checkbox("Include fixed benchmarks", value=True)
        lookback_days = controls[2].number_input("Lookback days", min_value=0, max_value=1200, value=320, step=20)
        refresh_asof = controls[3].date_input("Refresh as-of", value=date.today())
        if st.button("Run full historical refresh", use_container_width=True):
            with st.spinner("Refreshing historical lab artifacts..."):
                result = run_hindsight_refresh(
                    ctx.config,
                    asof_date=refresh_asof,
                    fetch_prices=fetch_prices,
                    include_benchmarks=include_benchmarks,
                    lookback_days=int(lookback_days),
                )
            st.success(f"Refresh complete. Playbook: {result.playbook_path}")
            st.dataframe([step.to_dict() for step in result.steps], use_container_width=True, hide_index=True)


def _render_observation_link_panel(ctx: UIContext) -> None:
    st.markdown("#### Observation Evidence Links")
    if st.button("Build observation links", use_container_width=True):
        links = build_hindsight_observation_links(ctx.config)
        st.success(f"Built {len(links)} observation links.")
    links = latest_hindsight_observation_links(ctx.config)
    if links.empty:
        st.info("No observation links yet. Build links after observations, evidence, and gates exist.")
        return
    st.caption(
        "Each observation must point to official evidence, computed gates, or an explicit review-required blocker."
    )
    summary = (
        links.groupby(["link_type", "link_status"], dropna=False)
        .size()
        .reset_index(name="count")
        .sort_values(["link_type", "link_status"])
    )
    st.dataframe(_display_observation_link_summary(summary), use_container_width=True, hide_index=True)
    st.dataframe(_display_observation_links_frame(links), use_container_width=True, hide_index=True)


def _render_hypothesis_registry(ctx: UIContext) -> None:
    st.subheader("Replay Hypothesis Registry")
    st.write(
        "This matrix turns individual observations into cross-case hypotheses. It does not confirm a rule; "
        "it shows which cases support, block, or still lack evidence for a future replay design."
    )
    st.info(
        "Controls are displayed in the matrix to expose winner-only selection bias. They do not count as failed "
        "patterns unless the same required evidence and gates are loaded and evaluated."
    )
    if st.button("Build replay hypothesis registry", use_container_width=True):
        hypotheses, case_results = build_hindsight_hypothesis_registry(ctx.config)
        st.success(f"Built {len(hypotheses)} hypotheses and {len(case_results)} case results.")
    hypotheses = latest_hindsight_hypotheses(ctx.config)
    case_results = latest_hindsight_hypothesis_case_results(ctx.config)
    if hypotheses.empty or case_results.empty:
        st.info("No replay hypothesis registry yet. Run a scan, build observation links, then build hypotheses.")
        return
    st.caption(
        "Counts are derived from per-case rows. One case counts once even if it has multiple evidence links."
    )
    events = latest_hindsight_events(ctx.config)
    evidence = latest_hindsight_evidence(ctx.config)
    gates = latest_hindsight_replay_gates(ctx.config)
    st.markdown("#### Pattern Readout")
    st.write(
        "This is the plain-language layer: what the current historical cases suggest, what remains blocked, "
        "and where controls warn that the mechanism may be too broad."
    )
    insight_rows = build_pattern_insight_rows(hypotheses, case_results)
    pattern_map_rows = build_case_pattern_map_rows(events, evidence, gates, hypotheses, case_results)
    summary_cards = build_hindsight_readout_summary_cards(insight_rows, pattern_map_rows)
    if summary_cards:
        _render_summary_cards(summary_cards)
    if insight_rows:
        _render_pattern_cards(build_pattern_story_cards(insight_rows))
        st.dataframe(insight_rows, use_container_width=True, hide_index=True)
    st.markdown("#### Industry + Technical Map")
    st.write(
        "This separates industry evidence from pre-event technical gates for each leader and control case. "
        "A case only teaches a composite pattern when both lanes are auditable."
    )
    if pattern_map_rows:
        _render_case_cards(build_case_story_cards(pattern_map_rows))
        st.dataframe(pattern_map_rows, use_container_width=True, hide_index=True)
    if st.button("Generate pattern playbook", use_container_width=True):
        path = generate_hindsight_pattern_playbook(ctx.config)
        st.success(f"Generated playbook: {path}")
    st.dataframe(_display_hypotheses_frame(hypotheses, case_results), use_container_width=True, hide_index=True)
    st.markdown("#### Case Matrix")
    st.dataframe(_display_hypothesis_case_matrix(hypotheses, case_results), use_container_width=True, hide_index=True)
    st.markdown("#### Case Result Detail")
    st.dataframe(_display_hypothesis_case_results(case_results), use_container_width=True, hide_index=True)


def _render_summary_cards(cards: list[dict[str, str]]) -> None:
    html = ['<div class="ss-hindsight-summary">']
    for card in cards:
        tone = _safe_tone(card.get("Tone"))
        html.append(
            f"""
            <div class="ss-research-card ss-tone-{tone}">
              <div class="ss-card-kicker">{escape(str(card.get("Label") or ""))}</div>
              <div class="ss-card-value">{escape(str(card.get("Value") or "-"))}</div>
              <div class="ss-card-body">{escape(str(card.get("Detail") or ""))}</div>
            </div>
            """
        )
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def _render_pattern_cards(cards: list[dict[str, str]]) -> None:
    if not cards:
        return
    html = ['<div class="ss-research-card-grid">']
    for card in cards:
        tone = _safe_tone(card.get("Tone"))
        html.append(
            f"""
            <div class="ss-research-card ss-tone-{tone}">
              <div class="ss-card-kicker">{escape(str(card.get("Lane") or ""))}</div>
              <div class="ss-card-title">{escape(str(card.get("Title") or ""))}</div>
              <div class="ss-card-body">{escape(str(card.get("Status") or ""))}</div>
              {_card_line("Support", card.get("Support"))}
              {_card_line("Gaps", card.get("Gaps"))}
              {_card_line("Control", card.get("Control"))}
              {_card_line("Takeaway", card.get("Takeaway"))}
              {_card_line("Next", card.get("Next"))}
            </div>
            """
        )
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def _render_case_cards(cards: list[dict[str, str]]) -> None:
    if not cards:
        return
    html = ['<div class="ss-case-card-grid">']
    for card in cards:
        tone = _safe_tone(card.get("Tone"))
        html.append(
            f"""
            <div class="ss-research-card ss-tone-{tone}">
              <div class="ss-card-kicker">{escape(str(card.get("Role") or ""))}</div>
              <div class="ss-card-title">{escape(str(card.get("Symbol") or ""))}</div>
              <div class="ss-card-body">{escape(str(card.get("Read") or ""))}</div>
              {_card_line("Industry", card.get("Industry"))}
              {_card_line("Technical", card.get("Technical"))}
              {_card_line("Timing", card.get("Timing"))}
              {_card_line("Next", card.get("Next"))}
            </div>
            """
        )
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def _card_line(label: str, value: object) -> str:
    return (
        '<div class="ss-card-line">'
        f"<span>{escape(label)}</span>"
        f"<span>{escape(str(value or '-'))}</span>"
        "</div>"
    )


def _safe_tone(value: object) -> str:
    text = str(value or "").lower()
    return text if text in {"green", "blue", "amber", "red", "purple"} else "blue"


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
        "hindsight_case_studies": "Historical leader, peer-control, and negative-control case definitions.",
        "hindsight_hypotheses": "Candidate replay mechanisms, not confirmed patterns.",
        "hindsight_hypothesis_case_results": "Per-case hypothesis support, blocker, and data-gap matrix.",
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


def _display_evidence_frame(frame: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Symbol": row.get("symbol"),
                "Evidence lane": _friendly_text(row.get("evidence_lane")),
                "Evidence kind": _friendly_text(row.get("evidence_kind")),
                "Claim": row.get("claim"),
                "Metric": _format_metric(row),
                "Status": _friendly_text(row.get("evidence_status")),
                "Usable in replay": bool(row.get("usable_in_replay")),
                "Available at": row.get("available_at_utc") or "Missing",
                "Replay decision at": row.get("replay_decision_at") or "Unresolved",
                "Source quality": _friendly_text(row.get("source_quality")),
                "Needs review": bool(row.get("requires_review")),
                "Review note": row.get("review_note"),
            }
            for _, row in frame.iterrows()
        ]
    )


def _display_hypotheses_frame(hypotheses: pd.DataFrame, case_results: pd.DataFrame) -> pd.DataFrame:
    counts = (
        case_results.groupby(["hypothesis_id", "result_status"], dropna=False)
        .size()
        .reset_index(name="count")
    )
    count_map = {
        (str(row["hypothesis_id"]), str(row["result_status"])): int(row["count"])
        for _, row in counts.iterrows()
    }
    return pd.DataFrame(
        [
            {
                "Hypothesis": row.get("hypothesis_name"),
                "Group": _friendly_text(row.get("hypothesis_group")),
                "Replay readiness": _friendly_text(row.get("promotion_status")),
                "Supports": count_map.get((str(row.get("hypothesis_id")), "SUPPORTS"), 0),
                "Blocks": count_map.get((str(row.get("hypothesis_id")), "BLOCKS"), 0),
                "Data gaps": count_map.get((str(row.get("hypothesis_id")), "DATA_GAP"), 0)
                + count_map.get((str(row.get("hypothesis_id")), "TIMING_GAP"), 0),
                "Needs review": count_map.get((str(row.get("hypothesis_id")), "REQUIRES_REVIEW"), 0),
                "Mechanism": row.get("mechanism"),
                "Minimum next evidence": row.get("minimum_next_evidence"),
                "Anti-hindsight note": row.get("anti_hindsight_notes"),
            }
            for _, row in hypotheses.iterrows()
        ]
    )


def _display_hypothesis_case_matrix(hypotheses: pd.DataFrame, case_results: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, hypothesis in hypotheses.iterrows():
        hypothesis_id = str(hypothesis.get("hypothesis_id"))
        result_rows = case_results[case_results["hypothesis_id"].astype(str) == hypothesis_id]
        row = {
            "Hypothesis": hypothesis.get("hypothesis_name"),
            "Replay readiness": _friendly_text(hypothesis.get("promotion_status")),
        }
        for _, result in result_rows.iterrows():
            row[str(result.get("symbol"))] = (
                f"{_friendly_text(result.get('result_status'))} ({_friendly_text(result.get('case_role'))})"
            )
        rows.append(row)
    return pd.DataFrame(rows)


def _display_hypothesis_case_results(case_results: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Hypothesis id": row.get("hypothesis_id"),
                "Symbol": row.get("symbol"),
                "Role": _friendly_text(row.get("case_role")),
                "Status": _friendly_text(row.get("result_status")),
                "Reason": row.get("reason_text"),
                "Control interpretation": _control_interpretation(row),
                "Evidence links": _json_count(row.get("linked_evidence_ids_json")),
                "Gate links": _json_count(row.get("linked_gate_ids_json")),
                "Observation links": _json_count(row.get("linked_observation_ids_json")),
            }
            for _, row in case_results.iterrows()
        ]
    )


def _control_interpretation(row: pd.Series) -> str:
    role = str(row.get("case_role") or "")
    status = str(row.get("result_status") or "")
    if role in {"negative_control", "peer_control"} and status in {"DATA_GAP", "TIMING_GAP", "REQUIRES_REVIEW"}:
        return "Control is not evaluable yet; do not treat as failure."
    if role in {"negative_control", "peer_control"} and status == "SUPPORTS":
        return "Control supports under same PIT rules; review for false-positive risk."
    return "-"


def _display_observation_links_frame(frame: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Symbol": row.get("symbol"),
                "Link type": _friendly_text(row.get("link_type")),
                "Role": _friendly_text(row.get("link_role")),
                "Status": _friendly_text(row.get("link_status")),
                "Reason": row.get("reason"),
                "Linked table": row.get("linked_table"),
                "Linked id": row.get("linked_id"),
                "Observation id": row.get("observation_id"),
            }
            for _, row in frame.iterrows()
        ]
    )


def _display_observation_link_summary(frame: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Link type": _friendly_text(row.get("link_type")),
                "Status": _friendly_text(row.get("link_status")),
                "Count": int(row.get("count") or 0),
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


def _format_metric(row: pd.Series) -> str:
    name = str(row.get("metric_name") or "").strip()
    value = str(row.get("metric_value") or "").strip()
    period = str(row.get("metric_period") or "").strip()
    parts = [part for part in [name, value, period] if part]
    return " / ".join(parts) if parts else "-"


def _json_count(value: object) -> int:
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return 0
    return len(parsed) if isinstance(parsed, list) else 0


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
