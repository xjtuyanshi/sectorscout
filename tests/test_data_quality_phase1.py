from datetime import date
from pathlib import Path

import duckdb

from sectorscout.config import SectorScoutConfig
from sectorscout.data_quality import compute_data_quality, persist_data_quality
from sectorscout.db import initialize_database
from sectorscout.ingest import ingest_prices_csv, ingest_universe_csv


def test_data_quality_counts_provider_fallback_and_missing_prices(tmp_path: Path) -> None:
    config = SectorScoutConfig.model_validate(
        {
            "database": {"path": tmp_path / "test.duckdb"},
            "providers": {"prices_fallback": "yfinance"},
        }
    )
    initialize_database(config)
    ingest_universe_csv(config, "data/fixtures/universe_sample.csv", provider="fixture")
    ingest_prices_csv(config, "data/fixtures/prices_sample.csv", provider="yfinance")
    report = compute_data_quality(config, date(2024, 11, 29))
    assert report.total_symbols == 4
    assert report.symbols_with_price_data == 3
    assert report.symbols_missing_price_data == 1
    assert report.stale_price_count == 1
    assert report.split_adjustment_warnings == 1
    assert report.fallback_to_yfinance_count == 4
    assert report.provider_mix == {"yfinance": 4}


def test_data_quality_persists_report(tmp_path: Path) -> None:
    config = SectorScoutConfig.model_validate(
        {"database": {"path": tmp_path / "test.duckdb"}}
    )
    initialize_database(config)
    ingest_universe_csv(config, "data/fixtures/universe_sample.csv", provider="fixture")
    ingest_prices_csv(config, "data/fixtures/prices_sample.csv", provider="fixture")
    report = compute_data_quality(config, date(2024, 11, 29))
    persist_data_quality(config, report)
    with duckdb.connect(str(config.database.path)) as connection:
        row = connection.execute(
            "SELECT total_symbols, symbols_missing_price_data FROM data_quality_daily"
        ).fetchone()
    assert row == (4, 1)

