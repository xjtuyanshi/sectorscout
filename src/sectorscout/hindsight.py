from __future__ import annotations

import csv
import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from sectorscout.config import SectorScoutConfig, config_hash
from sectorscout.db import connect_database
from sectorscout.metadata import get_git_commit


DEFAULT_HINDSIGHT_CASES_PATH = Path("data/hindsight/leader_cases.csv")
HINDSIGHT_HYPOTHESIS_RULE_VERSION = "hindsight_hypothesis_registry_v1"


OFFICIAL_EVENT_SEEDS: dict[str, dict[str, Any]] = {
    "NVDA": {
        "event_type": "earnings_results",
        "event_date": date(2024, 2, 21),
        "published_at_utc": datetime(2024, 2, 21, 21, 22, 9, tzinfo=timezone.utc),
        "market_session": "after_close",
        "source_url": "https://www.sec.gov/Archives/edgar/data/1045810/000104581024000028/0001045810-24-000028-index.htm",
        "source_quality": "sec_8k_official",
        "evidence_type": "realized_earnings_and_forward_guidance",
        "evidence_summary": "Q4 FY2024 results: record revenue and Data Center growth; 8-K accepted after market close.",
        "first_tradable_date": date(2024, 2, 22),
        "first_tradable_bar_policy": "next_regular_session_after_after_close_release",
        "technical_replay_as_of": date(2024, 2, 21),
        "timing_status": "TIMING_RESOLVED",
        "requires_review": False,
    },
    "MU": {
        "event_type": "earnings_results",
        "event_date": date(2025, 9, 23),
        "published_at_utc": datetime(2025, 9, 23, 20, 2, 28, tzinfo=timezone.utc),
        "market_session": "after_close",
        "source_url": "https://www.sec.gov/Archives/edgar/data/723125/000072312525000024/0000723125-25-000024-index.htm",
        "source_quality": "sec_8k_official",
        "evidence_type": "realized_earnings_and_forward_guidance",
        "evidence_summary": "FY2025 results: record fiscal Q4/full-year revenue driven by AI data center growth; 8-K accepted after market close.",
        "first_tradable_date": date(2025, 9, 24),
        "first_tradable_bar_policy": "next_regular_session_after_after_close_release",
        "technical_replay_as_of": date(2025, 9, 23),
        "timing_status": "TIMING_RESOLVED",
        "requires_review": False,
    },
    "SNDK": {
        "event_type": "spin_off_listing",
        "event_date": date(2025, 1, 31),
        "published_at_utc": datetime(2025, 2, 3, 21, 26, 53, tzinfo=timezone.utc),
        "market_session": "after_close",
        "source_url": "https://www.sec.gov/Archives/edgar/data/2023554/000119312525019298/d919795d8k.htm",
        "source_quality": "sec_8k_official",
        "evidence_type": "spin_off_schedule_and_regular_way_listing",
        "evidence_summary": "Registration statement effectiveness 8-K: distribution expected after close and regular-way SNDK trading expected on Nasdaq.",
        "first_tradable_date": date(2025, 2, 24),
        "first_tradable_bar_policy": "first_regular_way_trading_session_only_no_wdc_splice",
        "technical_replay_as_of": date(2025, 2, 24),
        "timing_status": "TIMING_RESOLVED",
        "requires_review": False,
    },
    "LITE": {
        "event_type": "earnings_results",
        "event_date": date(2026, 2, 3),
        "published_at_utc": datetime(2026, 2, 3, 21, 12, 14, tzinfo=timezone.utc),
        "market_session": "after_close",
        "source_url": "https://www.sec.gov/Archives/edgar/data/1633978/000162828026005005/0001628280-26-005005-index.htm",
        "source_quality": "sec_8k_official",
        "evidence_type": "realized_earnings_backlog_and_forward_orders",
        "evidence_summary": "Q2 FY2026 results: AI optical demand, OCS backlog above $400 million, and CPO order context; 8-K accepted after market close.",
        "first_tradable_date": date(2026, 2, 4),
        "first_tradable_bar_policy": "next_regular_session_after_after_close_release",
        "technical_replay_as_of": date(2026, 2, 3),
        "timing_status": "TIMING_RESOLVED",
        "requires_review": False,
    },
}


OFFICIAL_EVIDENCE_SEEDS: dict[str, list[dict[str, Any]]] = {
    "NVDA": [
        {
            "evidence_lane": "customer_demand",
            "evidence_kind": "segment_revenue",
            "claim": "AI data-center demand was visible in official Q4 FY2024 results and Data Center revenue acceleration.",
            "metric_name": "Data Center revenue",
            "metric_value": "Q4 FY2024 $18.4B; FY2024 $47.5B",
            "metric_period": "Q4 FY2024 / FY2024",
            "source_url": "https://investor.nvidia.com/news/press-release-details/2024/NVIDIA-Announces-Financial-Results-for-Fourth-Quarter-and-Fiscal-2024/default.aspx",
            "source_quality": "official_company_release",
            "supports_pattern": True,
            "requires_review": False,
            "review_note": "Official evidence for AI infrastructure demand; use only after the SEC accepted timestamp.",
        }
    ],
    "MU": [
        {
            "evidence_lane": "customer_demand",
            "evidence_kind": "earnings_release",
            "claim": "Record fiscal Q4 and full-year revenue was attributed to AI data center growth.",
            "metric_name": "Revenue",
            "metric_value": "Q4 FY2025 $11.3B; FY2025 $37.4B",
            "metric_period": "Q4 FY2025 / FY2025",
            "source_url": "https://micron.gcs-web.com/news-releases/news-release-details/micron-technology-inc-reports-results-fourth-quarter-and-full-8",
            "source_quality": "official_company_release",
            "supports_pattern": True,
            "requires_review": False,
            "review_note": "Official evidence for AI data-center memory demand; use only after release timestamp.",
        }
    ],
    "SNDK": [
        {
            "evidence_lane": "catalyst",
            "evidence_kind": "spin_off_registration",
            "claim": "Standalone SanDisk regular-way trading schedule was officially knowable before the first SNDK session.",
            "metric_name": "Regular-way listing",
            "metric_value": "Expected February 24, 2025",
            "metric_period": "Spin-off registration",
            "source_url": "https://www.sec.gov/Archives/edgar/data/2023554/000119312525019298/d919795d8k.htm",
            "source_quality": "sec_8k_official",
            "supports_pattern": True,
            "requires_review": False,
            "review_note": "Do not splice WDC price history into SNDK unless a separate pro-forma policy is enabled.",
        },
        {
            "evidence_lane": "customer_demand",
            "evidence_kind": "segment_revenue",
            "claim": "Datacenter storage demand later became visible in official Q3 FY2026 SanDisk results.",
            "metric_name": "Datacenter revenue",
            "metric_value": "Q3 FY2026 $1.467B; up 233% sequentially",
            "metric_period": "Q3 FY2026",
            "source_url": "https://investor.sandisk.com/news-releases/news-release-details/sandisk-reports-fiscal-third-quarter-2026-financial-results",
            "source_quality": "official_company_release",
            "published_at_utc": datetime(2026, 4, 30, 20, 5, tzinfo=timezone.utc),
            "available_at_utc": datetime(2026, 4, 30, 20, 5, tzinfo=timezone.utc),
            "supports_pattern": True,
            "requires_review": True,
            "review_note": "Future-only industry validation for the initial spin-off replay; not usable at first regular-way session.",
        },
    ],
    "LITE": [
        {
            "evidence_lane": "customer_demand",
            "evidence_kind": "backlog",
            "claim": "AI optical demand was visible through OCS backlog and CPO order context in official Q2 FY2026 results.",
            "metric_name": "OCS backlog",
            "metric_value": "> $400M",
            "metric_period": "Q2 FY2026",
            "source_url": "https://s21.q4cdn.com/377324469/files/doc_news/Lumentum-Announces-Second-Quarter-of-Fiscal-Year-2026-Financial-Results-2026.pdf",
            "source_quality": "official_company_release",
            "supports_pattern": True,
            "requires_review": False,
            "review_note": "Official evidence for AI optical infrastructure demand; use only after release timestamp.",
        }
    ],
}


@dataclass(frozen=True)
class HindsightCase:
    symbol: str
    label: str
    start_date: date
    end_date: date
    theme: str
    hindsight_reason: str
    anchor_event: str
    source_url: str
    case_role: str = "watchlist"
    outcome_bucket: str = "unknown"
    control_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["start_date"] = self.start_date.isoformat()
        payload["end_date"] = self.end_date.isoformat()
        return payload


@dataclass(frozen=True)
class HindsightResult:
    scan_id: str
    symbol: str
    label: str
    scan_start: date
    scan_end: date
    price_rows: int
    start_close: float | None
    end_close: float | None
    max_close: float | None
    peak_date: date | None
    max_gain_pct: float | None
    max_drawdown_pct: float | None
    rs_percentile_start: float | None
    hindsight_score: float
    flags: dict[str, Any]
    data_quality: str
    notes: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["scan_start"] = self.scan_start.isoformat()
        payload["scan_end"] = self.scan_end.isoformat()
        payload["peak_date"] = self.peak_date.isoformat() if self.peak_date else None
        payload["flags_json"] = json.dumps(self.flags, sort_keys=True)
        return payload


@dataclass(frozen=True)
class HindsightPatternObservation:
    observation_id: str
    scan_id: str
    symbol: str
    label: str
    observation_group: str
    pattern_name: str
    observation_value: str
    status: str
    evidence: str
    source: str
    extraction_method: str
    requires_review: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HindsightEvent:
    event_id: str
    symbol: str
    label: str
    event_type: str
    event_date: date
    published_at_utc: datetime | None
    market_session: str
    source_url: str
    source_quality: str
    evidence_type: str
    evidence_summary: str
    fundamental_evidence_available_at: datetime | None
    first_tradable_date: date | None
    first_tradable_bar_policy: str
    technical_replay_as_of: date | None
    timing_status: str
    requires_review: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["event_date"] = self.event_date.isoformat()
        payload["published_at_utc"] = self.published_at_utc.isoformat() if self.published_at_utc else None
        payload["fundamental_evidence_available_at"] = (
            self.fundamental_evidence_available_at.isoformat()
            if self.fundamental_evidence_available_at
            else None
        )
        payload["first_tradable_date"] = self.first_tradable_date.isoformat() if self.first_tradable_date else None
        payload["technical_replay_as_of"] = self.technical_replay_as_of.isoformat() if self.technical_replay_as_of else None
        return payload


@dataclass(frozen=True)
class HindsightEvidenceItem:
    evidence_id: str
    event_id: str
    symbol: str
    label: str
    evidence_lane: str
    evidence_kind: str
    claim: str
    metric_name: str
    metric_value: str
    metric_period: str
    source_url: str
    source_quality: str
    published_at_utc: datetime | None
    available_at_utc: datetime | None
    replay_decision_at: datetime | None
    usable_in_replay: bool
    supports_pattern: bool
    evidence_status: str
    requires_review: bool
    review_note: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["published_at_utc"] = self.published_at_utc.isoformat() if self.published_at_utc else None
        payload["available_at_utc"] = self.available_at_utc.isoformat() if self.available_at_utc else None
        payload["replay_decision_at"] = self.replay_decision_at.isoformat() if self.replay_decision_at else None
        return payload


@dataclass(frozen=True)
class HindsightObservationLink:
    link_id: str
    observation_id: str
    symbol: str
    link_type: str
    linked_id: str
    linked_table: str
    link_role: str
    link_status: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HindsightHypothesis:
    hypothesis_id: str
    hypothesis_group: str
    hypothesis_name: str
    mechanism: str
    required_evidence: str
    required_gates: str
    promotion_status: str
    minimum_next_evidence: str
    suggested_replay_rule: str
    anti_hindsight_notes: str
    rule_version: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HindsightHypothesisCaseResult:
    result_id: str
    hypothesis_id: str
    symbol: str
    label: str
    case_role: str
    result_status: str
    linked_observation_ids_json: str
    linked_evidence_ids_json: str
    linked_gate_ids_json: str
    reason_code: str
    reason_text: str
    evaluated_as_of: date

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evaluated_as_of"] = self.evaluated_as_of.isoformat()
        return payload


@dataclass(frozen=True)
class HindsightReplayGate:
    gate_id: str
    event_id: str
    symbol: str
    label: str
    gate_group: str
    gate_name: str
    gate_status: str
    formula: str
    computed_value: str
    threshold: str
    data_used: str
    required_rows: int
    available_rows: int
    missing_detail: str
    reason: str
    asof_date: date
    first_tradable_date: date | None
    source: str
    requires_review: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["asof_date"] = self.asof_date.isoformat()
        payload["first_tradable_date"] = self.first_tradable_date.isoformat() if self.first_tradable_date else None
        return payload


def default_hindsight_cases() -> list[HindsightCase]:
    return [
        HindsightCase(
            symbol="NVDA",
            label="NVDA 2024 AI infrastructure leader",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 12, 31),
            theme="AI semiconductors",
            hindsight_reason="Study whether SectorScout-style theme strength, relative strength, and setup structure would have surfaced the 2024 leader early enough.",
            anchor_event="AI accelerator demand and repeated earnings revisions.",
            source_url="https://www.nvidia.com/en-us/data-center/",
            case_role="anchor",
            outcome_bucket="leader",
            control_reason="Anchor case for AI accelerator demand shock.",
        ),
        HindsightCase(
            symbol="MU",
            label="MU 2025-2026 AI memory cycle",
            start_date=date(2025, 1, 2),
            end_date=date(2026, 5, 29),
            theme="AI memory and HBM",
            hindsight_reason="Study whether a memory-cycle leader would be caught by theme rotation, relative strength, and fundamental acceleration signals.",
            anchor_event="AI server memory demand and high-bandwidth memory cycle.",
            source_url="https://www.micron.com/products/memory/hbm",
            case_role="downstream_node",
            outcome_bucket="leader",
            control_reason="Downstream memory node after AI accelerator demand became visible.",
        ),
        HindsightCase(
            symbol="SNDK",
            label="SNDK 2025-2026 storage cycle",
            start_date=date(2025, 2, 24),
            end_date=date(2026, 5, 29),
            theme="Storage and NAND cycle",
            hindsight_reason="Study whether a storage-cycle case would appear as external-only context first, then graduate into internal ranking after price and theme evidence improved.",
            anchor_event="Standalone SanDisk trading history and NAND/storage cycle.",
            source_url="https://www.sandisk.com/",
            case_role="downstream_node",
            outcome_bucket="leader",
            control_reason="Downstream storage node; initial replay must not splice pre-spin history.",
        ),
        HindsightCase(
            symbol="LITE",
            label="LITE 2025-2026 optical AI infrastructure",
            start_date=date(2025, 1, 2),
            end_date=date(2026, 5, 29),
            theme="Optical networking and AI infrastructure",
            hindsight_reason="Study whether optical networking beneficiaries show up through relative strength, theme breadth, and setup candidates.",
            anchor_event="AI data-center optical component demand.",
            source_url="https://www.lumentum.com/en/markets/cloud-data-center",
            case_role="downstream_node",
            outcome_bucket="leader",
            control_reason="Downstream optical networking node after AI infrastructure demand broadened.",
        ),
        HindsightCase(
            symbol="AMD",
            label="AMD 2024 AI accelerator peer control",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 12, 31),
            theme="AI semiconductors",
            hindsight_reason="Control case for a same-theme AI accelerator peer so the lab does not learn from NVDA alone.",
            anchor_event="AI accelerator peer narrative; official PIT evidence is intentionally not seeded in MVP.",
            source_url="https://www.amd.com/en/solutions/ai.html",
            case_role="peer_control",
            outcome_bucket="control",
            control_reason="Same industry pool as NVDA; requires separate PIT evidence before it can support a hypothesis.",
        ),
        HindsightCase(
            symbol="INTC",
            label="INTC 2024 semiconductor peer control",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 12, 31),
            theme="AI semiconductors",
            hindsight_reason="Negative-control case for broad semiconductor exposure without seeded AI accelerator leadership evidence.",
            anchor_event="Semiconductor peer context; official PIT demand evidence is intentionally not seeded in MVP.",
            source_url="https://www.intel.com/content/www/us/en/artificial-intelligence/overview.html",
            case_role="negative_control",
            outcome_bucket="control",
            control_reason="Same broad semiconductor population, included to expose winner-only selection bias.",
        ),
        HindsightCase(
            symbol="MRVL",
            label="MRVL 2025 AI infrastructure peer control",
            start_date=date(2025, 1, 2),
            end_date=date(2026, 5, 29),
            theme="AI networking and custom silicon",
            hindsight_reason="Peer-control case for AI networking/custom silicon so downstream hypotheses require explicit evidence.",
            anchor_event="AI infrastructure peer narrative; official PIT conversion evidence is intentionally not seeded in MVP.",
            source_url="https://www.marvell.com/solutions/artificial-intelligence.html",
            case_role="peer_control",
            outcome_bucket="control",
            control_reason="Adjacent AI infrastructure node; matrix should show evidence gaps until official evidence is linked.",
        ),
    ]


def ensure_hindsight_tables(config: SectorScoutConfig) -> None:
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS hindsight_case_studies (
                symbol VARCHAR NOT NULL,
                label VARCHAR NOT NULL,
                start_date DATE NOT NULL,
                end_date DATE NOT NULL,
                theme VARCHAR NOT NULL,
                hindsight_reason VARCHAR NOT NULL,
                anchor_event VARCHAR NOT NULL,
                source_url VARCHAR NOT NULL,
                case_role VARCHAR NOT NULL DEFAULT 'watchlist',
                outcome_bucket VARCHAR NOT NULL DEFAULT 'unknown',
                control_reason VARCHAR NOT NULL DEFAULT '',
                created_at_utc TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (symbol, label, start_date)
            )
            """
        )
        connection.execute("ALTER TABLE hindsight_case_studies ADD COLUMN IF NOT EXISTS case_role VARCHAR DEFAULT 'watchlist'")
        connection.execute("ALTER TABLE hindsight_case_studies ADD COLUMN IF NOT EXISTS outcome_bucket VARCHAR DEFAULT 'unknown'")
        connection.execute("ALTER TABLE hindsight_case_studies ADD COLUMN IF NOT EXISTS control_reason VARCHAR DEFAULT ''")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS hindsight_scan_results (
                scan_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                label VARCHAR NOT NULL,
                scan_start DATE NOT NULL,
                scan_end DATE NOT NULL,
                price_rows INTEGER NOT NULL,
                start_close DOUBLE,
                end_close DOUBLE,
                max_close DOUBLE,
                peak_date DATE,
                max_gain_pct DOUBLE,
                max_drawdown_pct DOUBLE,
                rs_percentile_start DOUBLE,
                hindsight_score DOUBLE NOT NULL,
                flags_json VARCHAR NOT NULL,
                data_quality VARCHAR NOT NULL,
                notes VARCHAR NOT NULL,
                generated_at_utc TIMESTAMPTZ NOT NULL,
                config_hash VARCHAR NOT NULL,
                git_commit VARCHAR NOT NULL,
                data_snapshot_id VARCHAR NOT NULL,
                universe_version VARCHAR NOT NULL,
                theme_version VARCHAR NOT NULL,
                PRIMARY KEY (scan_id, symbol, label)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS hindsight_pattern_observations (
                observation_id VARCHAR NOT NULL,
                scan_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                label VARCHAR NOT NULL,
                observation_group VARCHAR NOT NULL,
                pattern_name VARCHAR NOT NULL,
                observation_value VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                evidence VARCHAR NOT NULL,
                source VARCHAR NOT NULL,
                extraction_method VARCHAR NOT NULL,
                requires_review BOOLEAN NOT NULL,
                generated_at_utc TIMESTAMPTZ NOT NULL,
                config_hash VARCHAR NOT NULL,
                git_commit VARCHAR NOT NULL,
                data_snapshot_id VARCHAR NOT NULL,
                universe_version VARCHAR NOT NULL,
                theme_version VARCHAR NOT NULL,
                PRIMARY KEY (observation_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS hindsight_event_ledger (
                event_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                label VARCHAR NOT NULL,
                event_type VARCHAR NOT NULL,
                event_date DATE NOT NULL,
                published_at_utc TIMESTAMPTZ,
                market_session VARCHAR NOT NULL,
                source_url VARCHAR NOT NULL,
                source_quality VARCHAR NOT NULL,
                evidence_type VARCHAR NOT NULL,
                evidence_summary VARCHAR NOT NULL,
                fundamental_evidence_available_at TIMESTAMPTZ,
                first_tradable_date DATE,
                first_tradable_bar_policy VARCHAR NOT NULL,
                technical_replay_as_of DATE,
                timing_status VARCHAR NOT NULL,
                requires_review BOOLEAN NOT NULL,
                generated_at_utc TIMESTAMPTZ NOT NULL,
                config_hash VARCHAR NOT NULL,
                git_commit VARCHAR NOT NULL,
                data_snapshot_id VARCHAR NOT NULL,
                universe_version VARCHAR NOT NULL,
                theme_version VARCHAR NOT NULL,
                PRIMARY KEY (event_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS hindsight_evidence_items (
                evidence_id VARCHAR NOT NULL,
                event_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                label VARCHAR NOT NULL,
                evidence_lane VARCHAR NOT NULL,
                evidence_kind VARCHAR NOT NULL,
                claim VARCHAR NOT NULL,
                metric_name VARCHAR NOT NULL,
                metric_value VARCHAR NOT NULL,
                metric_period VARCHAR NOT NULL,
                source_url VARCHAR NOT NULL,
                source_quality VARCHAR NOT NULL,
                published_at_utc TIMESTAMPTZ,
                available_at_utc TIMESTAMPTZ,
                replay_decision_at TIMESTAMPTZ,
                usable_in_replay BOOLEAN NOT NULL,
                supports_pattern BOOLEAN NOT NULL,
                evidence_status VARCHAR NOT NULL,
                requires_review BOOLEAN NOT NULL,
                review_note VARCHAR NOT NULL,
                generated_at_utc TIMESTAMPTZ NOT NULL,
                config_hash VARCHAR NOT NULL,
                git_commit VARCHAR NOT NULL,
                data_snapshot_id VARCHAR NOT NULL,
                universe_version VARCHAR NOT NULL,
                theme_version VARCHAR NOT NULL,
                PRIMARY KEY (evidence_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS hindsight_replay_gates (
                gate_id VARCHAR NOT NULL,
                event_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                label VARCHAR NOT NULL,
                gate_group VARCHAR NOT NULL,
                gate_name VARCHAR NOT NULL,
                gate_status VARCHAR NOT NULL,
                formula VARCHAR NOT NULL,
                computed_value VARCHAR NOT NULL,
                threshold VARCHAR NOT NULL,
                data_used VARCHAR NOT NULL,
                required_rows INTEGER NOT NULL,
                available_rows INTEGER NOT NULL,
                missing_detail VARCHAR NOT NULL,
                reason VARCHAR NOT NULL,
                asof_date DATE NOT NULL,
                first_tradable_date DATE,
                source VARCHAR NOT NULL,
                requires_review BOOLEAN NOT NULL,
                generated_at_utc TIMESTAMPTZ NOT NULL,
                config_hash VARCHAR NOT NULL,
                git_commit VARCHAR NOT NULL,
                data_snapshot_id VARCHAR NOT NULL,
                universe_version VARCHAR NOT NULL,
                theme_version VARCHAR NOT NULL,
                PRIMARY KEY (gate_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS hindsight_observation_links (
                link_id VARCHAR NOT NULL,
                observation_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                link_type VARCHAR NOT NULL,
                linked_id VARCHAR NOT NULL,
                linked_table VARCHAR NOT NULL,
                link_role VARCHAR NOT NULL,
                link_status VARCHAR NOT NULL,
                reason VARCHAR NOT NULL,
                generated_at_utc TIMESTAMPTZ NOT NULL,
                config_hash VARCHAR NOT NULL,
                git_commit VARCHAR NOT NULL,
                data_snapshot_id VARCHAR NOT NULL,
                universe_version VARCHAR NOT NULL,
                theme_version VARCHAR NOT NULL,
                PRIMARY KEY (link_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS hindsight_hypotheses (
                hypothesis_id VARCHAR NOT NULL,
                hypothesis_group VARCHAR NOT NULL,
                hypothesis_name VARCHAR NOT NULL,
                mechanism VARCHAR NOT NULL,
                required_evidence VARCHAR NOT NULL,
                required_gates VARCHAR NOT NULL,
                promotion_status VARCHAR NOT NULL,
                minimum_next_evidence VARCHAR NOT NULL,
                suggested_replay_rule VARCHAR NOT NULL,
                anti_hindsight_notes VARCHAR NOT NULL,
                rule_version VARCHAR NOT NULL,
                generated_at_utc TIMESTAMPTZ NOT NULL,
                config_hash VARCHAR NOT NULL,
                git_commit VARCHAR NOT NULL,
                data_snapshot_id VARCHAR NOT NULL,
                universe_version VARCHAR NOT NULL,
                theme_version VARCHAR NOT NULL,
                PRIMARY KEY (hypothesis_id, rule_version)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS hindsight_hypothesis_case_results (
                result_id VARCHAR NOT NULL,
                hypothesis_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                label VARCHAR NOT NULL,
                case_role VARCHAR NOT NULL,
                result_status VARCHAR NOT NULL,
                linked_observation_ids_json VARCHAR NOT NULL,
                linked_evidence_ids_json VARCHAR NOT NULL,
                linked_gate_ids_json VARCHAR NOT NULL,
                reason_code VARCHAR NOT NULL,
                reason_text VARCHAR NOT NULL,
                evaluated_as_of DATE NOT NULL,
                rule_version VARCHAR NOT NULL,
                generated_at_utc TIMESTAMPTZ NOT NULL,
                config_hash VARCHAR NOT NULL,
                git_commit VARCHAR NOT NULL,
                data_snapshot_id VARCHAR NOT NULL,
                universe_version VARCHAR NOT NULL,
                theme_version VARCHAR NOT NULL,
                PRIMARY KEY (result_id, rule_version)
            )
            """
        )


def load_hindsight_cases(path: Path = DEFAULT_HINDSIGHT_CASES_PATH) -> list[HindsightCase]:
    if not path.exists():
        return default_hindsight_cases()
    cases: list[HindsightCase] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            cases.append(
                HindsightCase(
                    symbol=str(row["symbol"]).upper(),
                    label=str(row["label"]),
                    start_date=date.fromisoformat(str(row["start_date"])),
                    end_date=date.fromisoformat(str(row["end_date"])),
                    theme=str(row["theme"]),
                    hindsight_reason=str(row["hindsight_reason"]),
                    anchor_event=str(row["anchor_event"]),
                    source_url=str(row.get("source_url") or ""),
                    case_role=str(row.get("case_role") or _default_case_role(str(row["symbol"]), str(row["theme"]))),
                    outcome_bucket=str(row.get("outcome_bucket") or "unknown"),
                    control_reason=str(row.get("control_reason") or ""),
                )
            )
    return cases


def write_default_hindsight_cases(path: Path = DEFAULT_HINDSIGHT_CASES_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cases = default_hindsight_cases()
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(cases[0].to_dict()))
        writer.writeheader()
        for case in cases:
            writer.writerow(case.to_dict())
    return path


def seed_hindsight_cases(config: SectorScoutConfig, path: Path = DEFAULT_HINDSIGHT_CASES_PATH) -> int:
    ensure_hindsight_tables(config)
    if not path.exists():
        write_default_hindsight_cases(path)
    cases = load_hindsight_cases(path)
    now = datetime.now(timezone.utc)
    with connect_database(config.database.path) as connection:
        for case in cases:
            connection.execute(
                """
                INSERT OR REPLACE INTO hindsight_case_studies (
                    symbol, label, start_date, end_date, theme, hindsight_reason,
                    anchor_event, source_url, case_role, outcome_bucket,
                    control_reason, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    case.symbol,
                    case.label,
                    case.start_date,
                    case.end_date,
                    case.theme,
                    case.hindsight_reason,
                    case.anchor_event,
                    case.source_url,
                    case.case_role,
                    case.outcome_bucket,
                    case.control_reason,
                    now,
                ],
            )
    return len(cases)


def build_hindsight_events_from_cases(cases: list[HindsightCase]) -> list[HindsightEvent]:
    events: list[HindsightEvent] = []
    for case in cases:
        event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "|".join(["hindsight_event", case.symbol, case.label])))
        seed = OFFICIAL_EVENT_SEEDS.get(case.symbol.upper())
        if seed:
            published_at_utc = seed.get("published_at_utc")
            events.append(
                HindsightEvent(
                    event_id=event_id,
                    symbol=case.symbol,
                    label=case.label,
                    event_type=str(seed["event_type"]),
                    event_date=seed["event_date"],
                    published_at_utc=published_at_utc,
                    market_session=str(seed["market_session"]),
                    source_url=str(seed["source_url"]),
                    source_quality=str(seed["source_quality"]),
                    evidence_type=str(seed["evidence_type"]),
                    evidence_summary=str(seed["evidence_summary"]),
                    fundamental_evidence_available_at=published_at_utc,
                    first_tradable_date=seed["first_tradable_date"],
                    first_tradable_bar_policy=str(seed["first_tradable_bar_policy"]),
                    technical_replay_as_of=seed["technical_replay_as_of"],
                    timing_status=str(seed["timing_status"]),
                    requires_review=bool(seed["requires_review"]),
                )
            )
            continue
        events.append(
            HindsightEvent(
                event_id=event_id,
                symbol=case.symbol,
                label=case.label,
                event_type=_event_type_from_theme(case.theme),
                event_date=case.start_date,
                published_at_utc=None,
                market_session="date_only_ambiguous",
                source_url=case.source_url,
                source_quality="case_metadata_needs_source_review",
                evidence_type="theme_or_catalyst_seed",
                evidence_summary=_case_seed_summary(case),
                fundamental_evidence_available_at=None,
                first_tradable_date=None,
                first_tradable_bar_policy="unresolved_until_timestamp_reviewed",
                technical_replay_as_of=case.start_date,
                timing_status="DATA_GAP",
                requires_review=True,
            )
        )
    return events


def seed_hindsight_events(config: SectorScoutConfig, path: Path = DEFAULT_HINDSIGHT_CASES_PATH) -> int:
    ensure_hindsight_tables(config)
    events = build_hindsight_events_from_cases(load_hindsight_cases(path))
    _persist_hindsight_events(config, events)
    return len(events)


def latest_hindsight_events(config: SectorScoutConfig, *, limit: int = 100) -> pd.DataFrame:
    ensure_hindsight_tables(config)
    with connect_database(config.database.path) as connection:
        return connection.execute(
            """
            SELECT *
            FROM hindsight_event_ledger
            ORDER BY symbol, event_date, event_type
            LIMIT ?
            """,
            [limit],
        ).fetchdf()


def build_hindsight_evidence_from_events(events: pd.DataFrame) -> list[HindsightEvidenceItem]:
    items: list[HindsightEvidenceItem] = []
    for _, row in events.iterrows():
        event = _event_from_row(row)
        replay_decision_at = _replay_decision_at(event)
        seeds = OFFICIAL_EVIDENCE_SEEDS.get(event.symbol)
        if not seeds:
            items.append(_fallback_evidence_item(event, replay_decision_at))
            continue
        for index, seed in enumerate(seeds):
            published_at = seed.get("published_at_utc") or event.published_at_utc
            available_at = seed.get("available_at_utc") or published_at
            usable = bool(available_at and replay_decision_at and available_at <= replay_decision_at)
            status = _evidence_status(available_at, replay_decision_at, seed_requires_review=bool(seed["requires_review"]))
            evidence_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    "|".join(["hindsight_evidence", event.event_id, event.symbol, str(index), str(seed["evidence_kind"])]),
                )
            )
            items.append(
                HindsightEvidenceItem(
                    evidence_id=evidence_id,
                    event_id=event.event_id,
                    symbol=event.symbol,
                    label=event.label,
                    evidence_lane=str(seed["evidence_lane"]),
                    evidence_kind=str(seed["evidence_kind"]),
                    claim=str(seed["claim"]),
                    metric_name=str(seed["metric_name"]),
                    metric_value=str(seed["metric_value"]),
                    metric_period=str(seed["metric_period"]),
                    source_url=str(seed["source_url"]),
                    source_quality=str(seed["source_quality"]),
                    published_at_utc=published_at,
                    available_at_utc=available_at,
                    replay_decision_at=replay_decision_at,
                    usable_in_replay=usable,
                    supports_pattern=bool(seed["supports_pattern"]),
                    evidence_status=status,
                    requires_review=bool(seed["requires_review"]) or status != "PASS",
                    review_note=str(seed["review_note"]),
                )
            )
    return items


def seed_hindsight_evidence(config: SectorScoutConfig, path: Path = DEFAULT_HINDSIGHT_CASES_PATH) -> int:
    ensure_hindsight_tables(config)
    events = latest_hindsight_events(config)
    if events.empty:
        seed_hindsight_events(config, path)
        events = latest_hindsight_events(config)
    evidence = build_hindsight_evidence_from_events(events)
    _persist_hindsight_evidence(config, evidence)
    return len(evidence)


def latest_hindsight_evidence(config: SectorScoutConfig, *, limit: int = 500) -> pd.DataFrame:
    ensure_hindsight_tables(config)
    with connect_database(config.database.path) as connection:
        return connection.execute(
            """
            SELECT *
            FROM hindsight_evidence_items
            ORDER BY symbol, evidence_lane, evidence_kind, metric_name
            LIMIT ?
            """,
            [limit],
        ).fetchdf()


def build_hindsight_replay_gates(
    config: SectorScoutConfig,
    *,
    path: Path = DEFAULT_HINDSIGHT_CASES_PATH,
    persist: bool = True,
    asof_date: date | None = None,
) -> list[HindsightReplayGate]:
    ensure_hindsight_tables(config)
    cases = load_hindsight_cases(path)
    case_by_symbol = {case.symbol: case for case in cases}
    events = latest_hindsight_events(config)
    if events.empty:
        seed_hindsight_events(config, path)
        events = latest_hindsight_events(config)
    gates: list[HindsightReplayGate] = []
    effective_asof = asof_date or date.today()
    for _, event_row in events.iterrows():
        symbol = str(event_row["symbol"]).upper()
        case = case_by_symbol.get(symbol)
        if case is None:
            continue
        event = _event_from_row(event_row)
        gates.extend(_event_timing_gates(event, case, asof_date=effective_asof))
        gates.extend(_pre_event_technical_gates(config, event, case, asof_date=effective_asof))
        gates.extend(_first_tradable_gates(config, event, case, asof_date=effective_asof))
    if persist:
        _persist_hindsight_replay_gates(config, gates)
    return gates


def latest_hindsight_replay_gates(config: SectorScoutConfig, *, limit: int = 500) -> pd.DataFrame:
    ensure_hindsight_tables(config)
    with connect_database(config.database.path) as connection:
        return connection.execute(
            """
            SELECT *
            FROM hindsight_replay_gates
            QUALIFY dense_rank() OVER (ORDER BY generated_at_utc DESC) = 1
            ORDER BY symbol, gate_group, gate_name
            LIMIT ?
            """,
            [limit],
        ).fetchdf()


def build_hindsight_observation_links(config: SectorScoutConfig) -> list[HindsightObservationLink]:
    ensure_hindsight_tables(config)
    observations = latest_hindsight_pattern_observations(config)
    if observations.empty:
        return []
    evidence = latest_hindsight_evidence(config)
    gates = latest_hindsight_replay_gates(config)
    links: list[HindsightObservationLink] = []
    for _, observation in observations.iterrows():
        links.extend(_links_for_observation(observation, evidence, gates))
    _persist_hindsight_observation_links(config, links)
    return links


def latest_hindsight_observation_links(config: SectorScoutConfig, *, limit: int = 1000) -> pd.DataFrame:
    ensure_hindsight_tables(config)
    with connect_database(config.database.path) as connection:
        return connection.execute(
            """
            SELECT *
            FROM hindsight_observation_links
            QUALIFY dense_rank() OVER (ORDER BY generated_at_utc DESC) = 1
            ORDER BY symbol, observation_id, link_type, link_role
            LIMIT ?
            """,
            [limit],
        ).fetchdf()


def build_hindsight_hypothesis_registry(
    config: SectorScoutConfig,
    *,
    path: Path = DEFAULT_HINDSIGHT_CASES_PATH,
    persist: bool = True,
    asof_date: date | None = None,
) -> tuple[list[HindsightHypothesis], list[HindsightHypothesisCaseResult]]:
    ensure_hindsight_tables(config)
    cases = load_hindsight_cases(path)
    observations = latest_hindsight_pattern_observations(config)
    evidence = latest_hindsight_evidence(config)
    gates = latest_hindsight_replay_gates(config)
    links = latest_hindsight_observation_links(config)
    if links.empty and not observations.empty:
        build_hindsight_observation_links(config)
        links = latest_hindsight_observation_links(config)
    evaluated_as_of = asof_date or date.today()
    case_results: list[HindsightHypothesisCaseResult] = []
    base_hypotheses = _base_hindsight_hypotheses()
    for hypothesis in base_hypotheses:
        for case in cases:
            case_results.append(
                _evaluate_hypothesis_case(
                    hypothesis,
                    case,
                    observations=observations,
                    evidence=evidence,
                    gates=gates,
                    links=links,
                    evaluated_as_of=evaluated_as_of,
                )
            )
    hypotheses = [
        _hypothesis_with_status(
            hypothesis,
            [result for result in case_results if result.hypothesis_id == hypothesis.hypothesis_id],
        )
        for hypothesis in base_hypotheses
    ]
    if persist:
        _persist_hindsight_hypothesis_registry(config, hypotheses, case_results)
    return hypotheses, case_results


def latest_hindsight_hypotheses(config: SectorScoutConfig, *, limit: int = 100) -> pd.DataFrame:
    ensure_hindsight_tables(config)
    with connect_database(config.database.path) as connection:
        return connection.execute(
            """
            SELECT *
            FROM hindsight_hypotheses
            QUALIFY dense_rank() OVER (ORDER BY generated_at_utc DESC) = 1
            ORDER BY hypothesis_group, hypothesis_name
            LIMIT ?
            """,
            [limit],
        ).fetchdf()


def latest_hindsight_hypothesis_case_results(config: SectorScoutConfig, *, limit: int = 1000) -> pd.DataFrame:
    ensure_hindsight_tables(config)
    with connect_database(config.database.path) as connection:
        return connection.execute(
            """
            SELECT *
            FROM hindsight_hypothesis_case_results
            QUALIFY dense_rank() OVER (ORDER BY generated_at_utc DESC) = 1
            ORDER BY hypothesis_id, symbol
            LIMIT ?
            """,
            [limit],
        ).fetchdf()


def fetch_hindsight_prices(
    config: SectorScoutConfig,
    *,
    path: Path = DEFAULT_HINDSIGHT_CASES_PATH,
    provider: str = "yahoo_chart_public",
    lookback_days: int = 320,
    timeout: int = 20,
) -> list[dict[str, Any]]:
    cases = load_hindsight_cases(path)
    summaries: list[dict[str, Any]] = []
    for case in cases:
        fetch_start = case.start_date - timedelta(days=max(0, lookback_days))
        if provider == "stooq_public":
            rows = fetch_stooq_daily_rows(case.symbol, fetch_start, case.end_date, timeout=timeout)
        elif provider == "yahoo_chart_public":
            rows = fetch_yahoo_chart_daily_rows(case.symbol, fetch_start, case.end_date, timeout=timeout)
        else:
            raise ValueError(f"Unsupported hindsight price provider: {provider}")
        inserted = _insert_price_rows(config, case.symbol, rows, provider=provider)
        summaries.append(
            {
                "symbol": case.symbol,
                "label": case.label,
                "start_date": case.start_date.isoformat(),
                "end_date": case.end_date.isoformat(),
                "fetch_start": fetch_start.isoformat(),
                "lookback_days": max(0, lookback_days),
                "provider": provider,
                "rows_fetched": len(rows),
                "rows_inserted": inserted,
                "data_quality_note": (
                    "Public daily price rows loaded with pre-event lookback; corporate-action adjustment status is provider-dependent."
                    if inserted
                    else "No public daily price rows returned for this case window."
                ),
            }
        )
    return summaries


def fetch_stooq_daily_rows(symbol: str, start: date, end: date, *, timeout: int = 20) -> list[dict[str, Any]]:
    params = {
        "s": f"{symbol.lower()}.us",
        "d1": start.strftime("%Y%m%d"),
        "d2": end.strftime("%Y%m%d"),
        "i": "d",
    }
    if api_key := os.environ.get("STOOQ_API_KEY"):
        params["apikey"] = api_key
    query = urlencode(params)
    url = f"https://stooq.com/q/d/l/?{query}"
    with urlopen(url, timeout=timeout) as response:  # nosec B310 - public CSV endpoint, no credentials.
        text = response.read().decode("utf-8")
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2 or lines[0].lower().startswith("no data"):
        return []
    parsed: list[dict[str, Any]] = []
    for row in csv.DictReader(lines):
        if not row.get("Date"):
            continue
        parsed.append(
            {
                "price_date": date.fromisoformat(row["Date"]),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": int(float(row["Volume"])),
            }
        )
    return parsed


def fetch_yahoo_chart_daily_rows(symbol: str, start: date, end: date, *, timeout: int = 20) -> list[dict[str, Any]]:
    period1 = int(datetime.combine(start, time.min, tzinfo=timezone.utc).timestamp())
    period2 = int(datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc).timestamp())
    query = urlencode({"period1": period1, "period2": period2, "interval": "1d", "events": "history"})
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol.upper()}?{query}"
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 SectorScout/0.1"})
    with urlopen(request, timeout=timeout) as response:  # nosec B310 - public chart JSON endpoint, no credentials.
        payload = json.loads(response.read().decode("utf-8"))
    result = (payload.get("chart", {}).get("result") or [None])[0]
    if not result:
        return []
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    parsed: list[dict[str, Any]] = []
    for index, timestamp in enumerate(timestamps):
        try:
            open_price = quote.get("open", [])[index]
            high = quote.get("high", [])[index]
            low = quote.get("low", [])[index]
            close = quote.get("close", [])[index]
            volume = quote.get("volume", [])[index]
        except IndexError:
            continue
        if any(value is None for value in [open_price, high, low, close, volume]):
            continue
        parsed.append(
            {
                "price_date": datetime.fromtimestamp(int(timestamp), tz=timezone.utc).date(),
                "open": float(open_price),
                "high": float(high),
                "low": float(low),
                "close": float(close),
                "volume": int(volume),
            }
        )
    return parsed


def scan_hindsight_cases(
    config: SectorScoutConfig,
    *,
    path: Path = DEFAULT_HINDSIGHT_CASES_PATH,
    persist: bool = True,
) -> list[HindsightResult]:
    ensure_hindsight_tables(config)
    cases = load_hindsight_cases(path)
    scan_id = str(uuid.uuid4())
    results = [_scan_case(config, case, scan_id=scan_id) for case in cases]
    if persist:
        _persist_results(config, results)
        _persist_pattern_observations(config, build_hindsight_pattern_observations(cases, results))
        seed_hindsight_events(config, path)
        seed_hindsight_evidence(config, path)
        build_hindsight_replay_gates(config, path=path, persist=True)
        build_hindsight_observation_links(config)
        build_hindsight_hypothesis_registry(config, path=path, persist=True)
    return results


def _insert_price_rows(
    config: SectorScoutConfig,
    symbol: str,
    rows: list[dict[str, Any]],
    *,
    provider: str,
) -> int:
    if not rows:
        return 0
    now = datetime.now(timezone.utc)
    with connect_database(config.database.path) as connection:
        for row in rows:
            connection.execute(
                """
                INSERT OR REPLACE INTO daily_prices (
                    symbol, price_date, open, high, low, close, volume,
                    adj_open, adj_high, adj_low, adj_close, adj_volume,
                    provider, is_adjusted, adjustment_warning, ingested_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    symbol.upper(),
                    row["price_date"],
                    row["open"],
                    row["high"],
                    row["low"],
                    row["close"],
                    row["volume"],
                    row["open"],
                    row["high"],
                    row["low"],
                    row["close"],
                    row["volume"],
                    provider,
                    False,
                    True,
                    now,
                ],
            )
    return len(rows)


def latest_hindsight_results(config: SectorScoutConfig, *, limit: int = 100) -> pd.DataFrame:
    ensure_hindsight_tables(config)
    with connect_database(config.database.path) as connection:
        return connection.execute(
            """
            SELECT *
            FROM hindsight_scan_results
            QUALIFY dense_rank() OVER (ORDER BY generated_at_utc DESC, scan_id DESC) = 1
            LIMIT ?
            """,
            [limit],
        ).fetchdf()


def latest_hindsight_pattern_observations(config: SectorScoutConfig, *, limit: int = 500) -> pd.DataFrame:
    ensure_hindsight_tables(config)
    with connect_database(config.database.path) as connection:
        return connection.execute(
            """
            SELECT *
            FROM hindsight_pattern_observations
            QUALIFY dense_rank() OVER (ORDER BY generated_at_utc DESC, scan_id DESC) = 1
            LIMIT ?
            """,
            [limit],
        ).fetchdf()


def build_hindsight_pattern_observations(
    cases: list[HindsightCase],
    results: list[HindsightResult] | pd.DataFrame,
) -> list[HindsightPatternObservation]:
    result_rows = _normalize_result_rows(results)
    result_by_symbol = {str(row["symbol"]).upper(): row for row in result_rows}
    observations: list[HindsightPatternObservation] = []
    for case in cases:
        row = result_by_symbol.get(case.symbol)
        scan_id = str(row.get("scan_id") if row else "unscanned")
        observations.extend(_industry_observations(case, scan_id=scan_id))
        observations.extend(_manual_required_observations(case, scan_id=scan_id))
        observations.extend(_technical_observations(case, row, scan_id=scan_id))
    return observations


def historical_pattern_summary(
    cases: list[HindsightCase],
    results: list[HindsightResult] | pd.DataFrame,
) -> dict[str, list[dict[str, Any]]]:
    result_rows = _normalize_result_rows(results)
    result_by_symbol = {str(row["symbol"]).upper(): row for row in result_rows}
    industry_rows: list[dict[str, Any]] = []
    by_theme: dict[str, list[HindsightCase]] = {}
    for case in cases:
        by_theme.setdefault(case.theme, []).append(case)
    for theme, theme_cases in sorted(by_theme.items()):
        matched_results = [result_by_symbol.get(case.symbol) for case in theme_cases]
        usable_results = [row for row in matched_results if row and row.get("data_quality") != "missing_price_history"]
        industry_rows.append(
            {
                "Industry / theme pattern": theme,
                "Symbols": ", ".join(case.symbol for case in theme_cases),
                "Case roles": ", ".join(f"{case.symbol}:{_case_role(case)}" for case in theme_cases),
                "What to study": "; ".join(sorted({case.anchor_event for case in theme_cases})),
                "Cases with price data": len(usable_results),
                "Status": "ready_to_analyze" if usable_results else "needs_historical_data",
            }
        )
    technical_checks = {
        "High relative strength near start": "rs_80_at_start",
        "Stage 2 trend proxy": "stage2_proxy",
        "Breakout / new-high proxy": "breakout_proxy",
        "Volume expansion proxy": "volume_expansion_proxy",
        "Large post-start advance": "gain_100pct",
    }
    technical_rows: list[dict[str, Any]] = []
    usable = [row for row in result_rows if row.get("data_quality") != "missing_price_history"]
    for label, flag in technical_checks.items():
        true_count = sum(1 for row in usable if _flags_from_row(row).get(flag))
        technical_rows.append(
            {
                "Technical pattern to test": label,
                "Cases matching": true_count,
                "Cases with price data": len(usable),
                "Status": "needs_historical_data" if not usable else "ready_to_compare",
            }
        )
    return {"industry_patterns": industry_rows, "technical_patterns": technical_rows}


def _scan_case(config: SectorScoutConfig, case: HindsightCase, *, scan_id: str) -> HindsightResult:
    prices = _price_rows(config, case)
    rs_start = _rs_percentile_near_start(config, case)
    if prices.empty:
        return HindsightResult(
            scan_id=scan_id,
            symbol=case.symbol,
            label=case.label,
            scan_start=case.start_date,
            scan_end=case.end_date,
            price_rows=0,
            start_close=None,
            end_close=None,
            max_close=None,
            peak_date=None,
            max_gain_pct=None,
            max_drawdown_pct=None,
            rs_percentile_start=rs_start,
            hindsight_score=0.0,
            flags={"has_price_history": False, "requires_data_load": True},
            data_quality="missing_price_history",
            notes="No price rows found for this case window. Load historical prices before interpreting the case.",
        )
    prices = prices.sort_values("price_date").reset_index(drop=True)
    start_close = float(prices.iloc[0]["adj_close"])
    end_close = float(prices.iloc[-1]["adj_close"])
    peak_index = int(prices["adj_close"].idxmax())
    max_close = float(prices.loc[peak_index, "adj_close"])
    peak_date = prices.loc[peak_index, "price_date"]
    peak_date = peak_date.date() if hasattr(peak_date, "date") else date.fromisoformat(str(peak_date)[:10])
    max_gain_pct = ((max_close / start_close) - 1.0) * 100 if start_close else None
    max_drawdown_pct = _max_drawdown_pct(prices["adj_close"])
    flags = _case_flags(prices, max_gain_pct=max_gain_pct, rs_percentile_start=rs_start)
    score = _hindsight_score(flags)
    return HindsightResult(
        scan_id=scan_id,
        symbol=case.symbol,
        label=case.label,
        scan_start=case.start_date,
        scan_end=case.end_date,
        price_rows=len(prices),
        start_close=start_close,
        end_close=end_close,
        max_close=max_close,
        peak_date=peak_date,
        max_gain_pct=max_gain_pct,
        max_drawdown_pct=max_drawdown_pct,
        rs_percentile_start=rs_start,
        hindsight_score=score,
        flags=flags,
        data_quality="ok" if len(prices) >= 60 else "thin_price_history",
        notes=_case_notes(flags),
    )


def _normalize_result_rows(results: list[HindsightResult] | pd.DataFrame) -> list[dict[str, Any]]:
    if isinstance(results, pd.DataFrame):
        return [row.to_dict() for _, row in results.iterrows()]
    return [result.to_dict() for result in results]


def _flags_from_row(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("flags") or row.get("flags_json") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw))
    except Exception:
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def _industry_observations(case: HindsightCase, *, scan_id: str) -> list[HindsightPatternObservation]:
    cluster = _industry_cluster(case.theme)
    return [
        _observation(
            scan_id=scan_id,
            case=case,
            observation_group="industry",
            pattern_name="Theme taxonomy seed",
            observation_value=case.theme,
            status="hypothesis_seed",
            evidence="Case metadata theme. Review whether this theme label was knowable at the study date.",
            source=case.source_url,
            extraction_method="case_metadata_manual",
            requires_review=True,
        ),
        _observation(
            scan_id=scan_id,
            case=case,
            observation_group="industry",
            pattern_name="Industry cluster",
            observation_value=cluster,
            status="hypothesis_seed",
            evidence=f"Theme '{case.theme}' mapped to cluster '{cluster}'.",
            source=case.source_url,
            extraction_method="keyword_theme_mapping_v1",
            requires_review=True,
        ),
    ]


def _manual_required_observations(case: HindsightCase, *, scan_id: str) -> list[HindsightPatternObservation]:
    return [
        _observation(
            scan_id=scan_id,
            case=case,
            observation_group="manual_or_llm_required",
            pattern_name="Catalyst narrative",
            observation_value=case.anchor_event,
            status="needs_manual_review",
            evidence="Catalyst context is narrative and source-dependent; it should not be inferred from OHLCV alone.",
            source=case.source_url,
            extraction_method="case_metadata_manual",
            requires_review=True,
        ),
        _observation(
            scan_id=scan_id,
            case=case,
            observation_group="manual_or_llm_required",
            pattern_name="Point-in-time theme discoverability",
            observation_value="unknown_until_reviewed",
            status="needs_manual_review",
            evidence="Reviewer must decide whether the theme was discoverable from public information before or during the case window.",
            source=case.source_url,
            extraction_method="review_required",
            requires_review=True,
        ),
    ]


def _technical_observations(
    case: HindsightCase,
    row: dict[str, Any] | None,
    *,
    scan_id: str,
) -> list[HindsightPatternObservation]:
    flags = _flags_from_row(row or {})
    if not row or row.get("data_quality") == "missing_price_history":
        return [
            _observation(
                scan_id=scan_id,
                case=case,
                observation_group="technical",
                pattern_name="OHLCV coverage",
                observation_value="missing",
                status="needs_historical_data",
                evidence="No price rows are loaded for the case window.",
                source="daily_prices",
                extraction_method="ohlcv_coverage_check",
                requires_review=False,
            )
        ]
    enough_history = bool(flags.get("enough_history_60d"))
    return [
        _technical_flag_observation(
            case,
            row,
            scan_id=scan_id,
            pattern_name="Relative strength near case start",
            flag_name="rs_80_at_start",
            observed_value=_format_metric(row.get("rs_percentile_start"), suffix=" percentile"),
            observed_evidence="Latest technical indicator before or at the case start has RS percentile >= 80.",
            missing_evidence="RS percentile is missing or below the study threshold near case start.",
            requires_review=row.get("rs_percentile_start") is None,
        ),
        _technical_flag_observation(
            case,
            row,
            scan_id=scan_id,
            pattern_name="Stage 2 trend proxy",
            flag_name="stage2_proxy",
            observed_value=str(flags.get("stage2_days_last_30", 0)) + " of last 30 sessions",
            observed_evidence="Close was above both 50-day and 200-day moving averages during the final 30 sessions of the case window.",
            missing_evidence="Trend proxy was not observed, or price history is too thin for this check.",
            force_needs_data=not enough_history,
        ),
        _technical_flag_observation(
            case,
            row,
            scan_id=scan_id,
            pattern_name="New-high / breakout proxy",
            flag_name="breakout_proxy",
            observed_value=str(flags.get("breakout_days", 0)) + " breakout proxy days",
            observed_evidence="Close exceeded the prior 20-session high at least once after the initial 20 sessions.",
            missing_evidence="No prior-20-session closing high proxy was observed in the loaded window.",
            force_needs_data=not flags.get("has_price_history"),
        ),
        _technical_flag_observation(
            case,
            row,
            scan_id=scan_id,
            pattern_name="Volume expansion proxy",
            flag_name="volume_expansion_proxy",
            observed_value=str(flags.get("volume_expansion_days", 0)) + " expansion proxy days",
            observed_evidence="Volume exceeded 1.3x its 50-session average at least once after the initial window.",
            missing_evidence="No volume expansion proxy was observed in the loaded window.",
            force_needs_data=not enough_history,
        ),
        _observation(
            scan_id=scan_id,
            case=case,
            observation_group="technical",
            pattern_name="Case-window outcome descriptor",
            observation_value=_format_metric(row.get("max_gain_pct"), suffix="% largest advance"),
            status="outcome_only_not_predictive",
            evidence="This describes what happened inside the selected case window. It is not a screening rule.",
            source="daily_prices",
            extraction_method="case_window_path_summary",
            requires_review=True,
        ),
    ]


def _technical_flag_observation(
    case: HindsightCase,
    row: dict[str, Any],
    *,
    scan_id: str,
    pattern_name: str,
    flag_name: str,
    observed_value: str,
    observed_evidence: str,
    missing_evidence: str,
    force_needs_data: bool = False,
    requires_review: bool = False,
) -> HindsightPatternObservation:
    flags = _flags_from_row(row)
    if force_needs_data:
        status = "needs_historical_data"
        value = "insufficient_history"
        evidence = missing_evidence
    elif flags.get(flag_name):
        status = "observed_hypothesis_feature"
        value = observed_value
        evidence = observed_evidence
    else:
        status = "not_observed_in_case_window"
        value = "not_observed"
        evidence = missing_evidence
    return _observation(
        scan_id=scan_id,
        case=case,
        observation_group="technical",
        pattern_name=pattern_name,
        observation_value=value,
        status=status,
        evidence=evidence,
        source="daily_prices",
        extraction_method="daily_ohlcv_rule_v1",
        requires_review=requires_review,
    )


def _links_for_observation(
    observation: pd.Series,
    evidence: pd.DataFrame,
    gates: pd.DataFrame,
) -> list[HindsightObservationLink]:
    group = str(observation.get("observation_group") or "")
    if group == "industry":
        return _industry_observation_links(observation, evidence)
    if group == "technical":
        return _technical_observation_links(observation, gates)
    return [_review_required_link(observation, "Manual or narrative observation needs official evidence or computed gates.")]


def _industry_observation_links(observation: pd.Series, evidence: pd.DataFrame) -> list[HindsightObservationLink]:
    symbol = str(observation.get("symbol") or "")
    if evidence.empty:
        return [_review_required_link(observation, "No official evidence rows are available for this industry observation.")]
    symbol_evidence = evidence[evidence["symbol"].astype(str) == symbol]
    usable = symbol_evidence[symbol_evidence["usable_in_replay"].astype(bool)] if not symbol_evidence.empty else symbol_evidence
    if usable.empty:
        return [_review_required_link(observation, "No usable point-in-time official evidence is linked.")]
    return [
        _observation_link(
            observation=observation,
            link_type="evidence",
            linked_id=str(row["evidence_id"]),
            linked_table="hindsight_evidence_items",
            link_role="supports" if bool(row.get("supports_pattern")) else "context",
            link_status=str(row.get("evidence_status") or ""),
            reason=f"{row.get('evidence_kind')} evidence was usable at the replay decision time.",
        )
        for _, row in usable.iterrows()
    ]


def _technical_observation_links(observation: pd.Series, gates: pd.DataFrame) -> list[HindsightObservationLink]:
    symbol = str(observation.get("symbol") or "")
    pattern_name = str(observation.get("pattern_name") or "")
    if gates.empty:
        return [_review_required_link(observation, "No computed replay gates are available for this technical observation.")]
    symbol_gates = gates[gates["symbol"].astype(str) == symbol]
    candidates = _matching_gates_for_pattern(pattern_name, symbol_gates)
    if candidates.empty:
        return [_review_required_link(observation, "No matching computed gate is available for this technical observation.")]
    return [
        _observation_link(
            observation=observation,
            link_type="gate",
            linked_id=str(row["gate_id"]),
            linked_table="hindsight_replay_gates",
            link_role="supports" if row.get("gate_status") == "PASS" else "blocks",
            link_status=str(row.get("gate_status") or ""),
            reason=f"{row.get('gate_name')} is the computed replay gate for this observation.",
        )
        for _, row in candidates.iterrows()
    ]


def _matching_gates_for_pattern(pattern_name: str, gates: pd.DataFrame) -> pd.DataFrame:
    if gates.empty:
        return gates
    mapping = {
        "Stage 2 trend proxy": "Stage 2 trend explain",
        "Relative strength near case start": "Benchmark RS",
        "OHLCV coverage": "Pre-event price coverage",
    }
    gate_match = mapping.get(pattern_name)
    if not gate_match:
        return gates.iloc[0:0]
    if gate_match == "Benchmark RS":
        return gates[gates["gate_name"].astype(str).str.startswith("Benchmark RS")]
    return gates[gates["gate_name"] == gate_match]


def _review_required_link(observation: pd.Series, reason: str) -> HindsightObservationLink:
    return _observation_link(
        observation=observation,
        link_type="review_required",
        linked_id=str(observation.get("observation_id") or ""),
        linked_table="hindsight_pattern_observations",
        link_role="needs_review",
        link_status="REQUIRES_REVIEW",
        reason=reason,
    )


def _observation_link(
    *,
    observation: pd.Series,
    link_type: str,
    linked_id: str,
    linked_table: str,
    link_role: str,
    link_status: str,
    reason: str,
) -> HindsightObservationLink:
    observation_id = str(observation.get("observation_id") or "")
    symbol = str(observation.get("symbol") or "")
    link_key = "|".join([observation_id, link_type, linked_table, linked_id, link_role])
    return HindsightObservationLink(
        link_id=str(uuid.uuid5(uuid.NAMESPACE_URL, link_key)),
        observation_id=observation_id,
        symbol=symbol,
        link_type=link_type,
        linked_id=linked_id,
        linked_table=linked_table,
        link_role=link_role,
        link_status=link_status,
        reason=reason,
    )


def _base_hindsight_hypotheses() -> list[HindsightHypothesis]:
    return [
        _hypothesis(
            hypothesis_group="industry",
            hypothesis_name="H1 - Anchor demand shock activates downstream watchlist",
            mechanism=(
                "An official anchor-company AI infrastructure demand shock creates a theme map for downstream "
                "suppliers to monitor, without claiming those suppliers are technically ready."
            ),
            required_evidence="Official anchor customer-demand evidence with evidence_status PASS and usable_in_replay true.",
            required_gates="No downstream technical gate required for this industry watchlist hypothesis.",
            minimum_next_evidence="Add independent downstream cases and negative controls before replay design.",
            suggested_replay_rule="When anchor demand evidence is PIT-valid, create a downstream watchlist only; do not modify scores.",
            anti_hindsight_notes="Anchor evidence cannot prove downstream conversion or technical readiness.",
        ),
        _hypothesis(
            hypothesis_group="industry",
            hypothesis_name="H2 - Downstream revenue conversion",
            mechanism=(
                "A downstream node shows official revenue, backlog, order, or guidance evidence tied to the same "
                "industry demand chain after the anchor theme appears."
            ),
            required_evidence=(
                "Official downstream customer-demand evidence with evidence_status PASS and usable_in_replay true; "
                "spin-off/listing evidence is context only."
            ),
            required_gates="No technical gate required; this is an industry evidence hypothesis.",
            minimum_next_evidence="Add source-labeled downstream revenue/backlog/order evidence and one or more non-leader controls.",
            suggested_replay_rule="Require PIT-valid downstream demand evidence before elevating a supplier from watchlist to replay candidate.",
            anti_hindsight_notes="Narrative AI labels and spin-off evidence cannot satisfy downstream demand conversion.",
        ),
        _hypothesis(
            hypothesis_group="composite",
            hypothesis_name="H3 - Industry evidence plus pre-event technical strength",
            mechanism=(
                "Official industry evidence and pre-event technical strength appear together before the first tradable "
                "or replay decision point."
            ),
            required_evidence="Official customer-demand evidence with evidence_status PASS and usable_in_replay true.",
            required_gates="Pre-event price coverage PASS, Stage 2 trend PASS, and fixed benchmark RS PASS.",
            minimum_next_evidence="Load benchmark coverage and pre-event OHLCV before interpreting this as a replay candidate.",
            suggested_replay_rule="Only test a replay rule when both PIT industry evidence and required technical gates are auditable.",
            anti_hindsight_notes="Post-event price path and later validation gates cannot confirm pre-event setup readiness.",
        ),
        _hypothesis(
            hypothesis_group="composite",
            hypothesis_name="H4 - Industry-only but technical not confirmed",
            mechanism=(
                "Official industry evidence exists, but at least one required pre-event technical gate fails. "
                "This creates a mixed analog bucket rather than a replay-ready candidate."
            ),
            required_evidence="Official customer-demand evidence with evidence_status PASS and usable_in_replay true.",
            required_gates="At least one required pre-event technical gate must be FAIL; DATA_GAP is not a failed gate.",
            minimum_next_evidence="Use this bucket only after required technical coverage is complete.",
            suggested_replay_rule="Keep as mixed analog or negative-control candidate; do not treat industry evidence alone as setup readiness.",
            anti_hindsight_notes="DATA_GAP must remain missing evidence, not a failed technical pattern.",
        ),
    ]


def _hypothesis(
    *,
    hypothesis_group: str,
    hypothesis_name: str,
    mechanism: str,
    required_evidence: str,
    required_gates: str,
    minimum_next_evidence: str,
    suggested_replay_rule: str,
    anti_hindsight_notes: str,
) -> HindsightHypothesis:
    hypothesis_id = str(
        uuid.uuid5(uuid.NAMESPACE_URL, "|".join([HINDSIGHT_HYPOTHESIS_RULE_VERSION, hypothesis_name]))
    )
    return HindsightHypothesis(
        hypothesis_id=hypothesis_id,
        hypothesis_group=hypothesis_group,
        hypothesis_name=hypothesis_name,
        mechanism=mechanism,
        required_evidence=required_evidence,
        required_gates=required_gates,
        promotion_status="DRAFT",
        minimum_next_evidence=minimum_next_evidence,
        suggested_replay_rule=suggested_replay_rule,
        anti_hindsight_notes=anti_hindsight_notes,
        rule_version=HINDSIGHT_HYPOTHESIS_RULE_VERSION,
    )


def _hypothesis_with_status(
    hypothesis: HindsightHypothesis,
    case_results: list[HindsightHypothesisCaseResult],
) -> HindsightHypothesis:
    status = _hypothesis_promotion_status(hypothesis, case_results)
    return HindsightHypothesis(
        hypothesis_id=hypothesis.hypothesis_id,
        hypothesis_group=hypothesis.hypothesis_group,
        hypothesis_name=hypothesis.hypothesis_name,
        mechanism=hypothesis.mechanism,
        required_evidence=hypothesis.required_evidence,
        required_gates=hypothesis.required_gates,
        promotion_status=status,
        minimum_next_evidence=_hypothesis_next_evidence(status, hypothesis.minimum_next_evidence),
        suggested_replay_rule=hypothesis.suggested_replay_rule,
        anti_hindsight_notes=hypothesis.anti_hindsight_notes,
        rule_version=hypothesis.rule_version,
    )


def _hypothesis_promotion_status(
    hypothesis: HindsightHypothesis,
    case_results: list[HindsightHypothesisCaseResult],
) -> str:
    controls = [result for result in case_results if result.case_role in {"negative_control", "peer_control"}]
    if any(result.result_status == "SUPPORTS" for result in controls):
        return "CONTROL_SUPPORT_REVIEW"
    evaluated = [result for result in case_results if result.case_role not in {"negative_control", "peer_control"}]
    supports = [result for result in evaluated if result.result_status == "SUPPORTS"]
    blockers = [result for result in evaluated if result.result_status == "BLOCKS"]
    data_gaps = [result for result in evaluated if result.result_status == "DATA_GAP"]
    timing_gaps = [result for result in evaluated if result.result_status == "TIMING_GAP"]
    reviews = [result for result in evaluated if result.result_status == "REQUIRES_REVIEW"]
    if hypothesis.hypothesis_name.startswith("H4") and supports:
        return "MIXED_ANALOG_REVIEW"
    if blockers:
        return "BLOCKED_BY_REQUIRED_GATE"
    if data_gaps:
        return "BLOCKED_BY_DATA_GAP"
    if timing_gaps:
        return "PARTIAL_SUPPORT_TIMING_GAP" if supports else "TIMING_GAP_REVIEW"
    if reviews:
        return "REQUIRES_REVIEW"
    if len(supports) < 2:
        return "NEEDS_MORE_CASES"
    return "ELIGIBLE_FOR_REPLAY_DESIGN"


def _hypothesis_next_evidence(status: str, default_note: str) -> str:
    return {
        "BLOCKED_BY_REQUIRED_GATE": "Inspect failed required gates and keep the pattern as a blocker or mixed analog.",
        "BLOCKED_BY_DATA_GAP": "Load missing PIT evidence, benchmark coverage, or pre-event OHLCV before replay design.",
        "REQUIRES_REVIEW": "Attach official timestamped evidence or computed gates before review can proceed.",
        "PARTIAL_SUPPORT_TIMING_GAP": "Keep supporting cases, but resolve future-only or timing-incompatible evidence before replay design.",
        "TIMING_GAP_REVIEW": "Resolve announcement timing and evidence availability before interpreting the hypothesis.",
        "CONTROL_SUPPORT_REVIEW": "A control case supports under the same rules; review for false-positive or overly broad mechanism risk.",
        "NEEDS_MORE_CASES": "Add comparable leaders and controls before this becomes a replay design candidate.",
        "MIXED_ANALOG_REVIEW": "Review as a mixed analog; do not use as a replay-ready rule.",
        "ELIGIBLE_FOR_REPLAY_DESIGN": "Translate into a replay rule and then validate separately.",
    }.get(status, default_note)


def _evaluate_hypothesis_case(
    hypothesis: HindsightHypothesis,
    case: HindsightCase,
    *,
    observations: pd.DataFrame,
    evidence: pd.DataFrame,
    gates: pd.DataFrame,
    links: pd.DataFrame,
    evaluated_as_of: date,
) -> HindsightHypothesisCaseResult:
    if hypothesis.hypothesis_name.startswith("H1"):
        status, reason_code, reason_text, evidence_ids, gate_ids = _evaluate_anchor_hypothesis(case, evidence)
    elif hypothesis.hypothesis_name.startswith("H2"):
        status, reason_code, reason_text, evidence_ids, gate_ids = _evaluate_downstream_evidence_hypothesis(
            case, evidence
        )
    elif hypothesis.hypothesis_name.startswith("H3"):
        status, reason_code, reason_text, evidence_ids, gate_ids = _evaluate_industry_plus_technical_hypothesis(
            case, evidence, gates
        )
    elif hypothesis.hypothesis_name.startswith("H4"):
        status, reason_code, reason_text, evidence_ids, gate_ids = _evaluate_mixed_analog_hypothesis(
            case, evidence, gates
        )
    else:
        status, reason_code, reason_text, evidence_ids, gate_ids = (
            "REQUIRES_REVIEW",
            "unknown_hypothesis",
            "Hypothesis rule has no evaluator.",
            [],
            [],
        )
    observation_ids = _linked_observation_ids(case.symbol, observations, links, evidence_ids, gate_ids)
    result_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            "|".join([HINDSIGHT_HYPOTHESIS_RULE_VERSION, hypothesis.hypothesis_id, case.symbol]),
        )
    )
    return HindsightHypothesisCaseResult(
        result_id=result_id,
        hypothesis_id=hypothesis.hypothesis_id,
        symbol=case.symbol,
        label=case.label,
        case_role=_case_role(case),
        result_status=status,
        linked_observation_ids_json=_json_list(observation_ids),
        linked_evidence_ids_json=_json_list(evidence_ids),
        linked_gate_ids_json=_json_list(gate_ids),
        reason_code=reason_code,
        reason_text=reason_text,
        evaluated_as_of=evaluated_as_of,
    )


def _evaluate_anchor_hypothesis(
    case: HindsightCase,
    evidence: pd.DataFrame,
) -> tuple[str, str, str, list[str], list[str]]:
    if _case_role(case) != "anchor":
        return "NOT_APPLICABLE", "not_anchor_case", "This case is a downstream or watchlist node, not the anchor.", [], []
    usable = _usable_customer_demand_evidence(evidence, case.symbol)
    if not usable.empty:
        return (
            "SUPPORTS",
            "official_anchor_demand_pass",
            "Official anchor demand evidence is PIT-usable.",
            _ids(usable, "evidence_id"),
            [],
        )
    pending = _pending_customer_demand_evidence(evidence, case.symbol)
    if not pending.empty:
        return (
            _pending_status(pending),
            "anchor_demand_not_pit_usable",
            "Anchor demand evidence exists but is not usable at the replay decision time.",
            _ids(pending, "evidence_id"),
            [],
        )
    return "DATA_GAP", "missing_anchor_demand_evidence", "No PIT-usable official anchor demand evidence is linked.", [], []


def _evaluate_downstream_evidence_hypothesis(
    case: HindsightCase,
    evidence: pd.DataFrame,
) -> tuple[str, str, str, list[str], list[str]]:
    if _case_role(case) == "anchor":
        return "NOT_APPLICABLE", "anchor_not_downstream", "The anchor case is not counted as downstream conversion.", [], []
    if _case_role(case) in {"negative_control", "peer_control"}:
        usable_control = _usable_customer_demand_evidence(evidence, case.symbol)
        if usable_control.empty:
            pending_control = _pending_customer_demand_evidence(evidence, case.symbol)
            return (
                "DATA_GAP",
                "control_not_evaluable",
                "Control case lacks PIT-usable official demand evidence; this is not a failed pattern.",
                _ids(pending_control, "evidence_id"),
                [],
            )
    usable = _usable_customer_demand_evidence(evidence, case.symbol)
    if not usable.empty:
        return (
            "SUPPORTS",
            "official_downstream_demand_pass",
            "Official downstream customer-demand evidence is PIT-usable.",
            _ids(usable, "evidence_id"),
            [],
        )
    pending = _pending_customer_demand_evidence(evidence, case.symbol)
    if not pending.empty:
        return (
            _pending_status(pending),
            "downstream_demand_not_pit_usable",
            "Downstream demand evidence exists but cannot support this replay point yet.",
            _ids(pending, "evidence_id"),
            [],
        )
    context = _context_only_evidence(evidence, case.symbol)
    if not context.empty:
        return (
            "REQUIRES_REVIEW",
            "context_evidence_not_demand",
            "Context evidence exists, but spin-off/listing evidence cannot prove demand conversion.",
            _ids(context, "evidence_id"),
            [],
        )
    return "DATA_GAP", "missing_downstream_demand_evidence", "No official downstream demand evidence is linked.", [], []


def _evaluate_industry_plus_technical_hypothesis(
    case: HindsightCase,
    evidence: pd.DataFrame,
    gates: pd.DataFrame,
) -> tuple[str, str, str, list[str], list[str]]:
    usable = _usable_customer_demand_evidence(evidence, case.symbol)
    if usable.empty:
        pending = _pending_customer_demand_evidence(evidence, case.symbol)
        if not pending.empty:
            return (
                _pending_status(pending),
                "industry_evidence_not_pit_usable",
                "Official industry evidence exists but is not PIT-usable for this replay point.",
                _ids(pending, "evidence_id"),
                [],
            )
        return "DATA_GAP", "missing_industry_evidence", "No PIT-usable official industry evidence is linked.", [], []
    gate_rows = _required_technical_gate_rows(gates, case.symbol)
    gate_status, reason_code, reason_text = _technical_gate_rollup(gate_rows)
    return gate_status, reason_code, reason_text, _ids(usable, "evidence_id"), _ids(gate_rows, "gate_id")


def _evaluate_mixed_analog_hypothesis(
    case: HindsightCase,
    evidence: pd.DataFrame,
    gates: pd.DataFrame,
) -> tuple[str, str, str, list[str], list[str]]:
    usable = _usable_customer_demand_evidence(evidence, case.symbol)
    if usable.empty:
        return (
            "NOT_APPLICABLE",
            "no_official_demand_for_mixed_analog",
            "Mixed analog requires PIT-usable industry evidence first.",
            _ids(_pending_customer_demand_evidence(evidence, case.symbol), "evidence_id"),
            [],
        )
    gate_rows = _required_technical_gate_rows(gates, case.symbol)
    if gate_rows.empty or gate_rows["gate_status"].isin(["DATA_GAP", "PENDING", "REQUIRES_REVIEW"]).any():
        return (
            "DATA_GAP",
            "technical_gates_incomplete",
            "Required technical coverage is incomplete; DATA_GAP is not a failed setup analog.",
            _ids(usable, "evidence_id"),
            _ids(gate_rows, "gate_id"),
        )
    failed = gate_rows[gate_rows["gate_status"] == "FAIL"]
    if not failed.empty:
        return (
            "SUPPORTS",
            "industry_pass_technical_fail",
            "Industry evidence passed, but at least one required technical gate failed.",
            _ids(usable, "evidence_id"),
            _ids(failed, "gate_id"),
        )
    return (
        "NOT_APPLICABLE",
        "technical_gates_not_failed",
        "Required technical gates did not fail, so this is not an industry-only mixed analog.",
        _ids(usable, "evidence_id"),
        _ids(gate_rows, "gate_id"),
    )


def _usable_customer_demand_evidence(evidence: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if evidence.empty:
        return evidence
    symbol_rows = evidence[evidence["symbol"].astype(str).str.upper() == symbol.upper()]
    if symbol_rows.empty:
        return symbol_rows
    demand_rows = symbol_rows[symbol_rows["evidence_lane"].astype(str).isin(["customer_demand"])]
    return demand_rows[
        demand_rows["usable_in_replay"].astype(bool)
        & demand_rows["supports_pattern"].astype(bool)
        & (demand_rows["evidence_status"].astype(str) == "PASS")
    ]


def _pending_customer_demand_evidence(evidence: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if evidence.empty:
        return evidence
    symbol_rows = evidence[evidence["symbol"].astype(str).str.upper() == symbol.upper()]
    if symbol_rows.empty:
        return symbol_rows
    return symbol_rows[
        symbol_rows["evidence_lane"].astype(str).isin(["customer_demand"])
        & ~(
            symbol_rows["usable_in_replay"].astype(bool)
            & symbol_rows["supports_pattern"].astype(bool)
            & (symbol_rows["evidence_status"].astype(str) == "PASS")
        )
    ]


def _context_only_evidence(evidence: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if evidence.empty:
        return evidence
    symbol_rows = evidence[evidence["symbol"].astype(str).str.upper() == symbol.upper()]
    if symbol_rows.empty:
        return symbol_rows
    return symbol_rows[~symbol_rows["evidence_lane"].astype(str).isin(["customer_demand"])]


def _required_technical_gate_rows(gates: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if gates.empty:
        return gates
    symbol_gates = gates[gates["symbol"].astype(str).str.upper() == symbol.upper()]
    if symbol_gates.empty:
        return symbol_gates
    names = symbol_gates["gate_name"].astype(str)
    return symbol_gates[
        (names == "Pre-event price coverage")
        | (names == "Stage 2 trend explain")
        | names.str.startswith("Benchmark RS")
    ]


def _technical_gate_rollup(gate_rows: pd.DataFrame) -> tuple[str, str, str]:
    required_names = ["Pre-event price coverage", "Stage 2 trend explain", "Benchmark RS"]
    if gate_rows.empty:
        return "DATA_GAP", "missing_required_technical_gates", "No required technical replay gates are available."
    present_names = set(gate_rows["gate_name"].astype(str))
    missing = [
        name
        for name in required_names
        if not any(existing == name or (name == "Benchmark RS" and existing.startswith("Benchmark RS")) for existing in present_names)
    ]
    if missing:
        return "DATA_GAP", "missing_required_technical_gates", "Missing required gates: " + ", ".join(missing)
    statuses = set(gate_rows["gate_status"].astype(str))
    if "FAIL" in statuses:
        return "BLOCKS", "required_technical_gate_failed", "At least one required technical replay gate failed."
    if statuses & {"DATA_GAP", "PENDING", "REQUIRES_REVIEW"}:
        return "DATA_GAP", "required_technical_gate_data_gap", "At least one required technical replay gate is missing or unresolved."
    return "SUPPORTS", "required_technical_gates_pass", "All required technical replay gates passed."


def _pending_status(evidence: pd.DataFrame) -> str:
    statuses = set(evidence["evidence_status"].astype(str)) if not evidence.empty else set()
    if statuses & {"REQUIRES_REVIEW"}:
        return "TIMING_GAP"
    return "DATA_GAP"


def _linked_observation_ids(
    symbol: str,
    observations: pd.DataFrame,
    links: pd.DataFrame,
    evidence_ids: list[str],
    gate_ids: list[str],
) -> list[str]:
    ids: set[str] = set()
    if not links.empty:
        linked_ids = set(evidence_ids + gate_ids)
        linked_rows = links[
            (links["symbol"].astype(str).str.upper() == symbol.upper())
            & links["linked_id"].astype(str).isin(linked_ids)
        ]
        ids.update(str(value) for value in linked_rows.get("observation_id", []))
    if not ids and not observations.empty:
        symbol_rows = observations[observations["symbol"].astype(str).str.upper() == symbol.upper()]
        ids.update(str(value) for value in symbol_rows.get("observation_id", []))
    return sorted(ids)


def _case_role(case: HindsightCase) -> str:
    role = str(getattr(case, "case_role", "") or "").strip()
    if role:
        return role
    return _default_case_role(case.symbol, case.theme)


def _ids(frame: pd.DataFrame, column: str) -> list[str]:
    if frame.empty or column not in frame:
        return []
    return [str(value) for value in frame[column].dropna().tolist()]


def _json_list(values: list[str]) -> str:
    return json.dumps(sorted({str(value) for value in values if str(value)}), sort_keys=True)


def _observation(
    *,
    scan_id: str,
    case: HindsightCase,
    observation_group: str,
    pattern_name: str,
    observation_value: str,
    status: str,
    evidence: str,
    source: str,
    extraction_method: str,
    requires_review: bool,
) -> HindsightPatternObservation:
    observation_key = "|".join([scan_id, case.symbol, observation_group, pattern_name])
    return HindsightPatternObservation(
        observation_id=str(uuid.uuid5(uuid.NAMESPACE_URL, observation_key)),
        scan_id=scan_id,
        symbol=case.symbol,
        label=case.label,
        observation_group=observation_group,
        pattern_name=pattern_name,
        observation_value=observation_value,
        status=status,
        evidence=evidence,
        source=source,
        extraction_method=extraction_method,
        requires_review=requires_review,
    )


def _industry_cluster(theme: str) -> str:
    lowered = theme.lower()
    if "memory" in lowered or "hbm" in lowered:
        return "AI infrastructure / memory and HBM"
    if "storage" in lowered or "nand" in lowered:
        return "AI infrastructure / storage and NAND"
    if "optical" in lowered or "network" in lowered:
        return "AI infrastructure / optical networking"
    if "semiconductor" in lowered or "chip" in lowered:
        return "AI infrastructure / semiconductors"
    if "ai" in lowered:
        return "AI infrastructure / other"
    return "Other industry theme"


def _default_case_role(symbol: str, theme: str) -> str:
    symbol_upper = symbol.upper()
    if symbol_upper == "NVDA":
        return "anchor"
    lowered = theme.lower()
    if any(word in lowered for word in ["memory", "hbm", "storage", "nand", "optical", "network"]):
        return "downstream_node"
    return "watchlist"


def _case_seed_summary(case: HindsightCase) -> str:
    parts = [case.anchor_event]
    if case.case_role in {"negative_control", "peer_control"}:
        parts.append(f"Control role: {case.case_role}.")
    if case.control_reason:
        parts.append(case.control_reason)
    return " ".join(part for part in parts if part)


def _event_type_from_theme(theme: str) -> str:
    lowered = theme.lower()
    if any(word in lowered for word in ["memory", "hbm", "storage", "nand", "optical", "semiconductor", "ai"]):
        return "theme_catalyst"
    return "other"


def _event_from_row(row: pd.Series) -> HindsightEvent:
    published = row.get("published_at_utc")
    fundamental_available = row.get("fundamental_evidence_available_at")
    return HindsightEvent(
        event_id=str(row["event_id"]),
        symbol=str(row["symbol"]).upper(),
        label=str(row["label"]),
        event_type=str(row["event_type"]),
        event_date=_coerce_date(row["event_date"]),
        published_at_utc=_coerce_datetime(published),
        market_session=str(row["market_session"]),
        source_url=str(row.get("source_url") or ""),
        source_quality=str(row.get("source_quality") or ""),
        evidence_type=str(row.get("evidence_type") or ""),
        evidence_summary=str(row.get("evidence_summary") or ""),
        fundamental_evidence_available_at=_coerce_datetime(fundamental_available),
        first_tradable_date=_coerce_optional_date(row.get("first_tradable_date")),
        first_tradable_bar_policy=str(row.get("first_tradable_bar_policy") or ""),
        technical_replay_as_of=_coerce_optional_date(row.get("technical_replay_as_of")),
        timing_status=str(row.get("timing_status") or "DATA_GAP"),
        requires_review=bool(row.get("requires_review")),
    )


def _coerce_date(value: object) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if hasattr(value, "date"):
        return value.date()
    return date.fromisoformat(str(value)[:10])


def _coerce_optional_date(value: object) -> date | None:
    if value is None or pd.isna(value):
        return None
    return _coerce_date(value)


def _coerce_datetime(value: object) -> datetime | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def resolve_first_tradable_date(event_date: date, market_session: str, trading_dates: list[date]) -> date | None:
    trading_dates = sorted({day for day in trading_dates if day >= event_date})
    if market_session == "date_only_ambiguous" or not trading_dates:
        return None
    if market_session in {"pre_market", "regular"} and trading_dates[0] == event_date:
        return event_date
    for trading_day in trading_dates:
        if trading_day > event_date:
            return trading_day
    return None


def _replay_decision_at(event: HindsightEvent) -> datetime | None:
    if not event.first_tradable_date:
        return None
    return datetime.combine(event.first_tradable_date, time(14, 30), tzinfo=timezone.utc)


def _evidence_status(
    available_at: datetime | None,
    replay_decision_at: datetime | None,
    *,
    seed_requires_review: bool,
) -> str:
    if not available_at or not replay_decision_at:
        return "DATA_GAP"
    if available_at > replay_decision_at:
        return "REQUIRES_REVIEW"
    return "REQUIRES_REVIEW" if seed_requires_review else "PASS"


def _fallback_evidence_item(event: HindsightEvent, replay_decision_at: datetime | None) -> HindsightEvidenceItem:
    evidence_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "|".join(["hindsight_evidence", event.event_id, "fallback"])))
    return HindsightEvidenceItem(
        evidence_id=evidence_id,
        event_id=event.event_id,
        symbol=event.symbol,
        label=event.label,
        evidence_lane="industry",
        evidence_kind="case_narrative",
        claim=event.evidence_summary,
        metric_name="",
        metric_value="",
        metric_period="",
        source_url=event.source_url,
        source_quality=event.source_quality,
        published_at_utc=event.published_at_utc,
        available_at_utc=event.fundamental_evidence_available_at,
        replay_decision_at=replay_decision_at,
        usable_in_replay=bool(
            event.fundamental_evidence_available_at
            and replay_decision_at
            and event.fundamental_evidence_available_at <= replay_decision_at
        ),
        supports_pattern=False,
        evidence_status="DATA_GAP",
        requires_review=True,
        review_note="Narrative case seed needs official timestamped evidence before it can support a pattern.",
    )


def _event_timing_gates(
    event: HindsightEvent,
    case: HindsightCase,
    *,
    asof_date: date,
) -> list[HindsightReplayGate]:
    gates = [
        _gate(
            event=event,
            case=case,
            gate_group="event_timing",
            gate_name="Event timestamp available",
            gate_status="PASS" if event.published_at_utc else "DATA_GAP",
            formula="published_at_utc must be known before event reaction can be interpreted",
            computed_value=event.published_at_utc.isoformat() if event.published_at_utc else "missing",
            threshold="non-null timestamp",
            data_used="hindsight_event_ledger.published_at_utc",
            required_rows=1,
            available_rows=1 if event.published_at_utc else 0,
            missing_detail="Published timestamp is not captured." if not event.published_at_utc else "",
            reason=(
                "Event timing is auditable."
                if event.published_at_utc
                else "Only a date-level seed is available; do not infer intraday availability."
            ),
            asof_date=asof_date,
            source=event.source_url,
            requires_review=not bool(event.published_at_utc),
        ),
        _gate(
            event=event,
            case=case,
            gate_group="event_timing",
            gate_name="First tradable date resolved",
            gate_status="PASS" if event.first_tradable_date else "DATA_GAP",
            formula="first_tradable_date must follow the event market session policy",
            computed_value=event.first_tradable_date.isoformat() if event.first_tradable_date else "unresolved",
            threshold="non-null first tradable date",
            data_used="hindsight_event_ledger.market_session + market calendar",
            required_rows=1,
            available_rows=1 if event.first_tradable_date else 0,
            missing_detail=(
                "Market session is date_only_ambiguous or the trading calendar has not resolved the event."
                if not event.first_tradable_date
                else ""
            ),
            reason=(
                "Reaction and first-tradable checks can be separated."
                if event.first_tradable_date
                else "Do not compute event reaction until the first tradable date is known."
            ),
            asof_date=asof_date,
            source=event.source_url,
            requires_review=not bool(event.first_tradable_date),
        ),
    ]
    return gates


def _pre_event_technical_gates(
    config: SectorScoutConfig,
    event: HindsightEvent,
    case: HindsightCase,
    *,
    asof_date: date,
) -> list[HindsightReplayGate]:
    prices = _price_rows_before_event(config, event.symbol, event.event_date)
    benchmark = _primary_benchmark(case)
    gates: list[HindsightReplayGate] = []
    gates.append(
        _gate(
            event=event,
            case=case,
            gate_group="pre_event",
            gate_name="Pre-event price coverage",
            gate_status="PASS" if len(prices) >= 60 else "DATA_GAP",
            formula="daily price rows before event_date >= 60",
            computed_value=f"{len(prices)} rows",
            threshold=">= 60 rows",
            data_used="daily_prices before event_date",
            required_rows=60,
            available_rows=len(prices),
            missing_detail="" if len(prices) >= 60 else "Not enough pre-event daily bars loaded.",
            reason=(
                "Enough pre-event bars exist for basic trend and liquidity context."
                if len(prices) >= 60
                else "Pre-event context is incomplete; avoid treating the case as if setup evidence was visible."
            ),
            asof_date=asof_date,
            source="daily_prices",
            requires_review=False,
        )
    )
    gates.append(_stage2_gate(event, case, prices, asof_date=asof_date))
    gates.append(_benchmark_rs_gate(config, event, case, benchmark=benchmark, asof_date=asof_date))
    return gates


def _stage2_gate(
    event: HindsightEvent,
    case: HindsightCase,
    prices: pd.DataFrame,
    *,
    asof_date: date,
) -> HindsightReplayGate:
    required = 220
    if len(prices) < required:
        return _gate(
            event=event,
            case=case,
            gate_group="pre_event",
            gate_name="Stage 2 trend explain",
            gate_status="DATA_GAP",
            formula="close > SMA50 and close > SMA200 and SMA200[t] > SMA200[t-20]",
            computed_value=f"{len(prices)} rows available",
            threshold=f">= {required} pre-event rows",
            data_used="daily_prices.adj_close",
            required_rows=required,
            available_rows=len(prices),
            missing_detail="Need enough pre-event rows for SMA200 and slope.",
            reason="Trend proxy cannot be evaluated without sufficient pre-event history.",
            asof_date=asof_date,
            source="daily_prices",
            requires_review=False,
        )
    close = prices["adj_close"].astype(float).reset_index(drop=True)
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    latest_close = float(close.iloc[-1])
    latest_sma50 = float(sma50.iloc[-1])
    latest_sma200 = float(sma200.iloc[-1])
    prior_sma200 = float(sma200.iloc[-21])
    passed = latest_close > latest_sma50 and latest_close > latest_sma200 and latest_sma200 > prior_sma200
    return _gate(
        event=event,
        case=case,
        gate_group="pre_event",
        gate_name="Stage 2 trend explain",
        gate_status="PASS" if passed else "FAIL",
        formula="close > SMA50 and close > SMA200 and SMA200[t] > SMA200[t-20]",
        computed_value=(
            f"close={latest_close:.2f}; sma50={latest_sma50:.2f}; "
            f"sma200={latest_sma200:.2f}; sma200_20d_prior={prior_sma200:.2f}"
        ),
        threshold="all conditions true",
        data_used="daily_prices.adj_close",
        required_rows=required,
        available_rows=len(prices),
        missing_detail="",
        reason="Pre-event trend proxy met." if passed else "Pre-event trend proxy did not meet all conditions.",
        asof_date=asof_date,
        source="daily_prices",
        requires_review=False,
    )


def _benchmark_rs_gate(
    config: SectorScoutConfig,
    event: HindsightEvent,
    case: HindsightCase,
    *,
    benchmark: str,
    asof_date: date,
) -> HindsightReplayGate:
    symbol_prices = _price_rows_before_event(config, event.symbol, event.event_date).tail(63)
    benchmark_prices = _price_rows_before_event(config, benchmark, event.event_date).tail(63)
    required = 63
    if len(symbol_prices) < required or len(benchmark_prices) < required:
        return _gate(
            event=event,
            case=case,
            gate_group="pre_event",
            gate_name=f"Benchmark RS vs {benchmark}",
            gate_status="DATA_GAP",
            formula="63-session symbol return minus fixed primary benchmark return",
            computed_value=f"symbol_rows={len(symbol_prices)}; benchmark_rows={len(benchmark_prices)}",
            threshold=f"{required} rows for both symbol and {benchmark}",
            data_used=f"daily_prices for {event.symbol} and fixed benchmark {benchmark}",
            required_rows=required * 2,
            available_rows=len(symbol_prices) + len(benchmark_prices),
            missing_detail="Primary benchmark coverage is incomplete; no benchmark fallback is applied.",
            reason="Relative strength cannot be evaluated without the fixed primary benchmark.",
            asof_date=asof_date,
            source="daily_prices",
            requires_review=False,
        )
    symbol_return = float(symbol_prices["adj_close"].iloc[-1] / symbol_prices["adj_close"].iloc[0] - 1)
    benchmark_return = float(benchmark_prices["adj_close"].iloc[-1] / benchmark_prices["adj_close"].iloc[0] - 1)
    spread = symbol_return - benchmark_return
    return _gate(
        event=event,
        case=case,
        gate_group="pre_event",
        gate_name=f"Benchmark RS vs {benchmark}",
        gate_status="PASS" if spread > 0 else "FAIL",
        formula="63-session symbol return minus fixed primary benchmark return",
        computed_value=f"symbol={symbol_return:.2%}; {benchmark}={benchmark_return:.2%}; spread={spread:.2%}",
        threshold="spread > 0",
        data_used=f"daily_prices for {event.symbol} and fixed benchmark {benchmark}",
        required_rows=required * 2,
        available_rows=len(symbol_prices) + len(benchmark_prices),
        missing_detail="",
        reason="Symbol led the fixed benchmark." if spread > 0 else "Symbol did not lead the fixed benchmark.",
        asof_date=asof_date,
        source="daily_prices",
        requires_review=False,
    )


def _first_tradable_gates(
    config: SectorScoutConfig,
    event: HindsightEvent,
    case: HindsightCase,
    *,
    asof_date: date,
) -> list[HindsightReplayGate]:
    if event.first_tradable_date is None:
        return [
            _gate(
                event=event,
                case=case,
                gate_group="first_tradable",
                gate_name="First-tradable reaction explain",
                gate_status="DATA_GAP",
                formula="first_tradable_date is required before reaction checks",
                computed_value="unresolved",
                threshold="first_tradable_date resolved",
                data_used="hindsight_event_ledger",
                required_rows=1,
                available_rows=0,
                missing_detail="First tradable date is unresolved.",
                reason="Reaction checks are blocked to avoid using an unavailable or ambiguous bar.",
                asof_date=asof_date,
                source=event.source_url,
                requires_review=True,
            )
        ]
    prices = _price_rows_on_or_after(config, event.symbol, event.first_tradable_date)
    first_row = prices.iloc[0].to_dict() if not prices.empty else None
    gates = [
        _gate(
            event=event,
            case=case,
            gate_group="first_tradable",
            gate_name="First-tradable price row",
            gate_status="PASS" if first_row else "DATA_GAP",
            formula="daily_prices row must exist for first_tradable_date",
            computed_value=str(first_row.get("price_date")) if first_row else "missing",
            threshold=event.first_tradable_date.isoformat(),
            data_used="daily_prices",
            required_rows=1,
            available_rows=1 if first_row else 0,
            missing_detail="" if first_row else "No price row exists for first_tradable_date.",
            reason="First tradable bar is available." if first_row else "First tradable bar is missing.",
            asof_date=asof_date,
            source="daily_prices",
            requires_review=False,
        )
    ]
    return gates


def _gate(
    *,
    event: HindsightEvent,
    case: HindsightCase,
    gate_group: str,
    gate_name: str,
    gate_status: str,
    formula: str,
    computed_value: str,
    threshold: str,
    data_used: str,
    required_rows: int,
    available_rows: int,
    missing_detail: str,
    reason: str,
    asof_date: date,
    source: str,
    requires_review: bool,
) -> HindsightReplayGate:
    gate_key = "|".join([event.event_id, gate_group, gate_name, asof_date.isoformat()])
    return HindsightReplayGate(
        gate_id=str(uuid.uuid5(uuid.NAMESPACE_URL, gate_key)),
        event_id=event.event_id,
        symbol=event.symbol,
        label=case.label,
        gate_group=gate_group,
        gate_name=gate_name,
        gate_status=gate_status,
        formula=formula,
        computed_value=computed_value,
        threshold=threshold,
        data_used=data_used,
        required_rows=required_rows,
        available_rows=available_rows,
        missing_detail=missing_detail,
        reason=reason,
        asof_date=asof_date,
        first_tradable_date=event.first_tradable_date,
        source=source,
        requires_review=requires_review,
    )


def _primary_benchmark(case: HindsightCase) -> str:
    lowered = case.theme.lower()
    if any(word in lowered for word in ["semiconductor", "memory", "hbm", "nand", "storage", "optical", "ai"]):
        return "SMH"
    return "QQQ"


def _format_metric(value: object, *, suffix: str = "") -> str:
    if value is None or pd.isna(value):
        return "missing"
    try:
        text = f"{float(value):.2f}"
    except Exception:
        text = str(value)
    return text + suffix


def _price_rows(config: SectorScoutConfig, case: HindsightCase) -> pd.DataFrame:
    with connect_database(config.database.path) as connection:
        try:
            return connection.execute(
                """
                SELECT price_date, adj_close, adj_volume, provider
                FROM daily_prices
                WHERE symbol = ?
                  AND price_date BETWEEN ? AND ?
                QUALIFY row_number() OVER (PARTITION BY price_date ORDER BY provider) = 1
                ORDER BY price_date
                """,
                [case.symbol, case.start_date, case.end_date],
            ).fetchdf()
        except Exception:
            return pd.DataFrame()


def _price_rows_before_event(config: SectorScoutConfig, symbol: str, event_date: date) -> pd.DataFrame:
    with connect_database(config.database.path) as connection:
        try:
            return connection.execute(
                """
                SELECT price_date, adj_open, adj_high, adj_low, adj_close, adj_volume, provider
                FROM daily_prices
                WHERE symbol = ?
                  AND price_date < ?
                QUALIFY row_number() OVER (PARTITION BY price_date ORDER BY provider) = 1
                ORDER BY price_date
                """,
                [symbol.upper(), event_date],
            ).fetchdf()
        except Exception:
            return pd.DataFrame()


def _price_rows_on_or_after(config: SectorScoutConfig, symbol: str, start_date: date) -> pd.DataFrame:
    with connect_database(config.database.path) as connection:
        try:
            return connection.execute(
                """
                SELECT price_date, adj_open, adj_high, adj_low, adj_close, adj_volume, provider
                FROM daily_prices
                WHERE symbol = ?
                  AND price_date >= ?
                QUALIFY row_number() OVER (PARTITION BY price_date ORDER BY provider) = 1
                ORDER BY price_date
                """,
                [symbol.upper(), start_date],
            ).fetchdf()
        except Exception:
            return pd.DataFrame()


def _rs_percentile_near_start(config: SectorScoutConfig, case: HindsightCase) -> float | None:
    with connect_database(config.database.path) as connection:
        try:
            row = connection.execute(
                """
                SELECT rs_percentile
                FROM technical_indicators
                WHERE symbol = ? AND asof_date <= ?
                ORDER BY asof_date DESC
                LIMIT 1
                """,
                [case.symbol, case.start_date],
            ).fetchone()
        except Exception:
            row = None
    return float(row[0]) if row and row[0] is not None else None


def _case_flags(prices: pd.DataFrame, *, max_gain_pct: float | None, rs_percentile_start: float | None) -> dict[str, Any]:
    close = prices["adj_close"].astype(float)
    volume = prices["adj_volume"].astype(float)
    sma50 = close.rolling(50, min_periods=20).mean()
    sma200 = close.rolling(200, min_periods=80).mean()
    high20 = close.rolling(20, min_periods=10).max()
    stage2_series = (close > sma50) & (close > sma200)
    breakout_series = close > high20.shift(1)
    volume_expansion_series = volume > volume.rolling(50, min_periods=20).mean() * 1.3
    stage2_days_last_30 = int(stage2_series.tail(30).sum()) if len(prices) >= 80 else 0
    breakout_days = int(breakout_series.tail(max(len(close) - 20, 1)).sum()) if len(prices) >= 20 else 0
    volume_expansion_days = int(volume_expansion_series.tail(max(len(volume) - 20, 1)).sum()) if len(prices) >= 20 else 0
    stage2_proxy = bool(stage2_days_last_30 > 0)
    breakout_proxy = bool(breakout_days > 0)
    volume_expansion_proxy = bool(volume_expansion_days > 0)
    above_50d_pct = float(stage2_series.fillna(False).sum() / len(stage2_series) * 100) if len(prices) else 0.0
    return {
        "has_price_history": True,
        "enough_history_60d": len(prices) >= 60,
        "gain_100pct": bool(max_gain_pct is not None and max_gain_pct >= 100),
        "gain_200pct": bool(max_gain_pct is not None and max_gain_pct >= 200),
        "gain_300pct": bool(max_gain_pct is not None and max_gain_pct >= 300),
        "rs_80_at_start": bool(rs_percentile_start is not None and rs_percentile_start >= 80),
        "stage2_proxy": stage2_proxy,
        "breakout_proxy": breakout_proxy,
        "volume_expansion_proxy": volume_expansion_proxy,
        "stage2_days_last_30": stage2_days_last_30,
        "breakout_days": breakout_days,
        "volume_expansion_days": volume_expansion_days,
        "above_50d_and_200d_pct": round(above_50d_pct, 2),
    }


def _hindsight_score(flags: dict[str, Any]) -> float:
    weights = {
        "has_price_history": 10,
        "enough_history_60d": 10,
        "gain_100pct": 20,
        "gain_200pct": 15,
        "gain_300pct": 10,
        "rs_80_at_start": 15,
        "stage2_proxy": 15,
        "breakout_proxy": 10,
        "volume_expansion_proxy": 5,
    }
    return float(sum(weight for flag, weight in weights.items() if flags.get(flag)))


def _case_notes(flags: dict[str, Any]) -> str:
    missing = [flag for flag, value in flags.items() if not value]
    if not missing:
        return "All current hindsight diagnostics are present. Treat this as a case-study candidate, not a strategy result."
    return "Missing or unconfirmed diagnostics: " + ", ".join(missing)


def _max_drawdown_pct(close: pd.Series) -> float | None:
    if close.empty:
        return None
    running_max = close.cummax()
    drawdowns = (close / running_max - 1.0) * 100
    return float(drawdowns.min())


def _persist_hindsight_events(config: SectorScoutConfig, events: list[HindsightEvent]) -> None:
    if not events:
        return
    now = datetime.now(timezone.utc)
    git_commit = get_git_commit()
    cfg_hash = config_hash(config)
    with connect_database(config.database.path) as connection:
        for event in events:
            connection.execute(
                """
                INSERT OR REPLACE INTO hindsight_event_ledger (
                    event_id, symbol, label, event_type, event_date, published_at_utc,
                    market_session, source_url, source_quality, evidence_type,
                    evidence_summary, fundamental_evidence_available_at,
                    first_tradable_date, first_tradable_bar_policy,
                    technical_replay_as_of, timing_status, requires_review,
                    generated_at_utc, config_hash, git_commit, data_snapshot_id,
                    universe_version, theme_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    event.event_id,
                    event.symbol,
                    event.label,
                    event.event_type,
                    event.event_date,
                    event.published_at_utc,
                    event.market_session,
                    event.source_url,
                    event.source_quality,
                    event.evidence_type,
                    event.evidence_summary,
                    event.fundamental_evidence_available_at,
                    event.first_tradable_date,
                    event.first_tradable_bar_policy,
                    event.technical_replay_as_of,
                    event.timing_status,
                    event.requires_review,
                    now,
                    cfg_hash,
                    git_commit,
                    config.reproducibility.data_snapshot_id,
                    config.reproducibility.universe_version,
                    config.reproducibility.theme_version,
                ],
            )


def _persist_hindsight_evidence(config: SectorScoutConfig, evidence_items: list[HindsightEvidenceItem]) -> None:
    if not evidence_items:
        return
    now = datetime.now(timezone.utc)
    git_commit = get_git_commit()
    cfg_hash = config_hash(config)
    with connect_database(config.database.path) as connection:
        for item in evidence_items:
            connection.execute(
                """
                INSERT OR REPLACE INTO hindsight_evidence_items (
                    evidence_id, event_id, symbol, label, evidence_lane, evidence_kind,
                    claim, metric_name, metric_value, metric_period, source_url,
                    source_quality, published_at_utc, available_at_utc,
                    replay_decision_at, usable_in_replay, supports_pattern,
                    evidence_status, requires_review, review_note,
                    generated_at_utc, config_hash, git_commit, data_snapshot_id,
                    universe_version, theme_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    item.evidence_id,
                    item.event_id,
                    item.symbol,
                    item.label,
                    item.evidence_lane,
                    item.evidence_kind,
                    item.claim,
                    item.metric_name,
                    item.metric_value,
                    item.metric_period,
                    item.source_url,
                    item.source_quality,
                    item.published_at_utc,
                    item.available_at_utc,
                    item.replay_decision_at,
                    item.usable_in_replay,
                    item.supports_pattern,
                    item.evidence_status,
                    item.requires_review,
                    item.review_note,
                    now,
                    cfg_hash,
                    git_commit,
                    config.reproducibility.data_snapshot_id,
                    config.reproducibility.universe_version,
                    config.reproducibility.theme_version,
                ],
            )


def _persist_hindsight_replay_gates(config: SectorScoutConfig, gates: list[HindsightReplayGate]) -> None:
    if not gates:
        return
    now = datetime.now(timezone.utc)
    git_commit = get_git_commit()
    cfg_hash = config_hash(config)
    with connect_database(config.database.path) as connection:
        for gate in gates:
            connection.execute(
                """
                INSERT OR REPLACE INTO hindsight_replay_gates (
                    gate_id, event_id, symbol, label, gate_group, gate_name,
                    gate_status, formula, computed_value, threshold, data_used,
                    required_rows, available_rows, missing_detail, reason,
                    asof_date, first_tradable_date, source, requires_review,
                    generated_at_utc, config_hash, git_commit, data_snapshot_id,
                    universe_version, theme_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    gate.gate_id,
                    gate.event_id,
                    gate.symbol,
                    gate.label,
                    gate.gate_group,
                    gate.gate_name,
                    gate.gate_status,
                    gate.formula,
                    gate.computed_value,
                    gate.threshold,
                    gate.data_used,
                    gate.required_rows,
                    gate.available_rows,
                    gate.missing_detail,
                    gate.reason,
                    gate.asof_date,
                    gate.first_tradable_date,
                    gate.source,
                    gate.requires_review,
                    now,
                    cfg_hash,
                    git_commit,
                    config.reproducibility.data_snapshot_id,
                    config.reproducibility.universe_version,
                    config.reproducibility.theme_version,
                ],
            )


def _persist_results(config: SectorScoutConfig, results: list[HindsightResult]) -> None:
    now = datetime.now(timezone.utc)
    git_commit = get_git_commit()
    cfg_hash = config_hash(config)
    with connect_database(config.database.path) as connection:
        for result in results:
            connection.execute(
                """
                INSERT OR REPLACE INTO hindsight_scan_results (
                    scan_id, symbol, label, scan_start, scan_end, price_rows,
                    start_close, end_close, max_close, peak_date, max_gain_pct,
                    max_drawdown_pct, rs_percentile_start, hindsight_score,
                    flags_json, data_quality, notes, generated_at_utc,
                    config_hash, git_commit, data_snapshot_id, universe_version, theme_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    result.scan_id,
                    result.symbol,
                    result.label,
                    result.scan_start,
                    result.scan_end,
                    result.price_rows,
                    result.start_close,
                    result.end_close,
                    result.max_close,
                    result.peak_date,
                    result.max_gain_pct,
                    result.max_drawdown_pct,
                    result.rs_percentile_start,
                    result.hindsight_score,
                    json.dumps(result.flags, sort_keys=True),
                    result.data_quality,
                    result.notes,
                    now,
                    cfg_hash,
                    git_commit,
                    config.reproducibility.data_snapshot_id,
                    config.reproducibility.universe_version,
                    config.reproducibility.theme_version,
                ],
            )


def _persist_hindsight_observation_links(config: SectorScoutConfig, links: list[HindsightObservationLink]) -> None:
    if not links:
        return
    now = datetime.now(timezone.utc)
    git_commit = get_git_commit()
    cfg_hash = config_hash(config)
    with connect_database(config.database.path) as connection:
        for link in links:
            connection.execute(
                """
                INSERT OR REPLACE INTO hindsight_observation_links (
                    link_id, observation_id, symbol, link_type, linked_id,
                    linked_table, link_role, link_status, reason,
                    generated_at_utc, config_hash, git_commit, data_snapshot_id,
                    universe_version, theme_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    link.link_id,
                    link.observation_id,
                    link.symbol,
                    link.link_type,
                    link.linked_id,
                    link.linked_table,
                    link.link_role,
                    link.link_status,
                    link.reason,
                    now,
                    cfg_hash,
                    git_commit,
                    config.reproducibility.data_snapshot_id,
                    config.reproducibility.universe_version,
                    config.reproducibility.theme_version,
                ],
            )


def _persist_hindsight_hypothesis_registry(
    config: SectorScoutConfig,
    hypotheses: list[HindsightHypothesis],
    case_results: list[HindsightHypothesisCaseResult],
) -> None:
    now = datetime.now(timezone.utc)
    git_commit = get_git_commit()
    cfg_hash = config_hash(config)
    with connect_database(config.database.path) as connection:
        for hypothesis in hypotheses:
            connection.execute(
                """
                INSERT OR REPLACE INTO hindsight_hypotheses (
                    hypothesis_id, hypothesis_group, hypothesis_name, mechanism,
                    required_evidence, required_gates, promotion_status,
                    minimum_next_evidence, suggested_replay_rule,
                    anti_hindsight_notes, rule_version, generated_at_utc,
                    config_hash, git_commit, data_snapshot_id, universe_version,
                    theme_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    hypothesis.hypothesis_id,
                    hypothesis.hypothesis_group,
                    hypothesis.hypothesis_name,
                    hypothesis.mechanism,
                    hypothesis.required_evidence,
                    hypothesis.required_gates,
                    hypothesis.promotion_status,
                    hypothesis.minimum_next_evidence,
                    hypothesis.suggested_replay_rule,
                    hypothesis.anti_hindsight_notes,
                    hypothesis.rule_version,
                    now,
                    cfg_hash,
                    git_commit,
                    config.reproducibility.data_snapshot_id,
                    config.reproducibility.universe_version,
                    config.reproducibility.theme_version,
                ],
            )
        for result in case_results:
            connection.execute(
                """
                INSERT OR REPLACE INTO hindsight_hypothesis_case_results (
                    result_id, hypothesis_id, symbol, label, case_role,
                    result_status, linked_observation_ids_json,
                    linked_evidence_ids_json, linked_gate_ids_json, reason_code,
                    reason_text, evaluated_as_of, rule_version, generated_at_utc,
                    config_hash, git_commit, data_snapshot_id, universe_version,
                    theme_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    result.result_id,
                    result.hypothesis_id,
                    result.symbol,
                    result.label,
                    result.case_role,
                    result.result_status,
                    result.linked_observation_ids_json,
                    result.linked_evidence_ids_json,
                    result.linked_gate_ids_json,
                    result.reason_code,
                    result.reason_text,
                    result.evaluated_as_of,
                    HINDSIGHT_HYPOTHESIS_RULE_VERSION,
                    now,
                    cfg_hash,
                    git_commit,
                    config.reproducibility.data_snapshot_id,
                    config.reproducibility.universe_version,
                    config.reproducibility.theme_version,
                ],
            )


def _persist_pattern_observations(config: SectorScoutConfig, observations: list[HindsightPatternObservation]) -> None:
    if not observations:
        return
    now = datetime.now(timezone.utc)
    git_commit = get_git_commit()
    cfg_hash = config_hash(config)
    with connect_database(config.database.path) as connection:
        for observation in observations:
            connection.execute(
                """
                INSERT OR REPLACE INTO hindsight_pattern_observations (
                    observation_id, scan_id, symbol, label, observation_group,
                    pattern_name, observation_value, status, evidence, source,
                    extraction_method, requires_review, generated_at_utc,
                    config_hash, git_commit, data_snapshot_id, universe_version,
                    theme_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    observation.observation_id,
                    observation.scan_id,
                    observation.symbol,
                    observation.label,
                    observation.observation_group,
                    observation.pattern_name,
                    observation.observation_value,
                    observation.status,
                    observation.evidence,
                    observation.source,
                    observation.extraction_method,
                    observation.requires_review,
                    now,
                    cfg_hash,
                    git_commit,
                    config.reproducibility.data_snapshot_id,
                    config.reproducibility.universe_version,
                    config.reproducibility.theme_version,
                ],
            )
