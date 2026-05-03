from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sectorscout.cli import app
from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database, initialize_database
from sectorscout.execution import generate_execution_decisions
from sectorscout.ledger import generate_trade_ledger_qa
from sectorscout.lifecycle import generate_position_lifecycle
from sectorscout.prices import PriceSnapshotValidationError, create_frozen_price_snapshot


ASOF = date(2024, 12, 3)
SIGNAL_ASOF = date(2024, 11, 29)
NEXT_SESSION = date(2024, 12, 2)
BENCHMARKS = ("QQQ", "SMH", "SPY")


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
    *,
    low: float | None = None,
) -> None:
    low_value = close - 1 if low is None else low
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
                low_value,
                close,
                close,
                close + 1,
                low_value,
                close,
                provider,
            ],
        )


def _insert_benchmark_prices(config: SectorScoutConfig, price_date: date) -> None:
    for symbol in BENCHMARKS:
        _insert_price(config, symbol, price_date, 500.0, "FMP")


def _insert_manual_price_snapshot(
    config: SectorScoutConfig,
    *,
    price_snapshot_id: str,
    symbol: str,
    price_date: date,
    close: float,
    provider: str = "FMP",
) -> None:
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO price_snapshot_runs (
                price_snapshot_id, asof_date, provider_priority_json,
                provider_mix_json, duplicate_provider_rows_dropped,
                min_price_date, max_price_date, raw_row_count,
                chosen_row_count, config_hash, git_commit, created_at_utc
            ) VALUES (
                ?, ?, '["FMP", "yfinance", "fixture"]', '{"FMP": 1}',
                0, ?, ?, 1, 1, 'snapshot_hash', 'snapshot_commit', now()
            )
            """,
            [price_snapshot_id, price_date, price_date, price_date],
        )
        connection.execute(
            """
            INSERT INTO price_snapshot_rows (
                price_snapshot_id, symbol, price_date, adj_open,
                adj_high, adj_low, adj_close, adj_volume, provider,
                adjustment_warning
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1000000, ?, false)
            """,
            [
                price_snapshot_id,
                symbol,
                price_date,
                close,
                close + 1,
                close - 1,
                close,
                provider,
            ],
        )


def _insert_signal(config: SectorScoutConfig) -> None:
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            INSERT INTO signals (
                asof_date, symbol, theme_id, setup_type, state,
                action_category, actionable, reason, execution_model,
                entry_trigger, stop_loss, reward_risk, setup_data_present,
                execution_data_quality_pass, price_snapshot_quality_pass,
                data_quality_pass,
                data_quality_reason, market_gate_pass, market_gate_reason,
                market_regime_risk_state, portfolio_risk_pass,
                portfolio_risk_reason,
                signal_generated_at_utc, config_hash, git_commit,
                data_snapshot_id, universe_version, theme_version
            ) VALUES (
                ?, 'MU', 'ai-memory', 'VCP', 'TRIGGERED',
                'Triggered setup candidate', false, 'Phase 4 candidate', 'next_open',
                100, 95, 2.0, true,
                false, true, false,
                'Phase 5 execution validation required.', true, 'Market gate pass.',
                'RISK_ON', true, 'Portfolio gate pass.',
                '2024-11-29T18:00:00+00:00', 'signal_hash', 'signal_commit',
                'signal_snapshot', 'signal_universe', 'signal_theme'
            )
            """,
            [SIGNAL_ASOF],
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


def test_phase5b4_execution_can_read_persisted_price_snapshot(tmp_path: Path) -> None:
    snapshot_config = _config(
        tmp_path,
        providers={"prices_primary": "FMP", "prices_fallback": "yfinance"},
    )
    _insert_signal(snapshot_config)
    _insert_price(snapshot_config, "MU", NEXT_SESSION, 101.0, "FMP")
    _insert_price(snapshot_config, "MU", NEXT_SESSION, 999.0, "yfinance")
    price_snapshot_id = create_frozen_price_snapshot(
        snapshot_config,
        NEXT_SESSION,
        symbols=["MU"],
    ).price_snapshot_id
    execution_config = SectorScoutConfig.model_validate(
        {
            "database": {"path": snapshot_config.database.path},
            "providers": {"prices_primary": "yfinance", "prices_fallback": "FMP"},
        }
    )

    result = generate_execution_decisions(
        execution_config,
        SIGNAL_ASOF,
        price_snapshot_id=price_snapshot_id,
    ).to_dict()

    decision = result["decisions"][0]
    assert result["price_snapshot_id"] == price_snapshot_id
    assert decision["decision"] == "SIMULATED_NEXT_OPEN_ACCEPTED"
    assert decision["actual_entry_price"] == 101.0
    assert decision["price_snapshot_id"] == price_snapshot_id
    with connect_database(snapshot_config.database.path) as connection:
        persisted = connection.execute(
            "SELECT er.price_snapshot_id, ed.price_snapshot_id FROM execution_runs er JOIN execution_decisions ed USING (execution_run_id)"
        ).fetchone()
    assert persisted == (price_snapshot_id, price_snapshot_id)


def test_phase5b4_lifecycle_inherits_execution_price_snapshot(tmp_path: Path) -> None:
    snapshot_config = _config(
        tmp_path,
        providers={"prices_primary": "FMP", "prices_fallback": "yfinance"},
    )
    _insert_signal(snapshot_config)
    _insert_price(snapshot_config, "MU", NEXT_SESSION, 101.0, "FMP", low=100.0)
    _insert_price(snapshot_config, "MU", NEXT_SESSION, 101.0, "yfinance", low=80.0)
    _insert_benchmark_prices(snapshot_config, NEXT_SESSION)
    price_snapshot_id = create_frozen_price_snapshot(
        snapshot_config,
        NEXT_SESSION,
        symbols=["MU", *BENCHMARKS],
    ).price_snapshot_id
    lifecycle_config = SectorScoutConfig.model_validate(
        {
            "database": {"path": snapshot_config.database.path},
            "providers": {"prices_primary": "yfinance", "prices_fallback": "FMP"},
        }
    )
    execution = generate_execution_decisions(
        lifecycle_config,
        SIGNAL_ASOF,
        price_snapshot_id=price_snapshot_id,
    ).to_dict()

    result = generate_position_lifecycle(
        lifecycle_config,
        execution["execution_run_id"],
        NEXT_SESSION,
    ).to_dict()

    assert result["price_snapshot_id"] == price_snapshot_id
    assert result["positions"][0]["status"] == "OPEN"
    assert result["positions"][0]["exit_reason"] is None
    with connect_database(snapshot_config.database.path) as connection:
        persisted = connection.execute(
            "SELECT price_snapshot_id FROM lifecycle_runs"
        ).fetchone()
    assert persisted == (price_snapshot_id,)


def test_phase5b3_execution_rejects_unknown_price_snapshot_id(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_signal(config)

    with pytest.raises(PriceSnapshotValidationError, match="UNKNOWN_PRICE_SNAPSHOT_ID"):
        generate_execution_decisions(config, SIGNAL_ASOF, price_snapshot_id="missing-snapshot")


def test_phase5b3_execution_rejects_snapshot_missing_required_symbol(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_signal(config)
    _insert_price(config, "NVDA", NEXT_SESSION, 120.0, "FMP")
    price_snapshot_id = create_frozen_price_snapshot(
        config,
        NEXT_SESSION,
        symbols=["NVDA"],
    ).price_snapshot_id

    with pytest.raises(PriceSnapshotValidationError, match="PRICE_SNAPSHOT_MISSING_SYMBOLS"):
        generate_execution_decisions(config, SIGNAL_ASOF, price_snapshot_id=price_snapshot_id)


def test_phase5b3_execution_rejects_snapshot_undercovered_required_symbol(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _insert_signal(config)
    _insert_price(config, "MU", SIGNAL_ASOF, 99.0, "FMP")
    _insert_price(config, "SPY", NEXT_SESSION, 500.0, "FMP")
    price_snapshot_id = create_frozen_price_snapshot(config, NEXT_SESSION).price_snapshot_id

    with pytest.raises(PriceSnapshotValidationError, match="PRICE_SNAPSHOT_MISSING_SYMBOLS"):
        generate_execution_decisions(config, SIGNAL_ASOF, price_snapshot_id=price_snapshot_id)


def test_phase5b3_execution_rejects_snapshot_missing_exact_next_session(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _insert_signal(config)
    _insert_price(config, "MU", date(2024, 12, 3), 103.0, "FMP")
    price_snapshot_id = create_frozen_price_snapshot(
        config,
        date(2024, 12, 3),
        symbols=["MU"],
    ).price_snapshot_id

    with pytest.raises(
        PriceSnapshotValidationError,
        match="PRICE_SNAPSHOT_MISSING_REQUIRED_SESSIONS",
    ):
        generate_execution_decisions(config, SIGNAL_ASOF, price_snapshot_id=price_snapshot_id)


def test_phase5b3_lifecycle_rejects_undercovered_price_snapshot(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_signal(config)
    _insert_price(config, "MU", NEXT_SESSION, 101.0, "FMP", low=100.0)
    price_snapshot_id = create_frozen_price_snapshot(
        config,
        NEXT_SESSION,
        symbols=["MU"],
    ).price_snapshot_id
    execution = generate_execution_decisions(
        config,
        SIGNAL_ASOF,
        price_snapshot_id=price_snapshot_id,
    ).to_dict()

    with pytest.raises(PriceSnapshotValidationError, match="PRICE_SNAPSHOT_UNDERCOVERED"):
        generate_position_lifecycle(
            config,
            execution["execution_run_id"],
            date(2024, 12, 3),
        )


def test_phase5b3_lifecycle_rejects_snapshot_missing_exact_entry_session(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _insert_signal(config)
    _insert_price(config, "MU", NEXT_SESSION, 101.0, "FMP", low=100.0)
    execution = generate_execution_decisions(config, SIGNAL_ASOF).to_dict()
    _insert_manual_price_snapshot(
        config,
        price_snapshot_id="sparse-entry-snapshot",
        symbol="MU",
        price_date=date(2024, 12, 3),
        close=103.0,
    )
    for symbol in BENCHMARKS:
        _insert_manual_price_snapshot(
            config,
            price_snapshot_id="sparse-entry-snapshot",
            symbol=symbol,
            price_date=date(2024, 12, 3),
            close=500.0,
        )

    with pytest.raises(
        PriceSnapshotValidationError,
        match="PRICE_SNAPSHOT_MISSING_REQUIRED_SESSIONS",
    ):
        generate_position_lifecycle(
            config,
            execution["execution_run_id"],
            date(2024, 12, 3),
            price_snapshot_id="sparse-entry-snapshot",
        )


def test_phase5b3_lifecycle_rejects_snapshot_missing_benchmarks(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_signal(config)
    _insert_price(config, "MU", NEXT_SESSION, 101.0, "FMP", low=100.0)
    price_snapshot_id = create_frozen_price_snapshot(
        config,
        NEXT_SESSION,
        symbols=["MU"],
    ).price_snapshot_id
    execution = generate_execution_decisions(
        config,
        SIGNAL_ASOF,
        price_snapshot_id=price_snapshot_id,
    ).to_dict()

    with pytest.raises(PriceSnapshotValidationError, match="PRICE_SNAPSHOT_MISSING_SYMBOLS"):
        generate_position_lifecycle(
            config,
            execution["execution_run_id"],
            NEXT_SESSION,
        )


def test_phase5b3_trade_ledger_marks_persisted_price_snapshot_mode(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_signal(config)
    _insert_price(config, "MU", NEXT_SESSION, 101.0, "FMP", low=100.0)
    _insert_benchmark_prices(config, NEXT_SESSION)
    price_snapshot_id = create_frozen_price_snapshot(
        config,
        NEXT_SESSION,
        symbols=["MU", *BENCHMARKS],
    ).price_snapshot_id
    execution = generate_execution_decisions(
        config,
        SIGNAL_ASOF,
        price_snapshot_id=price_snapshot_id,
    ).to_dict()
    lifecycle = generate_position_lifecycle(
        config,
        execution["execution_run_id"],
        NEXT_SESSION,
    ).to_dict()

    result = generate_trade_ledger_qa(config, lifecycle["lifecycle_run_id"]).to_dict()

    assert result["provenance"]["source_signal_config_hash"] == "signal_hash"
    assert result["provenance"]["price_snapshot_mode"] == "persisted_price_snapshot"
    assert result["provenance"]["execution_price_snapshot_id"] == price_snapshot_id
    assert result["provenance"]["lifecycle_price_snapshot_id"] == price_snapshot_id
    with connect_database(config.database.path) as connection:
        mode = connection.execute("SELECT price_snapshot_mode FROM lifecycle_qa").fetchone()
    assert mode == ("persisted_price_snapshot",)


def test_phase5b3_execution_cli_accepts_price_snapshot_id(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        providers={"prices_primary": "FMP", "prices_fallback": "yfinance"},
    )
    _insert_signal(config)
    _insert_price(config, "MU", NEXT_SESSION, 101.0, "FMP")
    _insert_price(config, "MU", NEXT_SESSION, 999.0, "yfinance")
    price_snapshot_id = create_frozen_price_snapshot(
        config,
        NEXT_SESSION,
        symbols=["MU"],
    ).price_snapshot_id
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
database:
  path: {config.database.path}
providers:
  prices_primary: yfinance
  prices_fallback: FMP
""",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "execution-decisions",
            "--asof",
            SIGNAL_ASOF.isoformat(),
            "--price-snapshot-id",
            price_snapshot_id,
            "--no-persist",
            "--config",
            str(config_path),
        ],
    )
    payload = json.loads(result.stdout)

    assert result.exit_code == 0
    assert payload["price_snapshot_id"] == price_snapshot_id
    assert payload["decisions"][0]["actual_entry_price"] == 101.0
    for forbidden in ("cagr", "sharpe", "drawdown", "win_rate", "profit_factor"):
        assert forbidden not in result.stdout.lower()


def test_phase5b3_lifecycle_cli_accepts_price_snapshot_id(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_signal(config)
    _insert_price(config, "MU", NEXT_SESSION, 101.0, "FMP", low=100.0)
    _insert_benchmark_prices(config, NEXT_SESSION)
    price_snapshot_id = create_frozen_price_snapshot(
        config,
        NEXT_SESSION,
        symbols=["MU", *BENCHMARKS],
    ).price_snapshot_id
    execution = generate_execution_decisions(
        config,
        SIGNAL_ASOF,
        price_snapshot_id=price_snapshot_id,
    ).to_dict()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"database:\n  path: {config.database.path}\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "position-lifecycle",
            "--execution-run-id",
            execution["execution_run_id"],
            "--through",
            NEXT_SESSION.isoformat(),
            "--price-snapshot-id",
            price_snapshot_id,
            "--no-persist",
            "--config",
            str(config_path),
        ],
    )
    payload = json.loads(result.stdout)

    assert result.exit_code == 0
    assert payload["price_snapshot_id"] == price_snapshot_id
    assert payload["positions"][0]["status"] == "OPEN"
    for forbidden in ("cagr", "sharpe", "drawdown", "win_rate", "profit_factor"):
        assert forbidden not in result.stdout.lower()
