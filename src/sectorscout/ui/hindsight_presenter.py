from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

import pandas as pd


STATUS_ORDER = ["DATA_GAP", "FAIL", "PENDING", "PASS", "N/A"]


def build_methodology_guardrail_rows() -> list[dict[str, str]]:
    return [
        {
            "Principle": "Case studies create mechanisms, not proof",
            "How SectorScout applies it": (
                "Historical leaders are used to define industry and technical hypotheses, then each case "
                "must show what was knowable before the replay point."
            ),
            "Reviewer check": "Ask what population this case is a case of, and whether the mechanism is too broad.",
        },
        {
            "Principle": "Controls expose winner-only bias",
            "How SectorScout applies it": (
                "Peer and negative controls sit in the same matrix as leaders. A control that supports the same "
                "hypothesis forces a false-positive review."
            ),
            "Reviewer check": "Do not treat a control DATA GAP as failure; evaluate it under the same PIT rules.",
        },
        {
            "Principle": "Backtest hygiene starts before validation",
            "How SectorScout applies it": (
                "The lab keeps look-ahead, survivorship, benchmark choice, and data coverage visible before any "
                "formal validation module exists."
            ),
            "Reviewer check": "Do not promote a pattern while benchmark, event timing, or pre-event price coverage is missing.",
        },
        {
            "Principle": "Model risk requires purpose and limits",
            "How SectorScout applies it": (
                "Every candidate mechanism has required evidence, required gates, anti-hindsight notes, and a next "
                "evidence requirement."
            ),
            "Reviewer check": "Keep research hypotheses separate from score logic and daily workflow decisions.",
        },
    ]


def build_pattern_insight_rows(hypotheses: pd.DataFrame, case_results: pd.DataFrame) -> list[dict[str, Any]]:
    if hypotheses.empty or case_results.empty:
        return []
    rows: list[dict[str, Any]] = []
    for _, hypothesis in _sort_hypotheses_for_readout(hypotheses).iterrows():
        hypothesis_id = str(hypothesis.get("hypothesis_id") or "")
        result_rows = case_results[case_results["hypothesis_id"].astype(str) == hypothesis_id]
        rows.append(
            {
                "Pattern candidate": hypothesis.get("hypothesis_name"),
                "Current read": _promotion_read(hypothesis.get("promotion_status")),
                "Leader support": _symbols_for_status(result_rows, "SUPPORTS", include_controls=False),
                "Blocked leaders": _symbols_for_status(result_rows, "BLOCKS", include_controls=False),
                "Leader data gaps": _symbols_for_status(
                    result_rows,
                    "DATA_GAP",
                    include_controls=False,
                    extra_statuses={"TIMING_GAP", "REQUIRES_REVIEW"},
                ),
                "Control check": _control_check_text(result_rows),
                "What this teaches": _pattern_teaching_text(hypothesis, result_rows),
                "Next research action": hypothesis.get("minimum_next_evidence"),
                "Replay boundary": hypothesis.get("anti_hindsight_notes"),
            }
        )
    return rows


def build_case_pattern_map_rows(
    events: pd.DataFrame,
    evidence: pd.DataFrame,
    gates: pd.DataFrame,
    hypotheses: pd.DataFrame,
    case_results: pd.DataFrame,
) -> list[dict[str, Any]]:
    symbols = _ordered_symbols(events, evidence, gates, case_results)
    if not symbols:
        return []
    hypothesis_names = _hypothesis_name_map(hypotheses)
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        symbol_gates = _symbol_rows(gates, "symbol", symbol)
        symbol_results = _symbol_rows(case_results, "symbol", symbol)
        h2_status = _hypothesis_case_status(symbol_results, "H2", hypothesis_names)
        h3_status = _hypothesis_case_status(symbol_results, "H3", hypothesis_names)
        h4_status = _hypothesis_case_status(symbol_results, "H4", hypothesis_names)
        rows.append(
            {
                "Symbol": symbol,
                "Role": friendly_lane(_case_role_from_results(symbol_results)),
                "Industry evidence": _industry_evidence_label(evidence, symbol),
                "Event timing": friendly_status(_lane_status(symbol_gates, "event_timing")),
                "Stage 2": friendly_status(_gate_status(symbol_gates, "Stage 2 trend explain")),
                "Benchmark RS": friendly_status(_gate_status_prefix(symbol_gates, "Benchmark RS vs")),
                "First session data": friendly_status(_lane_status(symbol_gates, "first_tradable")),
                "Industry hypothesis": friendly_status(h2_status),
                "Industry + technical": friendly_status(h3_status),
                "Mixed analog": friendly_status(h4_status),
                "Plain-English read": _case_pattern_read(
                    symbol_results,
                    evidence,
                    symbol_gates,
                    symbol,
                    hypothesis_names,
                ),
                "Next data task": _case_pattern_next_task(symbol_results, evidence, symbol_gates, symbol),
            }
        )
    return rows


def build_case_readiness_rows(events: pd.DataFrame, gates: pd.DataFrame) -> list[dict[str, Any]]:
    if events.empty:
        return []
    gates_by_symbol: dict[str, pd.DataFrame] = {}
    if not gates.empty:
        for symbol, group in gates.groupby("symbol"):
            gates_by_symbol[str(symbol)] = group

    rows: list[dict[str, Any]] = []
    for _, event in events.iterrows():
        symbol = str(event.get("symbol") or "")
        symbol_gates = gates_by_symbol.get(symbol, pd.DataFrame())
        timing = _lane_status(symbol_gates, "event_timing")
        pre_event = _lane_status(symbol_gates, "pre_event")
        first_tradable = _lane_status(symbol_gates, "first_tradable")
        status_counts = Counter(str(value) for value in symbol_gates.get("gate_status", []))
        rows.append(
            {
                "Symbol": symbol,
                "Case": event.get("label"),
                "Event timing": friendly_status(timing),
                "Pre-event setup": friendly_status(pre_event),
                "First-tradable data": friendly_status(first_tradable),
                "Data gaps": int(status_counts.get("DATA_GAP", 0)),
                "Failed gates": int(status_counts.get("FAIL", 0)),
                "Ready to interpret": _readiness_label(timing, pre_event, first_tradable),
                "Next action": _case_next_action(timing, pre_event, first_tradable, int(status_counts.get("DATA_GAP", 0))),
                "Blocked conclusion": _case_blocked_conclusion(timing, pre_event, first_tradable),
            }
        )
    return rows


def build_gate_review_rows(gates: pd.DataFrame) -> list[dict[str, Any]]:
    if gates.empty:
        return []
    rows: list[dict[str, Any]] = []
    for _, gate in gates.iterrows():
        status = str(gate.get("gate_status") or "")
        gate_name = str(gate.get("gate_name") or "")
        rows.append(
            {
                "Symbol": gate.get("symbol"),
                "Lane": friendly_lane(gate.get("gate_group")),
                "Question": gate_question(gate_name),
                "Status": friendly_status(status),
                "Meaning": status_meaning(status),
                "Next action": gate_next_action(gate),
                "Blocked conclusion": gate_blocked_conclusion(gate),
                "Audit detail": str(gate.get("reason") or ""),
                "Inputs": str(gate.get("data_used") or ""),
                "Rows": f"{gate.get('available_rows')}/{gate.get('required_rows')}",
                "Computed value": gate.get("computed_value"),
                "Threshold": gate.get("threshold"),
            }
        )
    return rows


def build_status_summary_rows(gates: pd.DataFrame) -> list[dict[str, str]]:
    if gates.empty:
        return []
    counts = Counter(str(value) for value in gates["gate_status"].tolist())
    return [
        {
            "Status": friendly_status(status),
            "Count": str(counts.get(status, 0)),
            "Meaning": status_meaning(status),
            "Reviewer rule": status_reviewer_rule(status),
        }
        for status in STATUS_ORDER
        if counts.get(status, 0)
    ]


def gate_question(gate_name: str) -> str:
    if gate_name == "Event timestamp available":
        return "Do we know when the event became public?"
    if gate_name == "First tradable date resolved":
        return "Do we know the first regular session that could reflect the event?"
    if gate_name == "Pre-event price coverage":
        return "Do we have enough prior price history to inspect the setup?"
    if gate_name == "Stage 2 trend explain":
        return "Was the pre-event trend regime testable and supportive?"
    if gate_name.startswith("Benchmark RS vs"):
        return "Can we compare the symbol to its fixed benchmark without fallback?"
    if gate_name == "First-tradable price row":
        return "Do we have the first regular-session price bar?"
    return gate_name.replace("_", " ").strip() or "What did this gate test?"


def gate_next_action(gate: pd.Series | dict[str, Any]) -> str:
    status = str(gate.get("gate_status") or "")
    gate_name = str(gate.get("gate_name") or "")
    missing = str(gate.get("missing_detail") or "")
    if status == "PASS":
        return "Keep this as auditable support and inspect the next lane."
    if status == "FAIL":
        return "Keep it as counter-evidence; do not force the case to fit the pattern."
    if status == "PENDING":
        return "Wait for the review window to complete before assigning support."
    if status == "N/A":
        return "No action needed for this case."
    if gate_name == "Event timestamp available":
        return "Attach an official timestamped source or keep event-reaction claims blocked."
    if gate_name == "First tradable date resolved":
        return "Resolve the first regular tradable session from event timing and market calendar."
    if gate_name in {"Pre-event price coverage", "Stage 2 trend explain"}:
        return "Load or verify enough pre-event OHLCV history before interpreting technical setup."
    if gate_name.startswith("Benchmark RS vs"):
        return "Load the fixed primary benchmark history; do not switch to an easier benchmark."
    if gate_name == "First-tradable price row":
        return "Load the first regular-session OHLCV row for this symbol."
    return missing or "Fill the missing evidence before using this gate."


def gate_blocked_conclusion(gate: pd.Series | dict[str, Any]) -> str:
    status = str(gate.get("gate_status") or "")
    gate_group = str(gate.get("gate_group") or "")
    gate_name = str(gate.get("gate_name") or "")
    if status == "PASS":
        return "-"
    if status == "FAIL":
        return "This gate cannot support the pattern for this case."
    if status == "PENDING":
        return "Post-window validation is not knowable yet."
    if gate_group == "event_timing":
        return "Event reaction and first-tradable interpretation are blocked."
    if gate_name.startswith("Benchmark RS vs"):
        return "Relative-strength claims are blocked."
    if gate_group == "pre_event":
        return "Pre-event technical setup claims are blocked."
    if gate_group == "first_tradable":
        return "First-tradable confirmation is blocked."
    return "Pattern support is blocked until this gap is resolved."


def friendly_lane(value: object) -> str:
    return {
        "anchor": "Anchor leader",
        "downstream_leader": "Downstream leader",
        "peer_control": "Peer control",
        "negative_control": "Negative control",
        "event_timing": "Event timing",
        "pre_event": "Pre-event setup",
        "first_tradable": "First-tradable confirmation",
        "post_event": "Post-event validation",
    }.get(str(value or ""), str(value or "-").replace("_", " ").title())


def friendly_status(value: object) -> str:
    status = str(value or "").upper()
    return {
        "PASS": "PASS - evidence supports the gate",
        "FAIL": "FAIL - evidence does not support it",
        "DATA_GAP": "DATA GAP - missing required evidence",
        "PENDING": "PENDING - review window incomplete",
        "N/A": "N/A - not applicable",
        "NO_GATES": "NO GATES - run gate builder",
    }.get(status, str(value or "-").replace("_", " ").title())


def status_meaning(value: object) -> str:
    status = str(value or "").upper()
    return {
        "PASS": "Required evidence exists and the rule condition is met.",
        "FAIL": "Required evidence exists and the rule condition is not met.",
        "DATA_GAP": "This is not a failed pattern; the required timestamp, price row, or benchmark row is missing.",
        "PENDING": "The future review window has not completed, so the result is not knowable yet.",
        "N/A": "The gate does not apply to this case.",
        "NO_GATES": "No gate rows exist yet for this case.",
    }.get(status, "Unrecognized status. Review the raw audit row.")


def status_reviewer_rule(value: object) -> str:
    status = str(value or "").upper()
    return {
        "PASS": "May be used as one piece of case evidence, with timestamp and source visible.",
        "FAIL": "Treat as counter-evidence; do not reinterpret it away.",
        "DATA_GAP": "Do not count as support or rejection; fill the missing data first.",
        "PENDING": "Keep out of confirmed pattern review until the window completes.",
        "N/A": "Ignore for this case.",
    }.get(status, "Escalate for manual review.")


def _lane_status(gates: pd.DataFrame, lane: str) -> str:
    if gates.empty or "gate_group" not in gates:
        return "NO_GATES"
    lane_rows = gates[gates["gate_group"] == lane]
    if lane_rows.empty:
        return "NO_GATES"
    statuses = [str(value) for value in lane_rows["gate_status"].tolist()]
    for status in STATUS_ORDER:
        if status in statuses:
            return status
    return statuses[0] if statuses else "NO_GATES"


def _readiness_label(timing: str, pre_event: str, first_tradable: str) -> str:
    if timing == "DATA_GAP":
        return "Blocked: event timing unresolved"
    if pre_event == "DATA_GAP":
        return "Blocked: pre-event setup incomplete"
    if first_tradable == "DATA_GAP":
        return "Partial: first-tradable data missing"
    if "FAIL" in {timing, pre_event, first_tradable}:
        return "Readable with counter-evidence"
    if timing == "PASS" and pre_event == "PASS" and first_tradable == "PASS":
        return "Readable case study"
    return "Needs review"


def _case_next_action(timing: str, pre_event: str, first_tradable: str, data_gaps: int) -> str:
    if timing == "DATA_GAP":
        return "Resolve official event timestamp and first tradable date."
    if pre_event == "DATA_GAP":
        return "Load pre-event OHLCV and fixed benchmark history."
    if first_tradable == "DATA_GAP":
        return "Load first-tradable session price row."
    if data_gaps:
        return "Open gate details and resolve remaining missing rows."
    if "FAIL" in {timing, pre_event, first_tradable}:
        return "Review failed gates as possible anti-pattern evidence."
    return "Compare industry evidence against technical gates; keep outcome separate."


def _case_blocked_conclusion(timing: str, pre_event: str, first_tradable: str) -> str:
    blocked = []
    if timing == "DATA_GAP":
        blocked.append("event reaction")
    if pre_event == "DATA_GAP":
        blocked.append("pre-event technical setup")
    if first_tradable == "DATA_GAP":
        blocked.append("first-tradable confirmation")
    if blocked:
        return "Blocked: " + ", ".join(blocked)
    return "-"


def _promotion_read(value: object) -> str:
    status = str(value or "")
    return {
        "ELIGIBLE_FOR_REPLAY_DESIGN": "Candidate can be translated into a replay rule, then validated separately.",
        "PARTIAL_SUPPORT_TIMING_GAP": "Promising but timing or availability still blocks part of the evidence.",
        "TIMING_GAP_REVIEW": "Event timing must be resolved before interpreting the pattern.",
        "BLOCKED_BY_DATA_GAP": "Do not judge yet; required data or evidence is missing.",
        "BLOCKED_BY_REQUIRED_GATE": "A required gate failed; keep it as counter-evidence.",
        "CONTROL_SUPPORT_REVIEW": "A control case also supports it; narrow or reject the mechanism before replay.",
        "NEEDS_MORE_CASES": "Too few comparable cases to design a replay rule.",
        "MIXED_ANALOG_REVIEW": "Useful as a mixed analog, not a replay-ready rule.",
        "REQUIRES_REVIEW": "Manual review is required before this can be interpreted.",
    }.get(status, _friendly_value(status))


def _sort_hypotheses_for_readout(hypotheses: pd.DataFrame) -> pd.DataFrame:
    if hypotheses.empty or "hypothesis_name" not in hypotheses:
        return hypotheses
    return hypotheses.assign(
        _h_order=hypotheses["hypothesis_name"].astype(str).map(_hypothesis_order_key)
    ).sort_values(["_h_order", "hypothesis_name"]).drop(columns=["_h_order"])


def _hypothesis_order_key(name: str) -> int:
    for index, prefix in enumerate(["H1", "H2", "H3", "H4"], start=1):
        if name.startswith(prefix):
            return index
    return 99


def _symbols_for_status(
    rows: pd.DataFrame,
    status: str,
    *,
    include_controls: bool,
    extra_statuses: set[str] | None = None,
) -> str:
    if rows.empty:
        return "-"
    statuses = {status} | (extra_statuses or set())
    filtered = rows[rows["result_status"].astype(str).isin(statuses)]
    if not include_controls:
        filtered = filtered[~filtered["case_role"].astype(str).isin(["peer_control", "negative_control"])]
    symbols = sorted(str(symbol) for symbol in filtered.get("symbol", []).tolist())
    return ", ".join(symbols) if symbols else "-"


def _control_check_text(rows: pd.DataFrame) -> str:
    if rows.empty or "case_role" not in rows:
        return "No control rows."
    controls = rows[rows["case_role"].astype(str).isin(["peer_control", "negative_control"])]
    if controls.empty:
        return "No control rows."
    supporting = sorted(str(symbol) for symbol in controls[controls["result_status"] == "SUPPORTS"]["symbol"].tolist())
    if supporting:
        return "False-positive review required: " + ", ".join(supporting)
    evaluable = controls[~controls["result_status"].astype(str).isin(["DATA_GAP", "TIMING_GAP", "REQUIRES_REVIEW"])]
    if evaluable.empty:
        return "Controls are present but not evaluable yet."
    blocked = sorted(str(symbol) for symbol in controls[controls["result_status"] == "BLOCKS"]["symbol"].tolist())
    if blocked:
        return "Controls did not support: " + ", ".join(blocked)
    return "Controls reviewed; no support signal in current matrix."


def _pattern_teaching_text(hypothesis: pd.Series, rows: pd.DataFrame) -> str:
    name = str(hypothesis.get("hypothesis_name") or "")
    status = str(hypothesis.get("promotion_status") or "")
    support = _symbols_for_status(rows, "SUPPORTS", include_controls=False)
    controls = _control_check_text(rows)
    if name.startswith("H1"):
        return "Anchor evidence is useful for theme mapping, not downstream readiness."
    if name.startswith("H2"):
        return f"Downstream demand conversion currently appears in: {support}. {controls}"
    if name.startswith("H3"):
        if status == "ELIGIBLE_FOR_REPLAY_DESIGN":
            return f"Industry evidence and pre-event technical gates align in: {support}."
        return "Composite pattern is still blocked by data gaps, timing gaps, or controls."
    if name.startswith("H4"):
        return "Industry-only cases are a separate analog bucket; they should not be treated as technical setups."
    return str(hypothesis.get("mechanism") or "")


def _ordered_symbols(*frames: pd.DataFrame) -> list[str]:
    seen: dict[str, None] = {}
    for frame in frames:
        if frame.empty or "symbol" not in frame:
            continue
        for symbol in frame["symbol"].tolist():
            text = str(symbol or "").upper()
            if text:
                seen.setdefault(text, None)
    preferred = ["NVDA", "MU", "SNDK", "LITE", "AMD", "INTC", "MRVL"]
    ordered = [symbol for symbol in preferred if symbol in seen]
    ordered.extend(symbol for symbol in sorted(seen) if symbol not in preferred)
    return ordered


def _symbol_rows(frame: pd.DataFrame, column: str, symbol: str) -> pd.DataFrame:
    if frame.empty or column not in frame:
        return pd.DataFrame()
    return frame[frame[column].astype(str).str.upper() == symbol.upper()]


def _case_role_from_results(rows: pd.DataFrame) -> str:
    if rows.empty or "case_role" not in rows:
        return "unknown"
    values = [str(value) for value in rows["case_role"].dropna().tolist()]
    return values[0] if values else "unknown"


def _industry_evidence_label(evidence: pd.DataFrame, symbol: str) -> str:
    rows = _symbol_rows(evidence, "symbol", symbol)
    if rows.empty:
        return "DATA GAP - no evidence ledger row"
    demand = rows[rows["evidence_lane"].astype(str) == "customer_demand"]
    if not demand.empty:
        usable = demand[
            demand["usable_in_replay"].astype(bool)
            & demand["supports_pattern"].astype(bool)
            & (demand["evidence_status"].astype(str) == "PASS")
        ]
        if not usable.empty:
            return "PASS - PIT official demand evidence"
        statuses = sorted(set(str(value) for value in demand["evidence_status"].tolist()))
        return "REVIEW - demand evidence not PIT-usable yet: " + ", ".join(statuses)
    context = rows[rows["evidence_lane"].astype(str).isin(["listing_context", "theme_context"])]
    if not context.empty:
        return "REVIEW - context evidence only"
    return "DATA GAP - no demand evidence"


def _hypothesis_name_map(hypotheses: pd.DataFrame) -> dict[str, str]:
    if hypotheses.empty or "hypothesis_id" not in hypotheses:
        return {}
    return {
        str(row.get("hypothesis_id") or ""): str(row.get("hypothesis_name") or "")
        for _, row in hypotheses.iterrows()
    }


def _hypothesis_case_status(
    rows: pd.DataFrame,
    prefix: str,
    hypothesis_names: dict[str, str] | None = None,
) -> str:
    if rows.empty or "hypothesis_id" not in rows:
        return "NO_GATES"
    id_map = hypothesis_names or {}
    matched = rows[rows["hypothesis_id"].astype(str).map(lambda value: id_map.get(value, "").startswith(prefix))]
    if matched.empty:
        # The stored result table does not repeat names, so derive order by result rows when needed.
        unique_ids = list(dict.fromkeys(str(value) for value in rows["hypothesis_id"].tolist()))
        index_by_prefix = {"H1": 0, "H2": 1, "H3": 2, "H4": 3}
        index = index_by_prefix.get(prefix)
        if index is not None and len(unique_ids) > index:
            matched = rows[rows["hypothesis_id"].astype(str) == unique_ids[index]]
    if matched.empty:
        return "NO_GATES"
    return str(matched.iloc[0].get("result_status") or "NO_GATES")


def _gate_status(gates: pd.DataFrame, gate_name: str) -> str:
    if gates.empty or "gate_name" not in gates:
        return "NO_GATES"
    matched = gates[gates["gate_name"].astype(str) == gate_name]
    if matched.empty:
        return "NO_GATES"
    return str(matched.iloc[0].get("gate_status") or "NO_GATES")


def _gate_status_prefix(gates: pd.DataFrame, gate_name_prefix: str) -> str:
    if gates.empty or "gate_name" not in gates:
        return "NO_GATES"
    matched = gates[gates["gate_name"].astype(str).str.startswith(gate_name_prefix)]
    if matched.empty:
        return "NO_GATES"
    return str(matched.iloc[0].get("gate_status") or "NO_GATES")


def _case_pattern_read(
    results: pd.DataFrame,
    evidence: pd.DataFrame,
    gates: pd.DataFrame,
    symbol: str,
    hypothesis_names: dict[str, str] | None = None,
) -> str:
    role = _case_role_from_results(results)
    h2 = _hypothesis_case_status(results, "H2", hypothesis_names)
    h3 = _hypothesis_case_status(results, "H3", hypothesis_names)
    h4 = _hypothesis_case_status(results, "H4", hypothesis_names)
    technical_support = (
        _gate_status(gates, "Stage 2 trend explain") == "PASS"
        and _gate_status_prefix(gates, "Benchmark RS vs") == "PASS"
    )
    if role in {"peer_control", "negative_control"}:
        if technical_support and h2 in {"DATA_GAP", "TIMING_GAP", "REQUIRES_REVIEW"}:
            return "Technical strength can appear in controls; require PIT industry evidence before treating it as a pattern."
        return "Control case: use it to test whether the mechanism is too broad."
    if h3 == "SUPPORTS":
        return "Industry evidence and pre-event technical strength align before the replay point."
    if h2 == "SUPPORTS" and h3 in {"DATA_GAP", "TIMING_GAP", "REQUIRES_REVIEW"}:
        return "Industry evidence exists, but technical replay evidence is incomplete."
    if h4 == "SUPPORTS":
        return "Industry evidence exists while required technical gates fail; treat as mixed analog."
    if "context only" in _industry_evidence_label(evidence, symbol).lower():
        return "Context is useful for story-building, but it is not downstream demand conversion."
    return "Not enough aligned industry and technical evidence yet."


def _case_pattern_next_task(
    results: pd.DataFrame,
    evidence: pd.DataFrame,
    gates: pd.DataFrame,
    symbol: str,
) -> str:
    industry = _industry_evidence_label(evidence, symbol)
    if industry.startswith("DATA GAP"):
        return "Attach PIT official industry evidence or keep industry claims blocked."
    if "not PIT-usable" in industry or "context evidence only" in industry:
        return "Resolve evidence timing or keep this as context-only."
    stage2 = _gate_status(gates, "Stage 2 trend explain")
    benchmark = _gate_status_prefix(gates, "Benchmark RS vs")
    if "DATA_GAP" in {stage2, benchmark}:
        return "Load pre-event price and fixed benchmark coverage."
    if "FAIL" in {stage2, benchmark}:
        return "Keep failed technical gates as counter-evidence."
    role = _case_role_from_results(results)
    if role in {"peer_control", "negative_control"}:
        return "If the control supports, narrow the mechanism before replay design."
    return "Compare against controls and expand the case set before validation."


def _friendly_value(value: object) -> str:
    return str(value or "-").replace("_", " ").title()


def group_gate_rows_by_symbol(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("Symbol") or "")].append(row)
    return dict(grouped)
