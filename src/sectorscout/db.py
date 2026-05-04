from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from sectorscout.config import SectorScoutConfig, config_hash
from sectorscout.metadata import RunMetadata, get_git_commit

SCHEMA_VERSION = 19
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


MIGRATION_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("data_quality_daily", "universe_mode", "VARCHAR DEFAULT 'live'"),
    ("data_quality_daily", "duplicate_provider_rows_dropped", "INTEGER DEFAULT 0"),
    ("data_quality_daily", "price_snapshot_provider_mix_json", "VARCHAR DEFAULT '{}'"),
    ("data_quality_daily", "benchmark_symbols_with_price_data", "INTEGER DEFAULT 0"),
    ("execution_runs", "source_signal_config_hash", "VARCHAR"),
    ("execution_runs", "source_signal_git_commit", "VARCHAR"),
    ("execution_runs", "source_universe_version", "VARCHAR"),
    ("execution_runs", "source_theme_version", "VARCHAR"),
    ("execution_runs", "mixed_source_signal_metadata", "BOOLEAN DEFAULT false"),
    ("execution_runs", "price_snapshot_id", "VARCHAR"),
    ("execution_decisions", "price_snapshot_id", "VARCHAR"),
    ("lifecycle_runs", "price_snapshot_id", "VARCHAR"),
    ("simulated_positions", "asof_date", "DATE"),
    ("simulated_positions", "execution_model", "VARCHAR DEFAULT 'next_open'"),
    ("exit_decisions", "asof_date", "DATE"),
    ("exit_decisions", "execution_model", "VARCHAR DEFAULT 'next_open'"),
    ("trade_ledger", "asof_date", "DATE"),
    ("trade_ledger", "execution_model", "VARCHAR DEFAULT 'next_open'"),
    ("trade_ledger", "calendar_holding_days", "INTEGER"),
    ("trade_ledger", "trading_holding_sessions", "INTEGER"),
    ("trade_ledger", "source_signal_snapshot_id", "VARCHAR"),
    ("trade_ledger", "execution_config_hash", "VARCHAR"),
    ("trade_ledger", "execution_data_snapshot_id", "VARCHAR"),
    ("lifecycle_qa", "price_snapshot_mismatch_warning", "BOOLEAN DEFAULT false"),
    ("lifecycle_qa", "non_price_input_snapshot_warning", "BOOLEAN DEFAULT false"),
    ("lifecycle_qa", "market_regime_source_mismatch_warning", "BOOLEAN DEFAULT false"),
    ("lifecycle_qa", "theme_score_source_mismatch_warning", "BOOLEAN DEFAULT false"),
    ("lifecycle_qa", "missing_market_regime_coverage_warning", "BOOLEAN DEFAULT false"),
    ("lifecycle_qa", "missing_theme_score_coverage_warning", "BOOLEAN DEFAULT false"),
    ("lifecycle_qa", "missing_lifecycle_input_qa_warning", "BOOLEAN DEFAULT false"),
    ("lifecycle_qa", "mixed_source_signal_metadata_warning", "BOOLEAN DEFAULT false"),
    (
        "lifecycle_qa",
        "non_price_input_mode",
        "VARCHAR DEFAULT 'live_table_version_guardrail'",
    ),
    ("lifecycle_qa", "non_price_input_qa_json", "VARCHAR DEFAULT '{}'"),
    ("lifecycle_input_qa", "expected_market_regime_sessions_json", "VARCHAR DEFAULT '[]'"),
    ("lifecycle_input_qa", "missing_market_regime_sessions_json", "VARCHAR DEFAULT '[]'"),
    ("lifecycle_input_qa", "expected_theme_score_keys_json", "VARCHAR DEFAULT '[]'"),
    ("lifecycle_input_qa", "missing_theme_score_keys_json", "VARCHAR DEFAULT '[]'"),
    (
        "lifecycle_input_qa",
        "missing_market_regime_coverage_warning",
        "BOOLEAN DEFAULT false",
    ),
    (
        "lifecycle_input_qa",
        "missing_theme_score_coverage_warning",
        "BOOLEAN DEFAULT false",
    ),
    (
        "lifecycle_input_qa",
        "mixed_source_signal_metadata_warning",
        "BOOLEAN DEFAULT false",
    ),
    ("price_snapshot_runs", "snapshot_rows_hash", "VARCHAR"),
)


class DatabaseConnection:
    def __init__(self, path: str | Path) -> None:
        db_path = Path(path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = duckdb.connect(str(db_path))
        self._connection.execute("SET TimeZone = 'UTC'")

    def __enter__(self) -> duckdb.DuckDBPyConnection:
        return self._connection

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._connection.close()

    def __getattr__(self, name: str) -> object:
        return getattr(self._connection, name)

    def close(self) -> None:
        self._connection.close()


def connect_database(path: str | Path) -> DatabaseConnection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return DatabaseConnection(db_path)


def _table_columns(connection: duckdb.DuckDBPyConnection, table_name: str) -> set[str]:
    try:
        rows = connection.execute(f"PRAGMA table_info('{table_name}')").fetchall()
    except duckdb.CatalogException:
        return set()
    return {str(row[1]) for row in rows}


def apply_schema_migrations(connection: duckdb.DuckDBPyConnection) -> None:
    for table_name, column_name, column_type in MIGRATION_COLUMNS:
        columns = _table_columns(connection, table_name)
        if not columns or column_name in columns:
            continue
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


def initialize_database(config: SectorScoutConfig, *, cwd: str | Path | None = None) -> Path:
    db_path = Path(config.database.path)
    with connect_database(db_path) as connection:
        connection.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        apply_schema_migrations(connection)
        connection.execute(
            "DELETE FROM schema_metadata WHERE schema_version = ?",
            [SCHEMA_VERSION],
        )
        connection.execute(
            """
            INSERT INTO schema_metadata (
                schema_version,
                initialized_at_utc,
                config_hash,
                git_commit
            ) VALUES (?, ?, ?, ?)
            """,
            [
                SCHEMA_VERSION,
                datetime.now(timezone.utc),
                config_hash(config),
                get_git_commit(cwd),
            ],
        )
    return db_path


def persist_run_metadata(config: SectorScoutConfig, metadata: RunMetadata) -> None:
    with connect_database(config.database.path) as connection:
        connection.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        connection.execute(
            """
            INSERT INTO run_metadata (
                command,
                asof_date,
                signal_generated_at_utc,
                config_hash,
                git_commit,
                data_snapshot_id,
                provider_versions_json,
                universe_version,
                theme_version,
                created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                metadata.command,
                metadata.asof_date,
                metadata.signal_generated_at,
                metadata.config_hash,
                metadata.git_commit,
                metadata.data_snapshot_id,
                json.dumps(metadata.provider_versions, sort_keys=True),
                metadata.universe_version,
                metadata.theme_version,
                metadata.created_at,
            ],
        )
