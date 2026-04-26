from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from sectorscout.config import SectorScoutConfig, config_hash
from sectorscout.metadata import RunMetadata, get_git_commit

SCHEMA_VERSION = 10
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect_database(path: str | Path) -> duckdb.DuckDBPyConnection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(db_path))
    connection.execute("SET TimeZone = 'UTC'")
    return connection


def initialize_database(config: SectorScoutConfig, *, cwd: str | Path | None = None) -> Path:
    db_path = Path(config.database.path)
    with connect_database(db_path) as connection:
        connection.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
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
