from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from typer.testing import CliRunner

from sectorscout.cli import app
from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database, initialize_database
from sectorscout.prices import create_frozen_price_snapshot


ASOF = date(2024, 12, 3)


def _config(tmp_path: Path, **overrides) -> SectorScoutConfig:
    raw = {"database": {"path": tmp_path / "test.duckdb"}}
    raw.update(overrides)
    config = SectorScoutConfig.model_validate(raw)
    initialize_database(config)
    return config


def _insert_price(
    config: SectorScoutConfig,
    symbol: str,
    price_date: date,
    close: float,
    provider: str,
) -> None:
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            INSERT INTO daily_prices (
                symbol, price_date, open, high, low, close, volume,
                adj_open, adj_high, adj_low, adj_close, adj_volume,
                provider, is_adjusted, adjustment_warning, ingested_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, 1000000, ?, ?, ?, ?, 1000000, ?, true, false, now())
            """,
            [
                symbol,
                price_date,
                close,
                close + 1,
                close - 1,
                close,
                close,
                close + 1,
                close - 1,
                close,
                provider,
            ],
        )


def test_phase5b3_persists_frozen_price_snapshot_scaffold(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        providers={"prices_primary": "FMP", "prices_fallback": "yfinance"},
    )
    _insert_price(config, "MU", date(2024, 12, 2), 100.0, "FMP")
    _insert_price(config, "MU", date(2024, 12, 2), 999.0, "yfinance")
    _insert_price(config, "NVDA", date(2024, 12, 3), 120.0, "yfinance")

    result = create_frozen_price_snapshot(config, ASOF).to_dict()

    assert result["provider_priority"] == ["FMP", "yfinance", "fixture"]
    assert result["provider_mix"] == {"FMP": 1, "yfinance": 1}
    assert result["duplicate_provider_rows_dropped"] == 1
    assert result["min_price_date"] == "2024-12-02"
    assert result["max_price_date"] == "2024-12-03"
    assert result["raw_row_count"] == 3
    assert result["chosen_row_count"] == 2
    with connect_database(config.database.path) as connection:
        run = connection.execute(
            """
            SELECT duplicate_provider_rows_dropped, raw_row_count, chosen_row_count
            FROM price_snapshot_runs
            """
        ).fetchone()
        rows = connection.execute(
            """
            SELECT symbol, price_date, adj_close, provider
            FROM price_snapshot_rows
            ORDER BY symbol
            """
        ).fetchall()
    assert run == (1, 3, 2)
    assert rows == [
        ("MU", date(2024, 12, 2), 100.0, "FMP"),
        ("NVDA", date(2024, 12, 3), 120.0, "yfinance"),
    ]


def test_phase5b3_price_snapshot_cli_has_no_formal_metric_terms(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_price(config, "MU", date(2024, 12, 2), 100.0, "FMP")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"database:\n  path: {config.database.path}\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "price-snapshot",
            "--asof",
            ASOF.isoformat(),
            "--no-persist",
            "--config",
            str(config_path),
        ],
    )
    output = result.stdout.lower()
    payload = json.loads(result.stdout)

    assert result.exit_code == 0
    assert payload["chosen_row_count"] == 1
    for forbidden in (
        "cagr",
        "sharpe",
        "max drawdown",
        "annual return",
        "annual returns",
        "win_rate",
        "win rate",
        "profit_factor",
        "profit factor",
        "expectancy",
        "edge claim",
        "claim edge",
    ):
        assert forbidden not in output
