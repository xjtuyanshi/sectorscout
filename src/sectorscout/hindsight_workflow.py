from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sectorscout.config import SectorScoutConfig, config_hash
from sectorscout.db import connect_database
from sectorscout.hindsight import (
    DEFAULT_HINDSIGHT_CASES_PATH,
    build_hindsight_hypothesis_registry,
    build_hindsight_observation_links,
    build_hindsight_replay_gates,
    ensure_hindsight_tables,
    fetch_hindsight_prices,
    scan_hindsight_cases,
    seed_hindsight_cases,
    seed_hindsight_evidence,
    seed_hindsight_events,
    write_default_hindsight_cases,
)
from sectorscout.hindsight_companyfacts import build_hindsight_companyfacts, companyfacts_summary
from sectorscout.hindsight_industry_profile import build_hindsight_industry_profiles, industry_profile_summary
from sectorscout.hindsight_playbook import generate_hindsight_pattern_playbook
from sectorscout.hindsight_sec_metadata import build_hindsight_sec_filing_metadata, sec_filing_metadata_summary
from sectorscout.hindsight_source_audit import build_hindsight_source_audit, source_audit_summary
from sectorscout.hindsight_source_snapshot import (
    DEFAULT_SOURCE_SNAPSHOT_DIR,
    fetch_hindsight_source_snapshots,
)
from sectorscout.metadata import get_git_commit


HINDSIGHT_REFRESH_TABLES = [
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


@dataclass(frozen=True)
class HindsightRefreshStep:
    step: str
    status: str
    detail: str
    rows: int | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class HindsightRefreshResult:
    generated_at_utc: str
    asof_date: str
    case_path: str
    price_provider: str
    price_rows_fetched: int
    price_rows_inserted: int
    playbook_path: str
    config_hash: str
    git_commit: str
    steps: list[HindsightRefreshStep]
    row_counts: dict[str, int]
    price_summary: list[dict[str, Any]]
    source_audit: list[dict[str, object]]
    source_audit_summary: dict[str, object]
    source_snapshots: list[dict[str, object]]
    sec_filing_metadata: list[dict[str, object]]
    sec_filing_metadata_summary: dict[str, object]
    companyfacts_candidates: list[dict[str, object]]
    companyfacts_status: list[dict[str, object]]
    companyfacts_summary: dict[str, object]
    industry_profiles: list[dict[str, object]]
    industry_profile_summary: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["steps"] = [step.to_dict() for step in self.steps]
        return payload


def run_hindsight_refresh(
    config: SectorScoutConfig,
    *,
    path: Path = DEFAULT_HINDSIGHT_CASES_PATH,
    output_dir: Path = Path("data/hindsight/reports"),
    asof_date: date | None = None,
    fetch_prices: bool = True,
    provider: str = "yahoo_chart_public",
    lookback_days: int = 320,
    include_benchmarks: bool = True,
    continue_on_price_error: bool = True,
    check_sources: bool = False,
    fetch_sec_metadata: bool = False,
    fetch_companyfacts: bool = False,
    snapshot_sources: bool = False,
    source_snapshot_dir: Path = DEFAULT_SOURCE_SNAPSHOT_DIR,
) -> HindsightRefreshResult:
    """Run the complete historical-pattern lab refresh without changing live scores."""

    effective_asof = asof_date or date.today()
    steps: list[HindsightRefreshStep] = []
    price_summary: list[dict[str, Any]] = []
    ensure_hindsight_tables(config)

    if not path.exists():
        write_default_hindsight_cases(path)
        steps.append(HindsightRefreshStep("case_seed_file", "OK", f"Wrote default case file at {path}."))
    else:
        steps.append(HindsightRefreshStep("case_seed_file", "OK", f"Using case file at {path}."))

    case_count = seed_hindsight_cases(config, path)
    steps.append(HindsightRefreshStep("case_registry", "OK", "Seeded historical leader and control cases.", case_count))

    event_count = seed_hindsight_events(config, path)
    steps.append(HindsightRefreshStep("event_ledger", "OK", "Seeded point-in-time event rows.", event_count))

    evidence_count = seed_hindsight_evidence(config, path)
    steps.append(HindsightRefreshStep("evidence_ledger", "OK", "Seeded official industry/fundamental evidence rows.", evidence_count))

    industry_profiles = build_hindsight_industry_profiles(config, path=path)
    industry_profile_summary_payload = industry_profile_summary(industry_profiles)
    steps.append(
        HindsightRefreshStep(
            "industry_profiles",
            "OK",
            "Built industry evidence profiles from PIT event and evidence ledgers.",
            len(industry_profiles),
        )
    )

    if fetch_prices:
        try:
            price_summary = fetch_hindsight_prices(
                config,
                path=path,
                provider=provider,
                lookback_days=lookback_days,
                include_benchmarks=include_benchmarks,
            )
            rows_inserted = sum(int(row.get("rows_inserted") or 0) for row in price_summary)
            rows_fetched = sum(int(row.get("rows_fetched") or 0) for row in price_summary)
            status = "OK" if rows_inserted else "WARNING"
            detail = (
                "Fetched public daily prices for cases and fixed replay benchmarks."
                if rows_inserted
                else "No public daily price rows were inserted; technical replay gates may remain DATA GAP."
            )
            steps.append(HindsightRefreshStep("public_price_history", status, detail, rows_inserted))
        except Exception as exc:
            if not continue_on_price_error:
                raise
            steps.append(
                HindsightRefreshStep(
                    "public_price_history",
                    "WARNING",
                    f"Price fetch skipped after provider error: {type(exc).__name__}: {exc}",
                    0,
                )
            )
    else:
        steps.append(
            HindsightRefreshStep(
                "public_price_history",
                "SKIPPED",
                "Price fetch disabled; replay uses any price rows already loaded.",
                0,
            )
        )

    results = scan_hindsight_cases(config, path=path, persist=True)
    steps.append(
        HindsightRefreshStep(
            "case_scan",
            "OK",
            "Scanned case windows and wrote pattern observations.",
            len(results),
        )
    )

    gates = build_hindsight_replay_gates(config, path=path, persist=True, asof_date=effective_asof)
    steps.append(HindsightRefreshStep("replay_gates", "OK", "Built timing, technical, and first-session replay gates.", len(gates)))

    links = build_hindsight_observation_links(config)
    steps.append(HindsightRefreshStep("observation_links", "OK", "Linked observations to evidence, gates, or blockers.", len(links)))

    hypotheses, case_results = build_hindsight_hypothesis_registry(
        config,
        path=path,
        persist=True,
        asof_date=effective_asof,
    )
    steps.append(
        HindsightRefreshStep(
            "hypothesis_registry",
            "OK",
            f"Built {len(hypotheses)} candidate mechanisms across {len(case_results)} case rows.",
            len(case_results),
        )
    )

    source_audit = build_hindsight_source_audit(config, check_remote=check_sources)
    source_summary = source_audit_summary(source_audit)
    steps.append(
        HindsightRefreshStep(
            "source_audit",
            "OK",
            "Built official source audit from event and evidence ledgers.",
            len(source_audit),
        )
    )

    sec_metadata = build_hindsight_sec_filing_metadata(config, fetch_remote=fetch_sec_metadata)
    sec_summary = sec_filing_metadata_summary(sec_metadata)
    steps.append(
        HindsightRefreshStep(
            "sec_filing_metadata",
            "OK",
            "Built SEC filing metadata from event source URLs and optional submissions API data.",
            len(sec_metadata),
        )
    )

    companyfacts, companyfacts_status = build_hindsight_companyfacts(config, fetch_remote=fetch_companyfacts)
    companyfacts_summary_payload = companyfacts_summary(companyfacts, companyfacts_status)
    steps.append(
        HindsightRefreshStep(
            "sec_companyfacts",
            "OK" if fetch_companyfacts else "SKIPPED",
            (
                "Fetched SEC companyfacts candidates for review."
                if fetch_companyfacts
                else "SEC companyfacts fetch disabled; candidate facts remain unloaded."
            ),
            len(companyfacts),
        )
    )

    source_snapshots: list[dict[str, object]] = []
    if snapshot_sources:
        snapshots = fetch_hindsight_source_snapshots(config, output_dir=source_snapshot_dir)
        source_snapshots = [snapshot.to_dict() for snapshot in snapshots]
        steps.append(
            HindsightRefreshStep(
                "source_snapshots",
                "OK",
                f"Fetched public source snapshots into {source_snapshot_dir}.",
                len(source_snapshots),
            )
        )
    else:
        steps.append(
            HindsightRefreshStep(
                "source_snapshots",
                "SKIPPED",
                "Public source snapshot fetch disabled.",
                0,
            )
        )

    playbook_path = generate_hindsight_pattern_playbook(
        config,
        output_dir=output_dir,
        asof_date=effective_asof,
        source_snapshot_dir=source_snapshot_dir,
    )
    steps.append(HindsightRefreshStep("pattern_playbook", "OK", f"Generated research playbook at {playbook_path}.", 1))

    return HindsightRefreshResult(
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        asof_date=effective_asof.isoformat(),
        case_path=str(path),
        price_provider=provider,
        price_rows_fetched=sum(int(row.get("rows_fetched") or 0) for row in price_summary),
        price_rows_inserted=sum(int(row.get("rows_inserted") or 0) for row in price_summary),
        playbook_path=str(playbook_path),
        config_hash=config_hash(config),
        git_commit=get_git_commit() or "unknown",
        steps=steps,
        row_counts=_row_counts(config),
        price_summary=price_summary,
        source_audit=[item.to_dict() for item in source_audit],
        source_audit_summary=source_summary,
        source_snapshots=source_snapshots,
        sec_filing_metadata=[item.to_dict() for item in sec_metadata],
        sec_filing_metadata_summary=sec_summary,
        companyfacts_candidates=[item.to_dict() for item in companyfacts],
        companyfacts_status=[item.to_dict() for item in companyfacts_status],
        companyfacts_summary=companyfacts_summary_payload,
        industry_profiles=[item.to_dict() for item in industry_profiles],
        industry_profile_summary=industry_profile_summary_payload,
    )


def _row_counts(config: SectorScoutConfig) -> dict[str, int]:
    ensure_hindsight_tables(config)
    return {table: _row_count(config, table) for table in HINDSIGHT_REFRESH_TABLES}


def _row_count(config: SectorScoutConfig, table: str) -> int:
    with connect_database(config.database.path) as connection:
        exists = connection.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'main' AND table_name = ?
            """,
            [table],
        ).fetchone()
        if exists is None:
            return 0
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
