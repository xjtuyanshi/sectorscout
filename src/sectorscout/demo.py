from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from sectorscout.config import SectorScoutConfig
from sectorscout.data_quality import compute_historical_data_quality, persist_data_quality
from sectorscout.db import connect_database, initialize_database
from sectorscout.indicators import compute_technical_indicators
from sectorscout.ingest import (
    ingest_corporate_actions_csv,
    ingest_fundamental_facts_csv,
    ingest_prices_csv,
    ingest_theme_members_csv,
    ingest_themes_csv,
    ingest_universe_csv,
)
from sectorscout.intel.chandler_seed import seed_chandler_fixture
from sectorscout.intel.report import generate_intel_daily_report
from sectorscout.intel.storage import ensure_intel_tables
from sectorscout.market_regime import compute_market_regime
from sectorscout.scoring import run_scoring
from sectorscout.setups import detect_setups


DEMO_ASOF_DATE = date(2024, 11, 29)


@dataclass(frozen=True)
class DemoInitResult:
    asof_date: str
    database_path: str
    report_path: str
    seeded_raw_item_id: str
    row_counts: dict[str, int]
    readiness: list[dict[str, object]]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _fixture_path(config: SectorScoutConfig, filename: str, fixtures_dir: str | Path | None = None) -> Path:
    root = Path(fixtures_dir) if fixtures_dir is not None else Path(config.data.fixture_dir)
    return root / filename


def _table_exists(config: SectorScoutConfig, table: str) -> bool:
    with connect_database(config.database.path) as connection:
        row = connection.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'main' AND table_name = ?
            """,
            [table],
        ).fetchone()
    return row is not None


def _row_count(config: SectorScoutConfig, table: str) -> int:
    if not _table_exists(config, table):
        return 0
    with connect_database(config.database.path) as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def demo_readiness(
    config: SectorScoutConfig,
    *,
    asof_date: date = DEMO_ASOF_DATE,
    reports_dir: str | Path = "data/intel/reports",
) -> list[dict[str, object]]:
    db_exists = Path(config.database.path).exists()
    report_path = Path(reports_dir) / f"intel_daily_{asof_date.isoformat()}.md"
    counts = {
        table: _row_count(config, table) if db_exists else 0
        for table in [
            "symbols",
            "daily_prices",
            "theme_scores",
            "market_regime",
            "intel_trade_views",
        ]
    }
    checks = [
        ("database_exists", db_exists, str(config.database.path)),
        ("universe_loaded", counts["symbols"] > 0, counts["symbols"]),
        ("prices_loaded", counts["daily_prices"] > 0, counts["daily_prices"]),
        ("theme_scores_ready", counts["theme_scores"] > 0, counts["theme_scores"]),
        ("market_regime_ready", counts["market_regime"] > 0, counts["market_regime"]),
        ("intel_seed_ready", counts["intel_trade_views"] > 0, counts["intel_trade_views"]),
        ("daily_report_ready", report_path.exists(), str(report_path)),
    ]
    return [{"check": name, "ok": ok, "detail": detail} for name, ok, detail in checks]


def run_demo_init(
    config: SectorScoutConfig,
    *,
    asof_date: date = DEMO_ASOF_DATE,
    fixtures_dir: str | Path | None = None,
    reports_dir: str | Path = "data/intel/reports",
    reset: bool = False,
) -> DemoInitResult:
    db_path = Path(config.database.path)
    if reset:
        for path in [db_path, db_path.with_suffix(db_path.suffix + ".wal")]:
            if path.exists():
                path.unlink()
    initialize_database(config)
    ingest_universe_csv(config, _fixture_path(config, "universe_sample.csv", fixtures_dir), provider="fixture")
    ingest_prices_csv(config, _fixture_path(config, "prices_sample.csv", fixtures_dir), provider="fixture")
    ingest_corporate_actions_csv(
        config,
        _fixture_path(config, "corporate_actions_sample.csv", fixtures_dir),
        source="fixture",
    )
    ingest_fundamental_facts_csv(
        config,
        _fixture_path(config, "fundamental_facts_sample.csv", fixtures_dir),
        source="fixture",
    )
    ingest_themes_csv(config, _fixture_path(config, "themes_sample.csv", fixtures_dir), source="fixture")
    ingest_theme_members_csv(
        config,
        _fixture_path(config, "theme_members_sample.csv", fixtures_dir),
        source="fixture",
    )
    quality = compute_historical_data_quality(config, asof_date)
    persist_data_quality(config, quality)
    compute_technical_indicators(config, asof_date, persist=True)
    compute_market_regime(config, asof_date, persist=True)
    run_scoring(config, asof_date)
    detect_setups(config, asof_date, persist=True)
    ensure_intel_tables(config)
    raw_item_id = seed_chandler_fixture(config, asof_date=asof_date)
    report_path = generate_intel_daily_report(config, asof_date, output_dir=reports_dir)
    row_counts = {
        table: _row_count(config, table)
        for table in [
            "symbols",
            "daily_prices",
            "theme_scores",
            "stock_scores",
            "signals",
            "intel_trade_views",
            "intel_review_marks",
        ]
    }
    return DemoInitResult(
        asof_date=asof_date.isoformat(),
        database_path=str(config.database.path),
        report_path=str(report_path),
        seeded_raw_item_id=raw_item_id,
        row_counts=row_counts,
        readiness=demo_readiness(config, asof_date=asof_date, reports_dir=reports_dir),
    )
