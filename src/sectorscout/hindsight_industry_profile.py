from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from sectorscout.config import SectorScoutConfig
from sectorscout.hindsight import DEFAULT_HINDSIGHT_CASES_PATH, latest_hindsight_evidence, load_hindsight_cases


@dataclass(frozen=True)
class HindsightIndustryProfile:
    symbol: str
    label: str
    case_role: str
    theme: str
    industry_chain_node: str
    demand_driver: str
    demand_stage: str
    evidence_kinds: list[str]
    mechanism_tags: list[str]
    quantitative_markers: list[str]
    pit_usable_evidence_count: int
    review_required_evidence_count: int
    future_context_count: int
    source_quality_mix: list[str]
    source_urls: list[str]
    profile_status: str
    profile_readiness: str
    review_note: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_hindsight_industry_profiles(
    config: SectorScoutConfig,
    *,
    path: Path = DEFAULT_HINDSIGHT_CASES_PATH,
) -> list[HindsightIndustryProfile]:
    """Summarize industry evidence without changing live SectorScout scoring."""

    cases = load_hindsight_cases(path)
    evidence = latest_hindsight_evidence(config)
    if evidence.empty:
        return [_profile_from_case(case, pd.DataFrame()) for case in cases]
    return [_profile_from_case(case, evidence[evidence["symbol"].astype(str).str.upper() == case.symbol]) for case in cases]


def industry_profile_summary(profiles: list[HindsightIndustryProfile]) -> dict[str, object]:
    return {
        "profiles": len(profiles),
        "pit_ready_profiles": sum(1 for row in profiles if row.profile_status.startswith("PIT_USABLE")),
        "data_gap_profiles": sum(1 for row in profiles if row.profile_status == "DATA_GAP"),
        "future_context_profiles": sum(1 for row in profiles if "FUTURE_CONTEXT" in row.profile_status),
        "drivers": sorted({row.demand_driver for row in profiles}),
    }


def industry_profiles_to_frame(profiles: list[HindsightIndustryProfile]) -> pd.DataFrame:
    return pd.DataFrame([_display_row(profile) for profile in profiles])


def _profile_from_case(case: Any, rows: pd.DataFrame) -> HindsightIndustryProfile:
    text = _combined_text(case, rows)
    usable_count = _count_bool(rows, "usable_in_replay")
    review_count = _count_bool(rows, "requires_review")
    future_count = _future_context_count(rows)
    evidence_kinds = _unique_values(rows, "evidence_kind")
    source_quality_mix = _unique_values(rows, "source_quality")
    source_urls = _unique_values(rows, "source_url")
    tags = _mechanism_tags(text)
    status = _profile_status(usable_count, review_count, future_count)
    return HindsightIndustryProfile(
        symbol=case.symbol,
        label=case.label,
        case_role=case.case_role,
        theme=case.theme,
        industry_chain_node=_industry_chain_node(case),
        demand_driver=_demand_driver(text, case.theme),
        demand_stage=_demand_stage(case.case_role, tags, future_count),
        evidence_kinds=evidence_kinds,
        mechanism_tags=tags,
        quantitative_markers=_quantitative_markers(rows),
        pit_usable_evidence_count=usable_count,
        review_required_evidence_count=review_count,
        future_context_count=future_count,
        source_quality_mix=source_quality_mix,
        source_urls=source_urls,
        profile_status=status,
        profile_readiness=_profile_readiness(status, case.case_role),
        review_note=_review_note(case.case_role, status, tags),
    )


def _display_row(profile: HindsightIndustryProfile) -> dict[str, object]:
    return {
        "Symbol": profile.symbol,
        "Role": _friendly(profile.case_role),
        "Theme": profile.theme,
        "Industry node": profile.industry_chain_node,
        "Demand driver": profile.demand_driver,
        "Demand stage": profile.demand_stage,
        "Mechanism tags": ", ".join(profile.mechanism_tags) or "-",
        "Evidence kinds": ", ".join(profile.evidence_kinds) or "-",
        "Quant markers": "; ".join(profile.quantitative_markers) or "-",
        "PIT usable evidence": profile.pit_usable_evidence_count,
        "Review required": profile.review_required_evidence_count,
        "Future context": profile.future_context_count,
        "Profile status": profile.profile_status,
        "Readiness": profile.profile_readiness,
        "Review note": profile.review_note,
    }


def _combined_text(case: Any, rows: pd.DataFrame) -> str:
    parts = [case.symbol, case.label, case.theme, case.hindsight_reason, case.anchor_event, case.control_reason]
    if not rows.empty:
        for _, row in rows.iterrows():
            parts.extend(
                [
                    row.get("evidence_lane"),
                    row.get("evidence_kind"),
                    row.get("claim"),
                    row.get("metric_name"),
                    row.get("metric_value"),
                    row.get("metric_period"),
                    row.get("review_note"),
                    row.get("source_quality"),
                ]
            )
    return " ".join(str(part or "") for part in parts).lower()


def _industry_chain_node(case: Any) -> str:
    role = str(case.case_role or "")
    symbol = str(case.symbol or "").upper()
    if symbol == "NVDA" or role == "anchor":
        return "Anchor AI compute leader"
    if symbol == "MU":
        return "Downstream AI memory node"
    if symbol == "SNDK":
        return "Downstream storage / NAND node"
    if symbol == "LITE":
        return "Downstream optical networking node"
    if "control" in role:
        return "Control case"
    return "Watchlist node"


def _demand_driver(text: str, theme: str) -> str:
    theme_text = str(theme or "").lower()
    combined = f"{theme_text} {text}"
    if any(token in combined for token in ["hbm", "high-bandwidth", "memory"]):
        return "AI memory / HBM demand"
    if any(token in combined for token in ["optical", "ocs", "cpo", "lumentum"]):
        return "AI optical infrastructure demand"
    if any(token in combined for token in ["storage", "nand", "sandisk"]):
        return "Datacenter storage / NAND demand"
    if any(token in combined for token in ["data center", "datacenter", "accelerator", "ai infrastructure"]):
        return "AI data-center compute demand"
    if any(token in combined for token in ["spin-off", "spin off", "regular-way", "listing"]):
        return "Corporate action discovery"
    return "Needs driver review"


def _demand_stage(case_role: str, tags: list[str], future_count: int) -> str:
    role = str(case_role or "")
    if role == "anchor":
        return "Anchor demand shock"
    if future_count:
        return "Partly future-confirmed context"
    if "backlog_or_orders" in tags:
        return "Order/backlog visibility"
    if role == "downstream_node":
        return "Downstream demand translation"
    if "control" in role:
        return "Control evidence pending"
    return "Review stage"


def _mechanism_tags(text: str) -> list[str]:
    tags: list[str] = []
    checks = [
        ("ai_data_center", ["data center", "datacenter", "ai infrastructure"]),
        ("hbm_memory", ["hbm", "high-bandwidth", "memory"]),
        ("storage_cycle", ["storage", "nand", "sandisk"]),
        ("optical_ai_networking", ["optical", "ocs", "cpo", "lumentum"]),
        ("revenue_acceleration", ["revenue", "growth", "record"]),
        ("backlog_or_orders", ["backlog", "orders", "order"]),
        ("corporate_action_listing", ["spin-off", "spin off", "regular-way", "listing"]),
        ("future_only_context", ["future-only", "not usable at first", "later became visible"]),
    ]
    for tag, needles in checks:
        if any(needle in text for needle in needles):
            tags.append(tag)
    return tags


def _profile_status(usable_count: int, review_count: int, future_count: int) -> str:
    if usable_count and future_count:
        return "PIT_USABLE_WITH_FUTURE_CONTEXT"
    if usable_count:
        return "PIT_USABLE"
    if future_count:
        return "FUTURE_CONTEXT_ONLY"
    if review_count:
        return "DATA_GAP"
    return "DATA_GAP"


def _profile_readiness(status: str, case_role: str) -> str:
    role = str(case_role or "")
    if status == "PIT_USABLE":
        return "Industry evidence ready for replay review"
    if status == "PIT_USABLE_WITH_FUTURE_CONTEXT":
        return "Replay review allowed; separate future context"
    if "control" in role:
        return "Control requires same PIT evidence before interpretation"
    if status == "FUTURE_CONTEXT_ONLY":
        return "Not usable at original replay point"
    return "Attach timestamped official evidence first"


def _review_note(case_role: str, status: str, tags: list[str]) -> str:
    if "control" in str(case_role or "") and status == "DATA_GAP":
        return "Control row is not evaluable until comparable PIT evidence is loaded."
    if status == "PIT_USABLE_WITH_FUTURE_CONTEXT":
        return "Use the PIT evidence separately from later confirmation so the replay does not leak future context."
    if status == "PIT_USABLE":
        return "Industry evidence is timestamped; still review source text and mechanism tag before promotion."
    if "future_only_context" in tags:
        return "Future context is visible; keep it out of the original replay decision."
    return "Missing official PIT evidence; keep the industry claim blocked."


def _count_bool(rows: pd.DataFrame, column: str) -> int:
    if rows.empty or column not in rows:
        return 0
    return int(rows[column].astype(bool).sum())


def _future_context_count(rows: pd.DataFrame) -> int:
    if rows.empty:
        return 0
    count = 0
    for _, row in rows.iterrows():
        note = str(row.get("review_note") or "").lower()
        status = str(row.get("evidence_status") or "")
        available = row.get("available_at_utc")
        replay = row.get("replay_decision_at")
        future_by_time = pd.notna(available) and pd.notna(replay) and pd.Timestamp(available) > pd.Timestamp(replay)
        if future_by_time or "future-only" in note or "not usable at first" in note:
            count += 1
        elif status == "REQUIRES_REVIEW" and "later" in note:
            count += 1
    return count


def _unique_values(rows: pd.DataFrame, column: str) -> list[str]:
    if rows.empty or column not in rows:
        return []
    values = [str(value) for value in rows[column].dropna().tolist() if str(value).strip()]
    return sorted(set(values))


def _quantitative_markers(rows: pd.DataFrame) -> list[str]:
    if rows.empty:
        return []
    markers: list[str] = []
    for _, row in rows.iterrows():
        name = str(row.get("metric_name") or "").strip()
        value = str(row.get("metric_value") or "").strip()
        period = str(row.get("metric_period") or "").strip()
        if not value:
            continue
        markers.append(" / ".join(part for part in [name, value, period] if part))
    return markers


def _friendly(value: str) -> str:
    return value.replace("_", " ").title()


def industry_profiles_json(profiles: list[HindsightIndustryProfile]) -> str:
    return json.dumps([profile.to_dict() for profile in profiles], indent=2, sort_keys=True)
