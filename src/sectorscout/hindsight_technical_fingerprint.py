from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from sectorscout.config import SectorScoutConfig
from sectorscout.hindsight import DEFAULT_HINDSIGHT_CASES_PATH, latest_hindsight_replay_gates, load_hindsight_cases


@dataclass(frozen=True)
class HindsightTechnicalFingerprint:
    symbol: str
    label: str
    case_role: str
    price_coverage_status: str
    price_coverage_detail: str
    stage2_status: str
    stage2_detail: str
    benchmark_rs_status: str
    benchmark_rs_detail: str
    first_tradable_status: str
    first_tradable_detail: str
    fingerprint_status: str
    fingerprint_label: str
    reviewer_readout: str
    guardrail: str
    next_research_step: str
    tone: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["technical_fingerprint_status"] = self.fingerprint_status
        return payload


def build_hindsight_technical_fingerprints(
    config: SectorScoutConfig,
    *,
    path: Path = DEFAULT_HINDSIGHT_CASES_PATH,
) -> list[HindsightTechnicalFingerprint]:
    cases = load_hindsight_cases(path)
    gates = latest_hindsight_replay_gates(config)
    return [_fingerprint_for_case(case, _symbol_gates(gates, case.symbol)) for case in cases]


def technical_fingerprint_summary(items: list[HindsightTechnicalFingerprint]) -> dict[str, object]:
    return {
        "fingerprints": len(items),
        "ready": sum(1 for item in items if item.fingerprint_status == "TECHNICAL_FINGERPRINT_READY"),
        "data_gaps": sum(1 for item in items if item.fingerprint_status == "TECHNICAL_CONTEXT_DATA_GAP"),
        "new_listing_gaps": sum(1 for item in items if item.fingerprint_status == "NEW_LISTING_TECHNICAL_GAP"),
        "control_partial_technical": sum(
            1 for item in items if item.fingerprint_status == "CONTROL_PARTIAL_TECHNICAL_REVIEW"
        ),
        "technical_failures": sum(1 for item in items if item.fingerprint_status == "TECHNICAL_FINGERPRINT_FAIL"),
        "ready_symbols": [item.symbol for item in items if item.fingerprint_status == "TECHNICAL_FINGERPRINT_READY"],
    }


def technical_fingerprints_to_frame(items: list[HindsightTechnicalFingerprint]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Symbol": item.symbol,
                "Role": item.case_role.replace("_", " ").title(),
                "Price coverage": item.price_coverage_status,
                "Price detail": item.price_coverage_detail,
                "Stage 2": item.stage2_status,
                "Stage 2 detail": item.stage2_detail,
                "Benchmark RS": item.benchmark_rs_status,
                "Benchmark detail": item.benchmark_rs_detail,
                "First-tradable": item.first_tradable_status,
                "Technical fingerprint": item.fingerprint_status,
                "Fingerprint label": item.fingerprint_label,
                "Current read": item.reviewer_readout,
                "Guardrail": item.guardrail,
                "Next step": item.next_research_step,
            }
            for item in items
        ]
    )


def _fingerprint_for_case(case: object, gates: pd.DataFrame) -> HindsightTechnicalFingerprint:
    price = _gate(gates, "Pre-event price coverage")
    stage2 = _gate(gates, "Stage 2 trend explain")
    benchmark = _gate_prefix(gates, "Benchmark RS")
    first_tradable = _gate_prefix(gates, "First-tradable")
    status = _fingerprint_status(case, price.status, stage2.status, benchmark.status)
    return HindsightTechnicalFingerprint(
        symbol=str(case.symbol),
        label=str(case.label),
        case_role=str(case.case_role),
        price_coverage_status=price.status,
        price_coverage_detail=price.detail,
        stage2_status=stage2.status,
        stage2_detail=stage2.detail,
        benchmark_rs_status=benchmark.status,
        benchmark_rs_detail=benchmark.detail,
        first_tradable_status=first_tradable.status,
        first_tradable_detail=first_tradable.detail,
        fingerprint_status=status,
        fingerprint_label=_fingerprint_label(status),
        reviewer_readout=_reviewer_readout(case, status, price.status, stage2.status, benchmark.status),
        guardrail=_guardrail(status),
        next_research_step=_next_step(case, status, price.status, stage2.status, benchmark.status),
        tone=_tone(status),
    )


@dataclass(frozen=True)
class _GateView:
    status: str
    detail: str


def _symbol_gates(gates: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if gates.empty or "symbol" not in gates:
        return pd.DataFrame()
    return gates[gates["symbol"].astype(str).str.upper() == str(symbol).upper()]


def _gate(gates: pd.DataFrame, gate_name: str) -> _GateView:
    if gates.empty or "gate_name" not in gates:
        return _GateView("NO_GATE", "Gate row is missing.")
    rows = gates[gates["gate_name"].astype(str) == gate_name]
    if rows.empty:
        return _GateView("NO_GATE", "Gate row is missing.")
    row = rows.iloc[0]
    return _GateView(str(row.get("gate_status") or "NO_GATE"), _gate_detail(row))


def _gate_prefix(gates: pd.DataFrame, prefix: str) -> _GateView:
    if gates.empty or "gate_name" not in gates:
        return _GateView("NO_GATE", "Gate row is missing.")
    rows = gates[gates["gate_name"].astype(str).str.startswith(prefix)]
    if rows.empty:
        return _GateView("NO_GATE", "Gate row is missing.")
    row = rows.iloc[0]
    return _GateView(str(row.get("gate_status") or "NO_GATE"), _gate_detail(row))


def _gate_detail(row: pd.Series) -> str:
    parts = [
        str(row.get("computed_value") or "").strip(),
        str(row.get("threshold") or "").strip(),
        str(row.get("reason") or "").strip(),
    ]
    return " | ".join(part for part in parts if part) or "-"


def _fingerprint_status(case: object, price: str, stage2: str, benchmark: str) -> str:
    statuses = {price, stage2, benchmark}
    symbol = str(case.symbol).upper()
    if "FAIL" in statuses:
        return "TECHNICAL_FINGERPRINT_FAIL"
    if symbol == "SNDK" and statuses & {"DATA_GAP", "NO_GATE"}:
        return "NEW_LISTING_TECHNICAL_GAP"
    if statuses == {"PASS"}:
        if "control" in str(case.case_role or ""):
            return "CONTROL_TECHNICAL_READY_REVIEW"
        return "TECHNICAL_FINGERPRINT_READY"
    if "control" in str(case.case_role or "") and "PASS" in statuses:
        return "CONTROL_PARTIAL_TECHNICAL_REVIEW"
    if statuses & {"DATA_GAP", "NO_GATE", "PENDING", "REQUIRES_REVIEW"}:
        return "TECHNICAL_CONTEXT_DATA_GAP"
    return "TECHNICAL_FINGERPRINT_REVIEW"


def _fingerprint_label(status: str) -> str:
    return {
        "TECHNICAL_FINGERPRINT_READY": "Technical fingerprint ready",
        "NEW_LISTING_TECHNICAL_GAP": "New listing technical gap",
        "CONTROL_PARTIAL_TECHNICAL_REVIEW": "Control technical review",
        "CONTROL_TECHNICAL_READY_REVIEW": "Control fingerprint ready for comparison",
        "TECHNICAL_FINGERPRINT_FAIL": "Technical gate review required",
        "TECHNICAL_CONTEXT_DATA_GAP": "Technical context data gap",
        "TECHNICAL_FINGERPRINT_REVIEW": "Technical fingerprint review",
    }.get(status, status.replace("_", " ").title())


def _reviewer_readout(case: object, status: str, price: str, stage2: str, benchmark: str) -> str:
    symbol = str(case.symbol)
    if status == "TECHNICAL_FINGERPRINT_READY":
        return f"{symbol}: price coverage, Stage 2 trend proxy, and fixed-benchmark RS are all present before the replay point."
    if status == "NEW_LISTING_TECHNICAL_GAP":
        return f"{symbol}: no pre-listing price path is available under the no-splice policy, so trend and RS remain blocked."
    if status == "CONTROL_PARTIAL_TECHNICAL_REVIEW":
        return f"{symbol}: some technical strength is visible in a control, but comparable industry evidence is still required."
    if status == "CONTROL_TECHNICAL_READY_REVIEW":
        return f"{symbol}: control technical gates are ready; use this to test whether the mechanism is too broad."
    if status == "TECHNICAL_FINGERPRINT_FAIL":
        return f"{symbol}: at least one required technical gate failed."
    return f"{symbol}: technical context is incomplete: price={price}, stage2={stage2}, benchmark_rs={benchmark}."


def _guardrail(status: str) -> str:
    return {
        "TECHNICAL_FINGERPRINT_READY": "Technical readiness is context for review, not proof of a repeatable rule.",
        "NEW_LISTING_TECHNICAL_GAP": "Do not splice predecessor history into a new listing unless a reviewed policy explicitly allows it.",
        "CONTROL_PARTIAL_TECHNICAL_REVIEW": "Technical strength in controls can expose false positives; require PIT industry evidence.",
        "CONTROL_TECHNICAL_READY_REVIEW": "If controls share the same technical fingerprint, narrow the mechanism before replay design.",
        "TECHNICAL_FINGERPRINT_FAIL": "Treat failure as counter-evidence only after confirming data quality.",
        "TECHNICAL_CONTEXT_DATA_GAP": "DATA_GAP is neither support nor rejection.",
    }.get(status, "Keep the technical fingerprint in manual review.")


def _next_step(case: object, status: str, price: str, stage2: str, benchmark: str) -> str:
    if status == "TECHNICAL_FINGERPRINT_READY":
        return "Compare this fingerprint against controls and more historical leaders before writing replay rules."
    if status == "NEW_LISTING_TECHNICAL_GAP":
        return "Keep first-session and post-listing technical evidence separate from pre-event trend analysis."
    if status in {"CONTROL_PARTIAL_TECHNICAL_REVIEW", "CONTROL_TECHNICAL_READY_REVIEW"}:
        return f"Attach comparable PIT industry evidence for {case.symbol}, then decide if the mechanism is too broad."
    if price != "PASS":
        return "Load or audit pre-event price coverage."
    if stage2 != "PASS":
        return "Load enough pre-event rows to evaluate the Stage 2 trend proxy."
    if benchmark != "PASS":
        return "Load matching fixed-benchmark coverage and rerun benchmark RS."
    return "Review technical gate inputs and source coverage."


def _tone(status: str) -> str:
    if status == "TECHNICAL_FINGERPRINT_READY":
        return "green"
    if status == "NEW_LISTING_TECHNICAL_GAP":
        return "purple"
    if status in {"CONTROL_PARTIAL_TECHNICAL_REVIEW", "CONTROL_TECHNICAL_READY_REVIEW"}:
        return "amber"
    if status == "TECHNICAL_FINGERPRINT_FAIL":
        return "red"
    return "blue"
