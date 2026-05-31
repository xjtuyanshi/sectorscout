from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

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


@dataclass(frozen=True)
class HindsightPatternDiagnostic:
    diagnostic_id: str
    title: str
    diagnostic_type: str
    status: str
    symbols: list[str]
    evidence_summary: str
    interpretation: str
    next_action: str
    guardrail: str

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


def build_hindsight_pattern_diagnostics(rows: list[HindsightPatternMatrixRow]) -> list[HindsightPatternDiagnostic]:
    return [
        _aligned_cluster_diagnostic(rows),
        _future_context_diagnostic(rows),
        _control_coverage_diagnostic(rows),
        _technical_gap_diagnostic(rows),
        _technical_only_diagnostic(rows),
    ]


def pattern_diagnostics_summary(diagnostics: list[HindsightPatternDiagnostic]) -> dict[str, object]:
    return {
        "diagnostics": len(diagnostics),
        "high_review_items": sum(1 for item in diagnostics if item.status in {"REVIEW_CLUSTER", "GUARDRAIL_REVIEW"}),
        "data_tasks": sum(1 for item in diagnostics if item.status in {"DATA_TASK", "CONTROL_DATA_GAP"}),
        "clear_items": sum(1 for item in diagnostics if item.status in {"NO_CURRENT_GAP", "NO_TECHNICAL_ONLY_ROWS"}),
    }


def pattern_diagnostics_to_frame(diagnostics: list[HindsightPatternDiagnostic]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Diagnostic": item.title,
                "Type": item.diagnostic_type.replace("_", " ").title(),
                "Status": item.status,
                "Symbols": ", ".join(item.symbols) or "-",
                "Evidence": item.evidence_summary,
                "Interpretation": item.interpretation,
                "Next action": item.next_action,
                "Guardrail": item.guardrail,
            }
            for item in diagnostics
        ]
    )


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


def _aligned_cluster_diagnostic(rows: list[HindsightPatternMatrixRow]) -> HindsightPatternDiagnostic:
    aligned = [row for row in rows if row.alignment_label == "INDUSTRY_AND_TECHNICAL_ALIGNED"]
    symbols = [row.symbol for row in aligned]
    drivers = _unique(row.demand_driver for row in aligned)
    status = "REVIEW_CLUSTER" if len(symbols) >= 2 else "NEEDS_MORE_CASES"
    return HindsightPatternDiagnostic(
        diagnostic_id="D1_ALIGNED_LEADER_CLUSTER",
        title="Aligned leader cluster",
        diagnostic_type="candidate_pattern",
        status=status,
        symbols=symbols,
        evidence_summary=_count_summary(symbols, "aligned leader/downstream rows"),
        interpretation=(
            "PIT industry evidence and required pre-event technical gates align across multiple leader/downstream rows."
            if status == "REVIEW_CLUSTER"
            else "Aligned evidence is too sparse to review as a cross-case cluster."
        ),
        next_action=(
            "Expand the case set and compare against controls before any replay promotion."
            if status == "REVIEW_CLUSTER"
            else "Load more historical cases or price coverage before interpreting this as a repeatable pattern."
        ),
        guardrail=f"Drivers observed: {', '.join(drivers) or '-'}. This remains a review cluster, not validation.",
    )


def _future_context_diagnostic(rows: list[HindsightPatternMatrixRow]) -> HindsightPatternDiagnostic:
    future = [row for row in rows if row.alignment_label == "FUTURE_CONTEXT_REVIEW"]
    symbols = [row.symbol for row in future]
    return HindsightPatternDiagnostic(
        diagnostic_id="D2_FUTURE_CONTEXT_GUARDRAIL",
        title="Future-context guardrail",
        diagnostic_type="leakage_guardrail",
        status="GUARDRAIL_REVIEW" if symbols else "NO_CURRENT_FUTURE_CONTEXT",
        symbols=symbols,
        evidence_summary=_count_summary(symbols, "future-context rows"),
        interpretation=(
            "At least one case has useful later context that must stay out of the original replay decision."
            if symbols
            else "No future-context row is currently flagged in the matrix."
        ),
        next_action=(
            "Split the PIT catalyst from later confirmation before using the row in case-set learning."
            if symbols
            else "Keep checking future-only evidence as new cases are added."
        ),
        guardrail="Future confirmation can explain a story, but it cannot support the original replay point.",
    )


def _control_coverage_diagnostic(rows: list[HindsightPatternMatrixRow]) -> HindsightPatternDiagnostic:
    controls = [row for row in rows if "control" in row.case_role]
    control_gaps = [row for row in controls if row.alignment_label in {"CONTROL_DATA_GAP", "CONTROL_TECHNICAL_ONLY_REVIEW"}]
    symbols = [row.symbol for row in control_gaps]
    return HindsightPatternDiagnostic(
        diagnostic_id="D3_CONTROL_COVERAGE",
        title="Control coverage",
        diagnostic_type="control_check",
        status="CONTROL_DATA_GAP" if symbols else "CONTROL_COVERAGE_READY",
        symbols=symbols,
        evidence_summary=f"{len(control_gaps)} of {len(controls)} control rows need review.",
        interpretation=(
            "Controls are not yet comparable because industry evidence or technical coverage is missing."
            if symbols
            else "Controls have enough matrix evidence for comparison."
        ),
        next_action=(
            "Attach comparable PIT evidence and price coverage to controls before narrowing or promoting mechanisms."
            if symbols
            else "Compare control labels against leader/downstream labels for false-positive risk."
        ),
        guardrail="A control data gap is not a failed pattern and not support for leader-only claims.",
    )


def _technical_gap_diagnostic(rows: list[HindsightPatternMatrixRow]) -> HindsightPatternDiagnostic:
    gaps = [row for row in rows if row.alignment_label == "INDUSTRY_READY_TECHNICAL_DATA_GAP"]
    symbols = [row.symbol for row in gaps]
    return HindsightPatternDiagnostic(
        diagnostic_id="D4_INDUSTRY_READY_TECHNICAL_GAP",
        title="Industry-ready technical gaps",
        diagnostic_type="data_task",
        status="DATA_TASK" if symbols else "NO_CURRENT_GAP",
        symbols=symbols,
        evidence_summary=_count_summary(symbols, "industry-ready rows with technical data gaps"),
        interpretation=(
            "Industry evidence is visible, but required technical context is incomplete."
            if symbols
            else "No current industry-ready row is blocked only by technical data coverage."
        ),
        next_action=(
            "Load pre-event prices and fixed benchmarks, then rerun replay gates."
            if symbols
            else "Keep monitoring this bucket as more cases or cleaner price coverage are added."
        ),
        guardrail="Do not treat DATA_GAP as technical failure or support.",
    )


def _technical_only_diagnostic(rows: list[HindsightPatternMatrixRow]) -> HindsightPatternDiagnostic:
    technical_only = [
        row
        for row in rows
        if row.alignment_label in {"TECHNICAL_ONLY_REVIEW", "CONTROL_TECHNICAL_ONLY_REVIEW"}
    ]
    symbols = [row.symbol for row in technical_only]
    return HindsightPatternDiagnostic(
        diagnostic_id="D5_TECHNICAL_ONLY_RISK",
        title="Technical-only risk",
        diagnostic_type="false_positive_review",
        status="TECHNICAL_ONLY_REVIEW" if symbols else "NO_TECHNICAL_ONLY_ROWS",
        symbols=symbols,
        evidence_summary=_count_summary(symbols, "technical-only rows"),
        interpretation=(
            "Technical structure appears without PIT industry support, so the mechanism may be too broad."
            if symbols
            else "No current row shows technical support without industry support."
        ),
        next_action=(
            "Treat these rows as false-positive review candidates, not as aligned patterns."
            if symbols
            else "Recheck after adding more control cases and benchmark coverage."
        ),
        guardrail="Technical strength by itself is not a SectorScout industry pattern.",
    )


def _count_summary(symbols: list[str], label: str) -> str:
    return f"{len(symbols)} {label}: {', '.join(symbols) if symbols else '-'}."


def _unique(values: Any) -> list[str]:
    return sorted({str(value) for value in values if str(value or "").strip()})
