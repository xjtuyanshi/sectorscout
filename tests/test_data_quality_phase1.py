from datetime import date
from pathlib import Path

import duckdb

from sectorscout.config import SectorScoutConfig
from sectorscout.data_quality import (
    compute_data_quality,
    compute_historical_data_quality,
    persist_data_quality,
)
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


def test_historical_data_quality_uses_asof_tradable_universe(tmp_path: Path) -> None:
    config = SectorScoutConfig.model_validate(
        {"database": {"path": tmp_path / "test.duckdb"}}
    )
    initialize_database(config)
    universe_csv = tmp_path / "universe.csv"
    prices_csv = tmp_path / "prices.csv"
    universe_csv.write_text(
        "symbol,name,exchange,security_type,is_etf,is_active,ipo_date,delist_date,first_seen_at,last_seen_at\n"
        "LIVE,Live Co,NASDAQ,common_stock,false,true,2020-01-01,,2020-01-01,2024-12-31\n"
        "DEAD,Dead Co,NASDAQ,common_stock,false,false,2020-01-01,2025-01-01,2020-01-01,2024-12-31\n"
        "FUTURE,Future Co,NASDAQ,common_stock,false,true,2025-01-01,,2025-01-01,2025-12-31\n"
        "SPY,SPDR S&P 500 ETF,NYSE,etf,true,true,1993-01-29,,2020-01-01,2024-12-31\n",
        encoding="utf-8",
    )
    prices_csv.write_text(
        "symbol,date,open,high,low,close,volume,adj_open,adj_high,adj_low,adj_close,adj_volume,adjustment_warning\n"
        "LIVE,2024-11-29,10,11,9,10,1000,10,11,9,10,1000,false\n"
        "SPY,2024-11-29,100,101,99,100,1000,100,101,99,100,1000,false\n",
        encoding="utf-8",
    )
    ingest_universe_csv(config, universe_csv, provider="fixture")
    ingest_prices_csv(config, prices_csv, provider="fixture")

    report = compute_historical_data_quality(config, date(2024, 11, 29))

    assert report.universe_mode == "historical"
    assert report.total_symbols == 2
    assert report.symbols_with_price_data == 1
    assert report.symbols_missing_price_data == 1
    assert report.benchmark_symbols_with_price_data == 1
