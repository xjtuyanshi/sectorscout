from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Iterable
from uuid import uuid4

import pandas as pd

from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database
from sectorscout.metadata import build_run_metadata

PRICE_SNAPSHOT_COLUMNS = [
    "symbol",
    "price_date",
    "adj_open",
    "adj_high",
    "adj_low",
    "adj_close",
    "adj_volume",
    "provider",
    "adjustment_warning",
]


@dataclass(frozen=True)
class FrozenPriceSnapshotResult:
    price_snapshot_id: str
    asof_date: str
    provider_priority: list[str]
    provider_mix: dict[str, int]
    duplicate_provider_rows_dropped: int
    min_price_date: str | None
    max_price_date: str | None
    raw_row_count: int
    chosen_row_count: int
    config_hash: str
    git_commit: str
    warning: str

    def to_dict(self) -> dict:
        return asdict(self)


def provider_priority(config: SectorScoutConfig) -> list[str]:
    providers = [
        config.providers.prices_primary,
        config.providers.prices_fallback,
        "fixture",
    ]
    result: list[str] = []
    for provider in providers:
        if provider and provider not in result:
            result.append(provider)
    return result


def choose_provider_rows(
    rows: pd.DataFrame,
    config: SectorScoutConfig,
) -> tuple[pd.DataFrame, int]:
    """Choose one provider row per symbol/date using configured provider priority."""
    if rows.empty:
        return rows.copy(), 0
    priority = {provider: rank for rank, provider in enumerate(provider_priority(config))}
    ranked = rows.copy()
    ranked["_provider_rank"] = ranked["provider"].map(priority).fillna(len(priority)).astype(int)
    ranked = ranked.sort_values(["symbol", "price_date", "_provider_rank", "provider"])
    duplicate_count = int(ranked.duplicated(["symbol", "price_date"]).sum())
    chosen = ranked.drop_duplicates(["symbol", "price_date"], keep="first")
    return chosen.drop(columns=["_provider_rank"]).reset_index(drop=True), duplicate_count


def load_price_snapshot(
    config: SectorScoutConfig,
    asof_date: date,
    *,
    symbols: Iterable[str] | None = None,
) -> tuple[pd.DataFrame, int]:
    symbol_list = sorted({symbol.upper() for symbol in symbols}) if symbols is not None else None
    if symbols is not None and not symbol_list:
        return pd.DataFrame(columns=PRICE_SNAPSHOT_COLUMNS), 0
    symbol_filter = "AND symbol IN (SELECT unnest(?))" if symbol_list else ""
    params: list[object] = [asof_date]
    if symbol_list:
        params.append(symbol_list)
    with connect_database(config.database.path) as connection:
        rows = connection.execute(
            f"""
            SELECT
                symbol,
                price_date,
                adj_open,
                adj_high,
                adj_low,
                adj_close,
                adj_volume,
                provider,
                adjustment_warning
            FROM daily_prices
            WHERE price_date <= ?
              {symbol_filter}
            ORDER BY symbol, price_date, provider
            """,
            params,
        ).fetchdf()
    return choose_provider_rows(rows, config)


def load_persisted_price_snapshot(
    config: SectorScoutConfig,
    price_snapshot_id: str,
    *,
    symbols: Iterable[str] | None = None,
    through_date: date | None = None,
) -> pd.DataFrame:
    symbol_list = sorted({symbol.upper() for symbol in symbols}) if symbols is not None else None
    if symbols is not None and not symbol_list:
        return pd.DataFrame(columns=PRICE_SNAPSHOT_COLUMNS)
    symbol_filter = "AND symbol IN (SELECT unnest(?))" if symbol_list else ""
    date_filter = "AND price_date <= ?" if through_date else ""
    params: list[object] = [price_snapshot_id]
    if symbol_list:
        params.append(symbol_list)
    if through_date:
        params.append(through_date)
    with connect_database(config.database.path) as connection:
        return connection.execute(
            f"""
            SELECT
                symbol,
                price_date,
                adj_open,
                adj_high,
                adj_low,
                adj_close,
                adj_volume,
                provider,
                adjustment_warning
            FROM price_snapshot_rows
            WHERE price_snapshot_id = ?
              {symbol_filter}
              {date_filter}
            ORDER BY symbol, price_date, provider
            """,
            params,
        ).fetchdf()


def _provider_mix(rows: pd.DataFrame) -> dict[str, int]:
    if rows.empty:
        return {}
    counts = rows["provider"].value_counts().to_dict()
    return {str(provider): int(count) for provider, count in sorted(counts.items())}


def _date_iso(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "date"):
        return value.date().isoformat()
    return date.fromisoformat(str(value)).isoformat()


def create_frozen_price_snapshot(
    config: SectorScoutConfig,
    asof_date: date,
    *,
    symbols: Iterable[str] | None = None,
    persist: bool = True,
) -> FrozenPriceSnapshotResult:
    symbol_list = sorted({symbol.upper() for symbol in symbols}) if symbols is not None else None
    if symbols is not None and not symbol_list:
        raw_rows = pd.DataFrame(columns=PRICE_SNAPSHOT_COLUMNS)
        duplicate_count = 0
        chosen = raw_rows.copy()
    else:
        symbol_filter = "AND symbol IN (SELECT unnest(?))" if symbol_list else ""
        params: list[object] = [asof_date]
        if symbol_list:
            params.append(symbol_list)
        with connect_database(config.database.path) as connection:
            raw_rows = connection.execute(
                f"""
                SELECT
                    symbol,
                    price_date,
                    adj_open,
                    adj_high,
                    adj_low,
                    adj_close,
                    adj_volume,
                    provider,
                    adjustment_warning
                FROM daily_prices
                WHERE price_date <= ?
                  {symbol_filter}
                ORDER BY symbol, price_date, provider
                """,
                params,
            ).fetchdf()
        chosen, duplicate_count = choose_provider_rows(raw_rows, config)

    metadata = build_run_metadata(config, "price-snapshot", asof_date=asof_date)
    price_snapshot_id = str(uuid4())
    provider_mix = _provider_mix(chosen)
    min_price_date = None if chosen.empty else chosen["price_date"].min()
    max_price_date = None if chosen.empty else chosen["price_date"].max()

    if persist:
        with connect_database(config.database.path) as connection:
            connection.execute(
                """
                INSERT INTO price_snapshot_runs (
                    price_snapshot_id, asof_date, provider_priority_json,
                    provider_mix_json, duplicate_provider_rows_dropped,
                    min_price_date, max_price_date, raw_row_count,
                    chosen_row_count, config_hash, git_commit, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    price_snapshot_id,
                    asof_date,
                    json.dumps(provider_priority(config), sort_keys=True),
                    json.dumps(provider_mix, sort_keys=True),
                    duplicate_count,
                    min_price_date,
                    max_price_date,
                    int(len(raw_rows)),
                    int(len(chosen)),
                    metadata.config_hash,
                    metadata.git_commit,
                    metadata.created_at,
                ],
            )
            for _, row in chosen.iterrows():
                connection.execute(
                    """
                    INSERT INTO price_snapshot_rows (
                        price_snapshot_id, symbol, price_date, adj_open,
                        adj_high, adj_low, adj_close, adj_volume, provider,
                        adjustment_warning
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        price_snapshot_id,
                        row["symbol"],
                        row["price_date"],
                        float(row["adj_open"]),
                        float(row["adj_high"]),
                        float(row["adj_low"]),
                        float(row["adj_close"]),
                        int(row["adj_volume"]),
                        row["provider"],
                        bool(row["adjustment_warning"]),
                    ],
                )

    return FrozenPriceSnapshotResult(
        price_snapshot_id=price_snapshot_id,
        asof_date=asof_date.isoformat(),
        provider_priority=provider_priority(config),
        provider_mix=provider_mix,
        duplicate_provider_rows_dropped=duplicate_count,
        min_price_date=_date_iso(min_price_date),
        max_price_date=_date_iso(max_price_date),
        raw_row_count=int(len(raw_rows)),
        chosen_row_count=int(len(chosen)),
        config_hash=metadata.config_hash,
        git_commit=metadata.git_commit,
        warning=(
            "Phase 5B3 only: frozen price snapshot tables are reproducibility "
            "scaffolding and are not a performance report."
        ),
    )
