from pathlib import Path

import duckdb

from sectorscout.config import SectorScoutConfig, config_hash
from sectorscout.db import initialize_database, persist_run_metadata
from sectorscout.metadata import build_run_metadata


def test_initialize_database_is_idempotent_and_persists_schema_metadata(tmp_path: Path) -> None:
    config = SectorScoutConfig.model_validate(
        {"database": {"path": tmp_path / "test.duckdb"}}
    )
    initialize_database(config)
    initialize_database(config)
    with duckdb.connect(str(config.database.path)) as connection:
        rows = connection.execute("SELECT config_hash FROM schema_metadata").fetchall()
    assert rows == [(config_hash(config),)]


def test_persist_run_metadata(tmp_path: Path) -> None:
    config = SectorScoutConfig.model_validate(
        {"database": {"path": tmp_path / "test.duckdb"}}
    )
    initialize_database(config)
    metadata = build_run_metadata(config, command="metadata", asof_date="2024-11-29")
    persist_run_metadata(config, metadata)
    with duckdb.connect(str(config.database.path)) as connection:
        row = connection.execute(
            """
            SELECT command, asof_date, config_hash, data_snapshot_id
            FROM run_metadata
            """
        ).fetchone()
    assert row[0] == "metadata"
    assert str(row[1]) == "2024-11-29"
    assert row[2] == config_hash(config)
    assert row[3] == "phase0-fixtures"


def test_initialize_database_adds_missing_phase5b3_columns(tmp_path: Path) -> None:
    config = SectorScoutConfig.model_validate(
        {"database": {"path": tmp_path / "legacy.duckdb"}}
    )
    with duckdb.connect(str(config.database.path)) as connection:
        connection.execute(
            """
            CREATE TABLE execution_runs (
                execution_run_id VARCHAR PRIMARY KEY,
                asof_date DATE NOT NULL,
                execution_model VARCHAR NOT NULL,
                execution_generated_at_utc TIMESTAMPTZ NOT NULL,
                execution_config_hash VARCHAR NOT NULL,
                execution_git_commit VARCHAR NOT NULL,
                execution_data_snapshot_id VARCHAR NOT NULL,
                source_signal_snapshot_id VARCHAR,
                created_at_utc TIMESTAMPTZ NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE lifecycle_runs (
                lifecycle_run_id VARCHAR PRIMARY KEY,
                execution_run_id VARCHAR NOT NULL,
                through_date DATE NOT NULL,
                lifecycle_generated_at_utc TIMESTAMPTZ NOT NULL,
                lifecycle_config_hash VARCHAR NOT NULL,
                lifecycle_git_commit VARCHAR NOT NULL,
                lifecycle_data_snapshot_id VARCHAR NOT NULL,
                created_at_utc TIMESTAMPTZ NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE trade_ledger (
                lifecycle_run_id VARCHAR NOT NULL,
                execution_run_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                theme_id VARCHAR NOT NULL,
                setup_type VARCHAR NOT NULL,
                entry_date DATE NOT NULL,
                entry_price DOUBLE NOT NULL,
                initial_stop_loss DOUBLE NOT NULL,
                risk_per_share DOUBLE NOT NULL,
                status VARCHAR NOT NULL,
                qa_status VARCHAR NOT NULL,
                lifecycle_generated_at_utc TIMESTAMPTZ NOT NULL,
                lifecycle_config_hash VARCHAR NOT NULL,
                lifecycle_git_commit VARCHAR NOT NULL,
                lifecycle_data_snapshot_id VARCHAR NOT NULL
            )
            """
        )

    initialize_database(config)

    with duckdb.connect(str(config.database.path)) as connection:
        execution_run_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('execution_runs')").fetchall()
        }
        lifecycle_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('lifecycle_runs')").fetchall()
        }
        ledger_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('trade_ledger')").fetchall()
        }
        lifecycle_qa_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('lifecycle_qa')").fetchall()
        }
        price_snapshot_run_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info('price_snapshot_runs')").fetchall()
        }
    assert {
        "source_signal_config_hash",
        "source_signal_git_commit",
        "source_universe_version",
        "source_theme_version",
        "mixed_source_signal_metadata",
        "price_snapshot_id",
    }.issubset(execution_run_columns)
    assert "price_snapshot_id" in lifecycle_columns
    assert {
        "asof_date",
        "execution_model",
        "calendar_holding_days",
        "trading_holding_sessions",
        "source_signal_snapshot_id",
        "execution_config_hash",
        "execution_data_snapshot_id",
    }.issubset(ledger_columns)
    assert "price_snapshot_mismatch_warning" in lifecycle_qa_columns
    assert "snapshot_rows_hash" in price_snapshot_run_columns
