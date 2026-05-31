from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

import pandas as pd


STATUS_ORDER = ["DATA_GAP", "FAIL", "PENDING", "PASS", "N/A"]


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


def group_gate_rows_by_symbol(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("Symbol") or "")].append(row)
    return dict(grouped)
