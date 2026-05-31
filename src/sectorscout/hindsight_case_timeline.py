from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from sectorscout.config import SectorScoutConfig
from sectorscout.hindsight import (
    DEFAULT_HINDSIGHT_CASES_PATH,
    latest_hindsight_evidence,
    latest_hindsight_events,
    latest_hindsight_replay_gates,
    load_hindsight_cases,
)


@dataclass(frozen=True)
class HindsightCaseTimeline:
    symbol: str
    label: str
    case_role: str
    theme: str
    event_type: str
    event_date: str
    published_at_utc: str | None
    replay_decision_at_utc: str | None
    first_tradable_date: str | None
    first_tradable_policy: str
    knowable_evidence: list[str]
    future_context: list[str]
    source_urls: list[str]
    timing_status: str
    pre_event_status: str
    first_tradable_status: str
    timeline_status: str
    reviewer_readout: str
    guardrail: str
    next_research_step: str
    tone: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_hindsight_case_timelines(
    config: SectorScoutConfig,
    *,
    path: Path = DEFAULT_HINDSIGHT_CASES_PATH,
) -> list[HindsightCaseTimeline]:
    cases = load_hindsight_cases(path)
    events = latest_hindsight_events(config)
    evidence = latest_hindsight_evidence(config)
    gates = latest_hindsight_replay_gates(config)
    return [
        _timeline_for_case(
            case=case,
            events=_symbol_frame(events, case.symbol),
            evidence=_symbol_frame(evidence, case.symbol),
            gates=_symbol_frame(gates, case.symbol),
        )
        for case in cases
    ]


def case_timeline_summary(items: list[HindsightCaseTimeline]) -> dict[str, object]:
    return {
        "timelines": len(items),
        "pit_ready": sum(1 for item in items if item.timeline_status == "PIT_TIMELINE_READY"),
        "future_context_splits": sum(1 for item in items if item.timeline_status == "FUTURE_CONTEXT_SPLIT"),
        "control_evidence_gaps": sum(1 for item in items if item.timeline_status == "CONTROL_EVIDENCE_GAP"),
        "technical_data_gaps": sum(1 for item in items if item.timeline_status == "TECHNICAL_DATA_GAP"),
        "timing_data_gaps": sum(1 for item in items if item.timeline_status == "TIMING_DATA_GAP"),
    }


def case_timelines_to_frame(items: list[HindsightCaseTimeline]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Symbol": item.symbol,
                "Role": item.case_role.replace("_", " ").title(),
                "Event type": item.event_type.replace("_", " ").title(),
                "Event date": item.event_date,
                "Published at UTC": item.published_at_utc or "-",
                "Replay decision at UTC": item.replay_decision_at_utc or "-",
                "First tradable date": item.first_tradable_date or "-",
                "Knowable evidence": "; ".join(item.knowable_evidence) or "-",
                "Future context": "; ".join(item.future_context) or "-",
                "Timing": item.timing_status,
                "Pre-event technical": item.pre_event_status,
                "First-tradable data": item.first_tradable_status,
                "Timeline status": item.timeline_status,
                "Current read": item.reviewer_readout,
                "Guardrail": item.guardrail,
                "Next step": item.next_research_step,
            }
            for item in items
        ]
    )


def _timeline_for_case(case: Any, events: pd.DataFrame, evidence: pd.DataFrame, gates: pd.DataFrame) -> HindsightCaseTimeline:
    event = events.iloc[0] if not events.empty else pd.Series(dtype=object)
    replay_decision = _replay_decision_at(event)
    knowable = _knowable_evidence(evidence)
    future = _future_context(evidence)
    timing_status = _lane_status(gates, "event_timing")
    pre_event_status = _lane_status(gates, "pre_event")
    first_tradable_status = _lane_status(gates, "first_tradable")
    timeline_status = _timeline_status(case, event, knowable, future, timing_status, pre_event_status)
    return HindsightCaseTimeline(
        symbol=case.symbol,
        label=case.label,
        case_role=case.case_role,
        theme=case.theme,
        event_type=str(event.get("event_type") or "case_seed"),
        event_date=_date_text(event.get("event_date") or case.start_date),
        published_at_utc=_timestamp_text(event.get("published_at_utc")),
        replay_decision_at_utc=_timestamp_text(replay_decision),
        first_tradable_date=_date_text(event.get("first_tradable_date")),
        first_tradable_policy=str(event.get("first_tradable_bar_policy") or "unresolved"),
        knowable_evidence=knowable,
        future_context=future,
        source_urls=_source_urls(event, evidence),
        timing_status=timing_status,
        pre_event_status=pre_event_status,
        first_tradable_status=first_tradable_status,
        timeline_status=timeline_status,
        reviewer_readout=_reviewer_readout(case, timeline_status, knowable, future, pre_event_status),
        guardrail=_guardrail(timeline_status),
        next_research_step=_next_step(case, timeline_status, pre_event_status, first_tradable_status),
        tone=_tone(timeline_status),
    )


def _symbol_frame(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if frame.empty or "symbol" not in frame:
        return pd.DataFrame()
    return frame[frame["symbol"].astype(str).str.upper() == symbol.upper()]


def _replay_decision_at(event: pd.Series) -> datetime | None:
    first_tradable = _coerce_date(event.get("first_tradable_date"))
    if first_tradable is None:
        return None
    return datetime.combine(first_tradable, time(14, 30), tzinfo=timezone.utc)


def _knowable_evidence(evidence: pd.DataFrame) -> list[str]:
    if evidence.empty:
        return []
    rows = evidence[evidence.get("usable_in_replay", pd.Series(dtype=bool)).astype(bool)]
    return [_evidence_label(row) for _, row in rows.iterrows()]


def _future_context(evidence: pd.DataFrame) -> list[str]:
    if evidence.empty:
        return []
    rows = []
    for _, row in evidence.iterrows():
        note = str(row.get("review_note") or "").lower()
        status = str(row.get("evidence_status") or "")
        available = _coerce_timestamp(row.get("available_at_utc"))
        replay = _coerce_timestamp(row.get("replay_decision_at"))
        future_by_time = bool(available and replay and available > replay)
        if future_by_time or status == "REQUIRES_REVIEW" or "future-only" in note or "later" in note:
            rows.append(_evidence_label(row))
    return rows


def _evidence_label(row: pd.Series) -> str:
    claim = str(row.get("claim") or "").strip()
    metric_name = str(row.get("metric_name") or "").strip()
    metric_value = str(row.get("metric_value") or "").strip()
    metric = " / ".join(part for part in [metric_name, metric_value] if part)
    if claim and metric:
        return f"{claim} ({metric})"
    return claim or metric or str(row.get("evidence_kind") or "evidence")


def _lane_status(gates: pd.DataFrame, gate_group: str) -> str:
    if gates.empty or "gate_group" not in gates:
        return "NO_GATE"
    rows = gates[gates["gate_group"].astype(str) == gate_group]
    if rows.empty:
        return "NO_GATE"
    statuses = {str(status or "") for status in rows["gate_status"].tolist()}
    if "FAIL" in statuses:
        return "FAIL"
    if "DATA_GAP" in statuses:
        return "DATA_GAP"
    if "REQUIRES_REVIEW" in statuses:
        return "REQUIRES_REVIEW"
    if statuses == {"PASS"}:
        return "PASS"
    return "REVIEW"


def _timeline_status(
    case: Any,
    event: pd.Series,
    knowable: list[str],
    future: list[str],
    timing_status: str,
    pre_event_status: str,
) -> str:
    if "control" in str(case.case_role or "") and not knowable:
        return "CONTROL_EVIDENCE_GAP"
    if future:
        return "FUTURE_CONTEXT_SPLIT"
    if timing_status in {"DATA_GAP", "NO_GATE"}:
        return "TIMING_DATA_GAP"
    if not knowable:
        return "EVIDENCE_DATA_GAP"
    if pre_event_status == "PASS":
        return "PIT_TIMELINE_READY"
    if pre_event_status in {"DATA_GAP", "NO_GATE"}:
        return "TECHNICAL_DATA_GAP"
    return "TIMELINE_REVIEW"


def _reviewer_readout(
    case: Any,
    status: str,
    knowable: list[str],
    future: list[str],
    pre_event_status: str,
) -> str:
    if status == "PIT_TIMELINE_READY":
        return f"{case.symbol}: event timing, knowable evidence, and pre-event technical gates are ready for replay-design review."
    if status == "FUTURE_CONTEXT_SPLIT":
        return f"{case.symbol}: {len(future)} later-context item(s) must stay separate from the original replay point."
    if status == "CONTROL_EVIDENCE_GAP":
        return f"{case.symbol}: control row lacks comparable PIT evidence, so it cannot confirm or reject the mechanism yet."
    if status == "TECHNICAL_DATA_GAP":
        return f"{case.symbol}: {len(knowable)} knowable evidence item(s) exist, but pre-event technical status is {pre_event_status}."
    if status == "TIMING_DATA_GAP":
        return f"{case.symbol}: event timing or first-tradable date is unresolved."
    return f"{case.symbol}: timeline requires reviewer attention before interpretation."


def _guardrail(status: str) -> str:
    return {
        "PIT_TIMELINE_READY": "Use only evidence visible at or before the replay decision time.",
        "FUTURE_CONTEXT_SPLIT": "Later confirmation can explain the story, but cannot support the original replay point.",
        "CONTROL_EVIDENCE_GAP": "Do not treat missing control evidence as a failed pattern.",
        "TECHNICAL_DATA_GAP": "Do not infer technical alignment until pre-event coverage is loaded.",
        "TIMING_DATA_GAP": "Do not analyze event reaction until timestamp and first-tradable policy are resolved.",
        "EVIDENCE_DATA_GAP": "Do not promote a mechanism without timestamped evidence.",
    }.get(status, "Keep this row in manual review.")


def _next_step(case: Any, status: str, pre_event_status: str, first_tradable_status: str) -> str:
    if status == "PIT_TIMELINE_READY":
        return "Compare the same evidence and gate requirements against controls and additional historical analogs."
    if status == "FUTURE_CONTEXT_SPLIT":
        return "Write separate rows for original PIT evidence and later confirmation before using this case in a rule design."
    if status == "CONTROL_EVIDENCE_GAP":
        return f"Attach comparable official PIT evidence and source timing for {case.symbol}."
    if status == "TECHNICAL_DATA_GAP":
        return f"Load or audit pre-event technical coverage; current pre-event status is {pre_event_status}."
    if first_tradable_status == "DATA_GAP":
        return "Resolve the first-tradable date and first-session price row."
    return "Review source timing, evidence usability, and gate coverage."


def _source_urls(event: pd.Series, evidence: pd.DataFrame) -> list[str]:
    urls = {str(event.get("source_url") or "").strip()}
    if not evidence.empty and "source_url" in evidence:
        urls.update(str(url or "").strip() for url in evidence["source_url"].tolist())
    return sorted(url for url in urls if url)


def _tone(status: str) -> str:
    if status == "PIT_TIMELINE_READY":
        return "green"
    if status == "FUTURE_CONTEXT_SPLIT":
        return "purple"
    if status in {"CONTROL_EVIDENCE_GAP", "TECHNICAL_DATA_GAP", "TIMING_DATA_GAP"}:
        return "amber"
    if status == "EVIDENCE_DATA_GAP":
        return "red"
    return "blue"


def _date_text(value: object) -> str | None:
    date_value = _coerce_date(value)
    return date_value.isoformat() if date_value is not None else None


def _timestamp_text(value: object) -> str | None:
    timestamp = _coerce_timestamp(value)
    return timestamp.isoformat() if timestamp is not None else None


def _coerce_date(value: object) -> Any | None:
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "date") and not isinstance(value, str):
        return value.date() if not hasattr(value, "year") or hasattr(value, "hour") else value
    try:
        return pd.Timestamp(value).date()
    except (TypeError, ValueError):
        return None


def _coerce_timestamp(value: object) -> datetime | None:
    if value is None or pd.isna(value):
        return None
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(timezone.utc)
    return timestamp.to_pydatetime()
