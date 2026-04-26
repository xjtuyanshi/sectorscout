from __future__ import annotations

import inspect
import json
from datetime import date
from pathlib import Path

from typer.testing import CliRunner

from sectorscout.cli import app
from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database, initialize_database
from sectorscout.ledger import generate_trade_ledger_qa
import sectorscout.ledger as ledger_module


LIFECYCLE_RUN_ID = "lifecycle-run"
EXECUTION_RUN_ID = "execution-run"


def _config(tmp_path: Path) -> SectorScoutConfig:
    config = SectorScoutConfig.model_validate({"database": {"path": tmp_path / "test.duckdb"}})
    initialize_database(config)
    return config


def _insert_lifecycle_context(
    config: SectorScoutConfig,
    *,
    lifecycle_config_hash: str = "lifecycle_hash",
    execution_config_hash: str = "execution_hash",
    lifecycle_snapshot: str = "lifecycle_snapshot",
    execution_snapshot: str = "execution_snapshot",
    source_signal_config_hash: str = "signal_hash",
    source_signal_git_commit: str = "signal_commit",
    source_universe_version: str = "signal_universe",
    source_theme_version: str = "signal_theme",
) -> None:
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            INSERT INTO execution_runs (
                execution_run_id, asof_date, execution_model,
                execution_generated_at_utc, execution_config_hash,
                execution_git_commit, execution_data_snapshot_id,
                source_signal_snapshot_id, source_signal_config_hash,
                source_signal_git_commit, source_universe_version,
                source_theme_version, mixed_source_signal_metadata,
                created_at_utc
            ) VALUES (
                ?, DATE '2024-11-29', 'next_open',
                '2024-11-29T18:01:00+00:00', ?, 'execution_commit',
                ?, 'signal_snapshot', ?, ?, ?, ?, false,
                '2024-11-29T18:01:00+00:00'
            )
            """,
            [
                EXECUTION_RUN_ID,
                execution_config_hash,
                execution_snapshot,
                source_signal_config_hash,
                source_signal_git_commit,
                source_universe_version,
                source_theme_version,
            ],
        )
        connection.execute(
            """
            INSERT INTO lifecycle_runs (
                lifecycle_run_id, execution_run_id, through_date,
                lifecycle_generated_at_utc, lifecycle_config_hash,
                lifecycle_git_commit, lifecycle_data_snapshot_id, created_at_utc
            ) VALUES (
                ?, ?, DATE '2024-12-06',
                '2024-12-06T21:00:00+00:00', ?, 'lifecycle_commit',
                ?, '2024-12-06T21:00:00+00:00'
            )
            """,
            [LIFECYCLE_RUN_ID, EXECUTION_RUN_ID, lifecycle_config_hash, lifecycle_snapshot],
        )


def _insert_position(
    config: SectorScoutConfig,
    *,
    symbol: str = "TEST",
    setup_type: str = "VCP",
    execution_model: str = "next_open",
    entry_date: date = date(2024, 12, 2),
    status: str = "CLOSED",
    exit_date: date | None = date(2024, 12, 5),
    exit_price: float | None = 115.0,
    exit_reason: str | None = "HARD_STOP_INTRADAY",
) -> None:
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            INSERT INTO simulated_positions (
                lifecycle_run_id, execution_run_id, asof_date, symbol,
                theme_id, setup_type, execution_model, entry_date,
                entry_price, initial_stop_loss, risk_per_share, target_1r,
                target_2r, target_3r, status, exit_date, exit_price,
                exit_reason, lifecycle_generated_at_utc,
                lifecycle_config_hash, lifecycle_git_commit,
                lifecycle_data_snapshot_id
            ) VALUES (
                ?, ?, DATE '2024-11-29', ?, 'ai-memory', ?, ?,
                ?, 100, 90, 10, 110,
                120, 130, ?, ?, ?, ?,
                '2024-12-06T21:00:00+00:00', 'lifecycle_hash',
                'lifecycle_commit', 'lifecycle_snapshot'
            )
            """,
            [
                LIFECYCLE_RUN_ID,
                EXECUTION_RUN_ID,
                symbol,
                setup_type,
                execution_model,
                entry_date,
                status,
                exit_date,
                exit_price,
                exit_reason,
            ],
        )


def _insert_baseline_row(
    config: SectorScoutConfig,
    symbol: str,
    price_date: date,
    provider: str,
) -> None:
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            INSERT INTO baseline_price_series (
                lifecycle_run_id, symbol, price_date, adj_open, adj_high,
                adj_low, adj_close, adj_volume, provider,
                lifecycle_generated_at_utc, lifecycle_config_hash,
                lifecycle_git_commit, lifecycle_data_snapshot_id
            ) VALUES (
                ?, ?, ?, 100, 101, 99, 100, 1000000, ?,
                '2024-12-06T21:00:00+00:00', 'lifecycle_hash',
                'lifecycle_commit', 'lifecycle_snapshot'
            )
            """,
            [LIFECYCLE_RUN_ID, symbol, price_date, provider],
        )


def _insert_skip(config: SectorScoutConfig, reason: str) -> None:
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            INSERT INTO lifecycle_skips (
                lifecycle_run_id, execution_run_id, asof_date, symbol,
                theme_id, setup_type, execution_model, skip_reason,
                evidence_json, lifecycle_generated_at_utc,
                lifecycle_config_hash, lifecycle_git_commit,
                lifecycle_data_snapshot_id
            ) VALUES (
                ?, ?, DATE '2024-11-29', 'MISS', 'ai-memory', 'VCP',
                'next_open', ?, '{}', '2024-12-06T21:00:00+00:00',
                'lifecycle_hash', 'lifecycle_commit', 'lifecycle_snapshot'
            )
            """,
            [LIFECYCLE_RUN_ID, EXECUTION_RUN_ID, reason],
        )


def test_phase5b2_trade_ledger_records_position_qa_fields(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_lifecycle_context(config)
    _insert_position(config)

    result = generate_trade_ledger_qa(config, LIFECYCLE_RUN_ID).to_dict()

    row = result["trade_ledger_rows"][0]
    assert row["lifecycle_run_id"] == LIFECYCLE_RUN_ID
    assert row["execution_run_id"] == EXECUTION_RUN_ID
    assert row["asof_date"] == "2024-11-29"
    assert row["symbol"] == "TEST"
    assert row["execution_model"] == "next_open"
    assert row["entry_date"] == "2024-12-02"
    assert row["entry_price"] == 100.0
    assert row["initial_stop_loss"] == 90.0
    assert row["risk_per_share"] == 10.0
    assert row["status"] == "CLOSED"
    assert row["exit_date"] == "2024-12-05"
    assert row["exit_price"] == 115.0
    assert row["exit_reason"] == "HARD_STOP_INTRADAY"
    assert row["holding_days"] == 3
    assert row["calendar_holding_days"] == 3
    assert row["trading_holding_sessions"] == 4
    assert row["gross_r_multiple"] == 1.5
    assert row["qa_status"] == "CLOSED_WITH_COMPLETE_EXIT"
    assert row["source_signal_snapshot_id"] == "signal_snapshot"
    assert row["execution_config_hash"] == "execution_hash"
    assert row["execution_data_snapshot_id"] == "execution_snapshot"
    with connect_database(config.database.path) as connection:
        persisted = connection.execute(
            "SELECT symbol, gross_r_multiple, qa_status FROM trade_ledger"
        ).fetchone()
    assert persisted == ("TEST", 1.5, "CLOSED_WITH_COMPLETE_EXIT")


def test_phase5b3_closed_position_missing_exit_date_is_flagged(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_lifecycle_context(config)
    _insert_position(config, status="CLOSED", exit_date=None, exit_price=115.0)

    row = generate_trade_ledger_qa(config, LIFECYCLE_RUN_ID).to_dict()["trade_ledger_rows"][0]

    assert row["qa_status"] == "MISSING_EXIT_DATE"


def test_phase5b3_closed_position_missing_exit_price_is_flagged(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_lifecycle_context(config)
    _insert_position(config, status="CLOSED", exit_date=date(2024, 12, 5), exit_price=None)

    row = generate_trade_ledger_qa(config, LIFECYCLE_RUN_ID).to_dict()["trade_ledger_rows"][0]

    assert row["qa_status"] == "MISSING_EXIT_PRICE"
    assert row["gross_r_multiple"] is None


def test_phase5b3_weekend_calendar_days_differ_from_trading_sessions(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_lifecycle_context(config)
    _insert_position(
        config,
        entry_date=date(2024, 12, 6),
        exit_date=date(2024, 12, 9),
        exit_price=105.0,
    )

    row = generate_trade_ledger_qa(config, LIFECYCLE_RUN_ID).to_dict()["trade_ledger_rows"][0]

    assert row["calendar_holding_days"] == 3
    assert row["trading_holding_sessions"] == 2


def test_phase5b3_execution_model_is_part_of_trade_ledger_identity(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_lifecycle_context(config)
    _insert_position(config, symbol="TEST", setup_type="VCP", execution_model="next_open")
    _insert_position(config, symbol="TEST", setup_type="VCP", execution_model="next_close")

    result = generate_trade_ledger_qa(config, LIFECYCLE_RUN_ID).to_dict()

    assert len(result["trade_ledger_rows"]) == 2
    assert {row["execution_model"] for row in result["trade_ledger_rows"]} == {
        "next_open",
        "next_close",
    }
    with connect_database(config.database.path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM trade_ledger").fetchone()[0]
    assert count == 2


def test_phase5b2_open_position_has_no_exit_diagnostic(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_lifecycle_context(config)
    _insert_position(
        config,
        symbol="OPEN",
        status="OPEN",
        exit_date=None,
        exit_price=None,
        exit_reason=None,
    )

    result = generate_trade_ledger_qa(config, LIFECYCLE_RUN_ID).to_dict()

    row = result["trade_ledger_rows"][0]
    assert row["status"] == "OPEN"
    assert row["exit_date"] is None
    assert row["gross_r_multiple"] is None
    assert row["qa_status"] == "OPEN_POSITION"


def test_phase5b2_baseline_qa_reports_coverage_and_provider_mix(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_lifecycle_context(config)
    _insert_position(config)
    _insert_baseline_row(config, "SPY", date(2024, 12, 2), "FMP")
    _insert_baseline_row(config, "SPY", date(2024, 12, 3), "FMP")
    _insert_baseline_row(config, "QQQ", date(2024, 12, 2), "yfinance")

    result = generate_trade_ledger_qa(config, LIFECYCLE_RUN_ID).to_dict()

    baseline = result["baseline_qa"]
    assert baseline["baseline_symbols_expected"] == ["QQQ", "SMH", "SPY"]
    assert baseline["baseline_symbols_present"] == ["QQQ", "SPY"]
    assert baseline["baseline_symbols_missing"] == ["SMH"]
    assert baseline["baseline_rows_count"] == 3
    assert baseline["provider_mix"] == {"FMP": 2, "yfinance": 1}
    assert baseline["coverage_start"] == "2024-12-02"
    assert baseline["coverage_end"] == "2024-12-03"
    assert baseline["per_symbol"]["SPY"] == {
        "expected_sessions": 5,
        "present_sessions": 2,
        "missing_sessions": 3,
        "coverage_start": "2024-12-02",
        "coverage_end": "2024-12-03",
        "provider_mix": {"FMP": 2},
    }
    assert baseline["per_symbol"]["QQQ"]["missing_sessions"] == 4
    assert baseline["per_symbol"]["SMH"]["missing_sessions"] == 5
    assert result["warnings"]["missing_baseline_coverage_warning"] is True


def test_phase5b2_provenance_and_warnings_are_persisted(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_lifecycle_context(
        config,
        lifecycle_config_hash="lifecycle_hash",
        execution_config_hash="execution_hash",
        lifecycle_snapshot="lifecycle_snapshot",
        execution_snapshot="execution_snapshot",
    )
    _insert_position(config)
    _insert_skip(config, "MISSING_ENTRY_SESSION_PRICE")

    result = generate_trade_ledger_qa(config, LIFECYCLE_RUN_ID).to_dict()

    assert result["provenance"] == {
        "lifecycle_run_id": LIFECYCLE_RUN_ID,
        "execution_run_id": EXECUTION_RUN_ID,
        "source_signal_snapshot_id": "signal_snapshot",
        "source_signal_config_hash": "signal_hash",
        "source_signal_git_commit": "signal_commit",
        "source_universe_version": "signal_universe",
        "source_theme_version": "signal_theme",
        "mixed_source_signal_metadata": False,
        "execution_config_hash": "execution_hash",
        "execution_git_commit": "execution_commit",
        "execution_data_snapshot_id": "execution_snapshot",
        "lifecycle_config_hash": "lifecycle_hash",
        "lifecycle_git_commit": "lifecycle_commit",
        "lifecycle_data_snapshot_id": "lifecycle_snapshot",
        "price_snapshot_mode": "in_memory_provider_priority",
    }
    assert result["warnings"]["config_mismatch_warning"] is True
    assert result["warnings"]["snapshot_mismatch_warning"] is True
    assert result["warnings"]["missing_entry_session_price_warning"] is True
    with connect_database(config.database.path) as connection:
        row = connection.execute(
            """
            SELECT config_mismatch_warning, snapshot_mismatch_warning,
                   missing_entry_session_price_warning, price_snapshot_mode
            FROM lifecycle_qa
            """
        ).fetchone()
    assert row == (True, True, True, "in_memory_provider_priority")


def test_phase5b2_cli_output_has_no_formal_metric_terms(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_lifecycle_context(config)
    _insert_position(config)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"database:\n  path: {config.database.path}\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "trade-ledger-qa",
            "--lifecycle-run-id",
            LIFECYCLE_RUN_ID,
            "--no-persist",
            "--config",
            str(config_path),
        ],
    )
    output = result.stdout.lower()
    payload = json.loads(result.stdout)

    assert result.exit_code == 0
    assert "trade_ledger_rows" in payload
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


def test_phase5b2_ledger_path_reads_only_lifecycle_tables() -> None:
    source = inspect.getsource(ledger_module)

    assert "FROM signals" not in source
    assert "execution_decisions" not in source
    assert "run_scoring" not in source
    assert "detect_setups" not in source
