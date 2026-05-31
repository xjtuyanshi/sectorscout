from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from sectorscout.config import SectorScoutConfig
from sectorscout.hindsight import DEFAULT_HINDSIGHT_CASES_PATH
from sectorscout.hindsight_industry_profile import build_hindsight_industry_profiles
from sectorscout.hindsight_pattern_matrix import HindsightPatternMatrixRow, build_hindsight_pattern_matrix


@dataclass(frozen=True)
class HindsightPatternCandidate:
    candidate_id: str
    title: str
    candidate_type: str
    mechanism: str
    supporting_symbols: list[str]
    blocked_symbols: list[str]
    control_symbols: list[str]
    readiness_status: str
    reviewer_readout: str
    industry_evidence_required: list[str]
    technical_confirmation_required: list[str]
    anti_hindsight_guardrail: str
    next_research_step: str
    source_basis: list[str]
    tone: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_hindsight_pattern_candidates(
    config: SectorScoutConfig,
    *,
    path: Path = DEFAULT_HINDSIGHT_CASES_PATH,
) -> list[HindsightPatternCandidate]:
    profiles = build_hindsight_industry_profiles(config, path=path)
    matrix_rows = build_hindsight_pattern_matrix(config, path=path)
    profile_by_symbol = {profile.symbol: profile for profile in profiles}
    matrix_by_symbol = {row.symbol: row for row in matrix_rows}
    controls = sorted(row.symbol for row in matrix_rows if "control" in row.case_role)

    return [
        _single_symbol_candidate(
            candidate_id="P1_ANCHOR_COMPUTE_SHOCK",
            title="Anchor compute demand shock",
            symbol="NVDA",
            candidate_type="anchor_mechanism",
            mechanism=(
                "Official AI data-center demand evidence appears first at the anchor compute leader, then replay "
                "asks whether pre-event trend and benchmark-relative strength were already visible."
            ),
            industry_required=[
                "Official data-center or accelerator demand evidence with timestamp.",
                "Revenue or guidance language tied to AI infrastructure demand.",
            ],
            technical_required=[
                "Pre-event price coverage before the first replay decision point.",
                "Stage 2 trend context and fixed-benchmark relative strength.",
            ],
            guardrail="Do not treat later 2024 leadership as evidence that was knowable before the replay point.",
            next_step="Review official NVDA source text, then compare the same demand-and-technical requirements against controls.",
            profile_by_symbol=profile_by_symbol,
            matrix_by_symbol=matrix_by_symbol,
            controls=controls,
        ),
        _single_symbol_candidate(
            candidate_id="P2_DOWNSTREAM_MEMORY_CONVERSION",
            title="Downstream memory conversion",
            symbol="MU",
            candidate_type="downstream_mechanism",
            mechanism=(
                "AI compute demand can translate into HBM and memory revenue, but the replay card requires "
                "official memory-demand evidence plus the same pre-event technical confirmation gates."
            ),
            industry_required=[
                "Official HBM, AI data-center memory, or revenue acceleration evidence.",
                "Release timestamp that is available before the replay decision point.",
            ],
            technical_required=[
                "Pre-event trend structure, benchmark-relative strength, and enough price rows for review.",
                "Theme rotation context must be audited separately from the historical outcome.",
            ],
            guardrail="Do not infer a memory-cycle rule from NVDA alone; MU needs its own PIT evidence and gates.",
            next_step="Check whether MU's official AI-memory evidence and technical gates align before adding more memory cases.",
            profile_by_symbol=profile_by_symbol,
            matrix_by_symbol=matrix_by_symbol,
            controls=controls,
        ),
        _single_symbol_candidate(
            candidate_id="P3_STORAGE_CONTEXT_SPLIT",
            title="Storage context split",
            symbol="SNDK",
            candidate_type="leakage_guardrail",
            mechanism=(
                "Standalone storage/NAND context may matter, but the replay must split the first regular-way "
                "trading setup from later datacenter-storage confirmation."
            ),
            industry_required=[
                "Official standalone listing or spin-off evidence for the original replay point.",
                "Separate later demand evidence from any evidence available at first regular-way trading.",
            ],
            technical_required=[
                "First-session-only price policy; do not splice predecessor price history unless a reviewed policy exists.",
                "Post-listing technical evidence must be labeled by the date it became observable.",
            ],
            guardrail="Future-only 2026 storage evidence can explain context, but it cannot support the February 2025 replay decision.",
            next_step="Keep SNDK in guardrail review until the PIT listing evidence and later storage-demand evidence are shown separately.",
            profile_by_symbol=profile_by_symbol,
            matrix_by_symbol=matrix_by_symbol,
            controls=controls,
            force_guardrail=True,
        ),
        _single_symbol_candidate(
            candidate_id="P4_OPTICAL_INTERCONNECT_TRANSFER",
            title="Optical interconnect transfer",
            symbol="LITE",
            candidate_type="downstream_mechanism",
            mechanism=(
                "AI data-center buildout can broaden into optical networking demand when official backlog, "
                "orders, or customer-demand evidence line up with pre-event technical gates."
            ),
            industry_required=[
                "Official optical demand, backlog, or order evidence tied to AI infrastructure.",
                "Timestamped source text before the reviewed replay point.",
            ],
            technical_required=[
                "Pre-event price coverage, Stage 2 trend context, and benchmark-relative strength.",
                "Control comparison against adjacent AI infrastructure peers.",
            ],
            guardrail="Do not generalize every AI-infrastructure peer into the same rule without explicit evidence.",
            next_step="Compare LITE's backlog/order evidence against MRVL and other controls before narrowing the mechanism.",
            profile_by_symbol=profile_by_symbol,
            matrix_by_symbol=matrix_by_symbol,
            controls=controls,
        ),
        _control_candidate(matrix_rows),
    ]


def pattern_candidates_summary(items: list[HindsightPatternCandidate]) -> dict[str, object]:
    return {
        "candidates": len(items),
        "review_ready": sum(1 for item in items if item.readiness_status == "REPLAY_DESIGN_REVIEW_READY"),
        "technical_data_tasks": sum(1 for item in items if item.readiness_status == "NEEDS_TECHNICAL_COVERAGE"),
        "guardrail_items": sum(1 for item in items if item.readiness_status == "FUTURE_CONTEXT_GUARDRAIL"),
        "control_checks": sum(1 for item in items if item.candidate_type == "control_check"),
    }


def pattern_candidates_to_frame(items: list[HindsightPatternCandidate]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Card": item.title,
                "Type": item.candidate_type.replace("_", " ").title(),
                "Status": item.readiness_status,
                "Support": ", ".join(item.supporting_symbols) or "-",
                "Blocked": ", ".join(item.blocked_symbols) or "-",
                "Controls": ", ".join(item.control_symbols) or "-",
                "Mechanism": item.mechanism,
                "Evidence required": "; ".join(item.industry_evidence_required),
                "Technical required": "; ".join(item.technical_confirmation_required),
                "Guardrail": item.anti_hindsight_guardrail,
                "Next step": item.next_research_step,
            }
            for item in items
        ]
    )


def _single_symbol_candidate(
    *,
    candidate_id: str,
    title: str,
    symbol: str,
    candidate_type: str,
    mechanism: str,
    industry_required: list[str],
    technical_required: list[str],
    guardrail: str,
    next_step: str,
    profile_by_symbol: dict[str, Any],
    matrix_by_symbol: dict[str, HindsightPatternMatrixRow],
    controls: list[str],
    force_guardrail: bool = False,
) -> HindsightPatternCandidate:
    row = matrix_by_symbol.get(symbol)
    profile = profile_by_symbol.get(symbol)
    readiness_status = _readiness_status(row, force_guardrail=force_guardrail)
    blocked_symbols = [symbol] if readiness_status not in {"REPLAY_DESIGN_REVIEW_READY", "NEEDS_TECHNICAL_COVERAGE"} else []
    supporting_symbols = [symbol] if profile is not None and str(profile.profile_status).startswith("PIT_USABLE") else []
    if readiness_status == "NEEDS_EVIDENCE":
        blocked_symbols = [symbol]
        supporting_symbols = []
    return HindsightPatternCandidate(
        candidate_id=candidate_id,
        title=title,
        candidate_type=candidate_type,
        mechanism=mechanism,
        supporting_symbols=supporting_symbols,
        blocked_symbols=blocked_symbols,
        control_symbols=controls,
        readiness_status=readiness_status,
        reviewer_readout=_reviewer_readout(symbol, row, readiness_status),
        industry_evidence_required=industry_required,
        technical_confirmation_required=technical_required,
        anti_hindsight_guardrail=guardrail,
        next_research_step=next_step,
        source_basis=_source_basis(profile),
        tone=_tone_for_status(readiness_status),
    )


def _control_candidate(rows: list[HindsightPatternMatrixRow]) -> HindsightPatternCandidate:
    controls = [row for row in rows if "control" in row.case_role]
    gap_symbols = [row.symbol for row in controls if row.alignment_label == "CONTROL_DATA_GAP"]
    technical_only_symbols = [row.symbol for row in controls if row.alignment_label == "CONTROL_TECHNICAL_ONLY_REVIEW"]
    readiness = "CONTROL_COMPARABILITY_CHECK"
    readout = (
        f"{len(gap_symbols)} controls still need comparable evidence."
        if gap_symbols
        else "Controls can be compared against leader/downstream rows."
    )
    return HindsightPatternCandidate(
        candidate_id="P5_CONTROL_COMPARABILITY",
        title="Control comparability check",
        candidate_type="control_check",
        mechanism=(
            "Controls prevent the lab from learning only from winners. A broad AI or semiconductor story is not enough "
            "unless the same PIT industry evidence and technical gates are available for peers."
        ),
        supporting_symbols=[],
        blocked_symbols=gap_symbols + technical_only_symbols,
        control_symbols=[row.symbol for row in controls],
        readiness_status=readiness,
        reviewer_readout=readout,
        industry_evidence_required=[
            "Comparable official evidence for each peer or negative-control row.",
            "Explicit reason when a control should be outside the mechanism.",
        ],
        technical_confirmation_required=[
            "The same price coverage, trend, and benchmark-relative strength gates as leader rows.",
            "Control rows must not be counted as failures while evidence is missing.",
        ],
        anti_hindsight_guardrail="If a control later satisfies the same mechanism, narrow the candidate rule before replay design.",
        next_research_step="Load comparable PIT evidence for AMD, INTC, and MRVL before interpreting leader-only clusters.",
        source_basis=[],
        tone="amber" if gap_symbols else "blue",
    )


def _readiness_status(row: HindsightPatternMatrixRow | None, *, force_guardrail: bool) -> str:
    if row is None:
        return "NEEDS_EVIDENCE"
    if force_guardrail or row.alignment_label == "FUTURE_CONTEXT_REVIEW":
        return "FUTURE_CONTEXT_GUARDRAIL"
    if row.alignment_label == "INDUSTRY_AND_TECHNICAL_ALIGNED":
        return "REPLAY_DESIGN_REVIEW_READY"
    if row.alignment_label == "INDUSTRY_READY_TECHNICAL_DATA_GAP":
        return "NEEDS_TECHNICAL_COVERAGE"
    if row.alignment_label == "INDUSTRY_READY_TECHNICAL_FAIL":
        return "COUNTER_EVIDENCE_REVIEW"
    return "NEEDS_EVIDENCE"


def _reviewer_readout(symbol: str, row: HindsightPatternMatrixRow | None, status: str) -> str:
    if row is None:
        return f"{symbol} is missing from the industry plus technical matrix."
    if status == "REPLAY_DESIGN_REVIEW_READY":
        return f"{symbol} has PIT industry evidence and required pre-event technical gates available for review."
    if status == "NEEDS_TECHNICAL_COVERAGE":
        return f"{symbol} has usable industry evidence, but technical gates still need complete pre-event coverage."
    if status == "FUTURE_CONTEXT_GUARDRAIL":
        return f"{symbol} has useful context, but later evidence must be separated from the original replay point."
    if status == "COUNTER_EVIDENCE_REVIEW":
        return f"{symbol} has industry evidence while required technical gates currently disagree."
    return row.review_note


def _source_basis(profile: Any | None) -> list[str]:
    if profile is None:
        return []
    return [str(url) for url in profile.source_urls if str(url or "").strip()]


def _tone_for_status(status: str) -> str:
    if status == "REPLAY_DESIGN_REVIEW_READY":
        return "green"
    if status == "FUTURE_CONTEXT_GUARDRAIL":
        return "purple"
    if status in {"NEEDS_TECHNICAL_COVERAGE", "CONTROL_COMPARABILITY_CHECK"}:
        return "amber"
    if status == "COUNTER_EVIDENCE_REVIEW":
        return "red"
    return "blue"
