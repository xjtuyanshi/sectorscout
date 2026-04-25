from pathlib import Path

import duckdb
import pytest

from sectorscout.config import SectorScoutConfig
from sectorscout.db import initialize_database
from sectorscout.ingest import IngestionError, ingest_prices_csv, ingest_universe_csv


def test_ingest_universe_and_adjusted_prices(tmp_path: Path) -> None:
    config = SectorScoutConfig.model_validate(
        {"database": {"path": tmp_path / "test.duckdb"}}
    )
    initialize_database(config)
    universe_csv = Path("data/fixtures/universe_sample.csv")
    prices_csv = Path("data/fixtures/prices_sample.csv")
    assert ingest_universe_csv(config, universe_csv, provider="fixture") == 4
    assert ingest_prices_csv(config, prices_csv, provider="fixture") == 4
    with duckdb.connect(str(config.database.path)) as connection:
        symbol_count = connection.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        price_count = connection.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]
    assert symbol_count == 4
    assert price_count == 4


def test_prices_require_adjusted_ohlc_for_backtest_safety(tmp_path: Path) -> None:
    config = SectorScoutConfig.model_validate(
        {"database": {"path": tmp_path / "test.duckdb"}}
    )
    initialize_database(config)
    bad_csv = tmp_path / "bad_prices.csv"
    bad_csv.write_text(
        "symbol,date,open,high,low,close,volume,adj_close,adj_volume\n"
        "AAPL,2024-11-29,1,2,1,2,100,2,100\n",
        encoding="utf-8",
    )
    with pytest.raises(IngestionError, match="Adjusted OHLCV is required"):
        ingest_prices_csv(config, bad_csv, provider="fixture")

