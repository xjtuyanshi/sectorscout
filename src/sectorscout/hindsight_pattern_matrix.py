from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from sectorscout.config import SectorScoutConfig
from sectorscout.hindsight import (
    DEFAULT_HINDSIGHT_CASES_PATH,
    latest_hindsight_replay_gates,
)
from sectorscout.hindsight_industry_profile import HindsightIndustryProfile, build_hindsight_industry_profiles


REQUIRED_TECHNICAL_GATES = ("Pre-event price coverage", "Stage 2 trend explain", "Benchmark RS")


@dataclass(frozen=True)
class HindsightPatternMatrixRow:
    symbol: str
    case_role: str
    industry_chain_node: str
    demand_driver: str
    industry_status: str
    price_coverage_status: str
    stage2_status: str
    benchmark_rs_status: str
    technical_status: str
    alignment_label: str
    review_priority: str
    review_note: str
    mechanism_tags: list[str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_hindsight_pattern_matrix(
    config: SectorScoutConfig,
    *,
    path: Path = DEFAULT_HINDSIGHT_CASES_PATH,
) -> list[HindsightPatternMatrixRow]:
    profiles = build_hindsight_industry_profiles(config, path=path)
    gates = latest_hindsight_replay_gates(config)
    return [_matrix_row(profile, gates) for profile in profiles]


def pattern_matrix_summary(rows: list[HindsightPatternMatrixRow]) -> dict[str, object]:
    return {
        "rows": len(rows),
        "aligned_rows": sum(1 for row in rows if row.alignment_label == "INDUSTRY_AND_TECHNICAL_ALIGNED"),
        "industry_ready_technical_gap_rows": sum(
            1 for row in rows if row.alignment_label == "INDUSTRY_READY_TECHNICAL_DATA_GAP"
        ),
        "control_review_rows": sum(1 for row in rows if "CONTROL" in row.alignment_label),
        "future_context_rows": sum(1 for row in rows if row.alignment_label == "FUTURE_CONTEXT_REVIEW"),
        "data_gap_rows": sum(1 for row in rows if row.alignment_label == "DATA_GAP"),
    }


def pattern_matrix_to_frame(rows: list[HindsightPatternMatrixRow]) -> pd.DataFrame:
    return pd.DataFrame([_display_row(row) for row in rows])


def _matrix_row(profile: HindsightIndustryProfile, gates: pd.DataFrame) -> HindsightPatternMatrixRow:
    symbol_gates = _symbol_gates(gates, profile.symbol)
    price_coverage = _gate_status(symbol_gates, "Pre-event price coverage")
    stage2 = _gate_status(symbol_gates, "Stage 2 trend explain")
    benchmark_rs = _gate_status_prefix(symbol_gates, "Benchmark RS")
    technical_status = _technical_rollup(price_coverage, stage2, benchmark_rs)
    alignment = _alignment_label(profile, technical_status)
    return HindsightPatternMatrixRow(
        symbol=profile.symbol,
        case_role=profile.case_role,
        industry_chain_node=profile.industry_chain_node,
        demand_driver=profile.demand_driver,
        industry_status=profile.profile_status,
        price_coverage_status=price_coverage,
        stage2_status=stage2,
        benchmark_rs_status=benchmark_rs,
        technical_status=technical_status,
        alignment_label=alignment,
        review_priority=_review_priority(alignment),
        review_note=_review_note(profile, technical_status, alignment),
        mechanism_tags=profile.mechanism_tags,
    )


def _display_row(row: HindsightPatternMatrixRow) -> dict[str, object]:
    return {
        "Symbol": row.symbol,
        "Role": row.case_role.replace("_", " ").title(),
        "Demand driver": row.demand_driver,
        "Industry status": row.industry_status,
        "Price coverage": row.price_coverage_status,
        "Stage 2": row.stage2_status,
        "Benchmark RS": row.benchmark_rs_status,
        "Technical status": row.technical_status,
        "Matrix label": row.alignment_label,
        "Review priority": row.review_priority,
        "Mechanism tags": ", ".join(row.mechanism_tags) or "-",
        "Review note": row.review_note,
    }


def _symbol_gates(gates: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if gates.empty or "symbol" not in gates:
        return pd.DataFrame()
    return gates[gates["symbol"].astype(str).str.upper() == symbol.upper()]


def _gate_status(gates: pd.DataFrame, gate_name: str) -> str:
    if gates.empty or "gate_name" not in gates:
        return "NO_GATE"
    matched = gates[gates["gate_name"].astype(str) == gate_name]
    if matched.empty:
        return "NO_GATE"
    return str(matched.iloc[0].get("gate_status") or "NO_GATE")


def _gate_status_prefix(gates: pd.DataFrame, gate_name_prefix: str) -> str:
    if gates.empty or "gate_name" not in gates:
        return "NO_GATE"
    matched = gates[gates["gate_name"].astype(str).str.startswith(gate_name_prefix)]
    if matched.empty:
        return "NO_GATE"
    return str(matched.iloc[0].get("gate_status") or "NO_GATE")


def _technical_rollup(price_coverage: str, stage2: str, benchmark_rs: str) -> str:
    statuses = {price_coverage, stage2, benchmark_rs}
    if "FAIL" in statuses:
        return "TECHNICAL_FAIL"
    if statuses == {"PASS"}:
        return "TECHNICAL_PASS"
    if statuses & {"DATA_GAP", "PENDING", "REQUIRES_REVIEW", "NO_GATE"}:
        return "TECHNICAL_DATA_GAP"
    return "TECHNICAL_REVIEW"


def _alignment_label(profile: HindsightIndustryProfile, technical_status: str) -> str:
    role = str(profile.case_role or "")
    industry_ready = profile.profile_status in {"PIT_USABLE", "PIT_USABLE_WITH_FUTURE_CONTEXT"}
    if role in {"peer_control", "negative_control"}:
        if technical_status == "TECHNICAL_PASS" and not industry_ready:
            return "CONTROL_TECHNICAL_ONLY_REVIEW"
        if not industry_ready:
            return "CONTROL_DATA_GAP"
    if profile.profile_status == "PIT_USABLE_WITH_FUTURE_CONTEXT":
        return "FUTURE_CONTEXT_REVIEW"
    if industry_ready and technical_status == "TECHNICAL_PASS":
        return "INDUSTRY_AND_TECHNICAL_ALIGNED"
    if industry_ready and technical_status == "TECHNICAL_DATA_GAP":
        return "INDUSTRY_READY_TECHNICAL_DATA_GAP"
    if industry_ready and technical_status == "TECHNICAL_FAIL":
        return "INDUSTRY_READY_TECHNICAL_FAIL"
    if not industry_ready and technical_status == "TECHNICAL_PASS":
        return "TECHNICAL_ONLY_REVIEW"
    return "DATA_GAP"


def _review_priority(alignment_label: str) -> str:
    if alignment_label == "INDUSTRY_AND_TECHNICAL_ALIGNED":
        return "HIGH_REVIEW"
    if alignment_label in {"FUTURE_CONTEXT_REVIEW", "CONTROL_TECHNICAL_ONLY_REVIEW"}:
        return "HIGH_GUARDRAIL_REVIEW"
    if alignment_label in {"INDUSTRY_READY_TECHNICAL_FAIL", "TECHNICAL_ONLY_REVIEW"}:
        return "MEDIUM_REVIEW"
    if alignment_label in {"INDUSTRY_READY_TECHNICAL_DATA_GAP", "CONTROL_DATA_GAP"}:
        return "DATA_TASK"
    return "LOW_UNTIL_DATA"


def _review_note(profile: HindsightIndustryProfile, technical_status: str, alignment_label: str) -> str:
    if alignment_label == "INDUSTRY_AND_TECHNICAL_ALIGNED":
        return "Candidate pattern for review: PIT industry evidence and required pre-event technical gates align."
    if alignment_label == "FUTURE_CONTEXT_REVIEW":
        return "Separate PIT evidence from later confirmation before using this case in replay design."
    if alignment_label == "CONTROL_TECHNICAL_ONLY_REVIEW":
        return "Control has technical support without PIT industry evidence; narrow the mechanism before promotion."
    if alignment_label == "CONTROL_DATA_GAP":
        return "Control is not evaluable until comparable PIT industry and technical evidence are loaded."
    if alignment_label == "INDUSTRY_READY_TECHNICAL_DATA_GAP":
        return "Industry evidence is visible, but required pre-event technical coverage is incomplete."
    if alignment_label == "INDUSTRY_READY_TECHNICAL_FAIL":
        return "Industry evidence is visible while required technical gates fail; keep as counter-evidence."
    if alignment_label == "TECHNICAL_ONLY_REVIEW":
        return "Technical structure exists without PIT industry support; do not treat as an industry pattern."
    if technical_status == "TECHNICAL_DATA_GAP":
        return "Missing evidence remains missing; do not infer alignment."
    return profile.review_note
