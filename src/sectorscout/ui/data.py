from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from sectorscout.config import SectorScoutConfig, config_hash, load_config
from sectorscout.db import connect_database
from sectorscout.intel.storage import ensure_intel_dirs, ensure_intel_tables
from sectorscout.intel.x_collector import x_api_status
from sectorscout.metadata import get_git_commit


@dataclass(frozen=True)
class UIContext:
    config: SectorScoutConfig
    config_path: Path
    asof_date: date | None


def load_ui_context(config_path: str | Path) -> UIContext:
    config = load_config(config_path)
    ensure_intel_dirs()
    ensure_intel_tables(config)
    return UIContext(config=config, config_path=Path(config_path), asof_date=latest_asof_date(config))


def table_exists(config: SectorScoutConfig, table: str) -> bool:
    with connect_database(config.database.path) as connection:
        row = connection.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'main' AND table_name = ?
            """,
            [table],
        ).fetchone()
    return row is not None


def row_count(config: SectorScoutConfig, table: str) -> int:
    if not table_exists(config, table):
        return 0
    with connect_database(config.database.path) as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def filtered_count(config: SectorScoutConfig, table: str, where_clause: str) -> int:
    if not table_exists(config, table):
        return 0
    with connect_database(config.database.path) as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table} WHERE {where_clause}").fetchone()[0])


def safe_df(config: SectorScoutConfig, sql: str, params: list | None = None) -> pd.DataFrame:
    with connect_database(config.database.path) as connection:
        return connection.execute(sql, params or []).fetchdf()


def table_df(config: SectorScoutConfig, table: str, *, limit: int = 500) -> pd.DataFrame:
    if not table_exists(config, table):
        return pd.DataFrame()
    with connect_database(config.database.path) as connection:
        return connection.execute(f"SELECT * FROM {table} LIMIT {limit}").fetchdf()


def latest_asof_date(config: SectorScoutConfig) -> date | None:
    candidates: list[date] = []
    for table in [
        "stock_scores",
        "theme_scores",
        "signals",
        "setups",
        "execution_decisions",
        "trade_ledger",
        "market_regime",
    ]:
        if not table_exists(config, table):
            continue
        column = "asof_date"
        try:
            row = safe_df(config, f"SELECT max({column}) AS asof_date FROM {table}")
        except Exception:
            continue
        if not row.empty and pd.notna(row.iloc[0]["asof_date"]):
            value = row.iloc[0]["asof_date"]
            candidates.append(value.date() if hasattr(value, "date") else date.fromisoformat(str(value)[:10]))
    return max(candidates) if candidates else None


def latest_rows(config: SectorScoutConfig, table: str, date_column: str = "asof_date") -> pd.DataFrame:
    if not table_exists(config, table):
        return pd.DataFrame()
    try:
        return safe_df(
            config,
            f"""
            SELECT *
            FROM {table}
            WHERE {date_column} = (SELECT max({date_column}) FROM {table})
            LIMIT 500
            """,
        )
    except Exception:
        return table_df(config, table)


def parse_json_list(value: Any) -> list:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def system_status(config: SectorScoutConfig) -> dict[str, Any]:
    x_status = x_api_status()
    return {
        "duckdb_path": str(config.database.path),
        "git_commit": get_git_commit(),
        "config_hash": config_hash(config),
        "data_snapshot_id": config.reproducibility.data_snapshot_id,
        "universe_version": config.reproducibility.universe_version,
        "theme_version": config.reproducibility.theme_version,
        "openai_vision_provider": bool(os.environ.get("OPENAI_API_KEY")),
        "x_api_status": x_status["status"],
        "x_api_endpoint": x_status["endpoint"],
    }
