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

