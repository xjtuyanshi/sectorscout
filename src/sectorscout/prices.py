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


@dataclass(frozen=True)
class PriceSnapshotUsage:
    price_snapshot_id: str
    asof_date: str
    provider_priority: list[str]
    provider_mix: dict[str, int]
    duplicate_provider_rows_dropped: int
    min_price_date: str | None
    max_price_date: str | None
    raw_row_count: int
    chosen_row_count: int
    required_symbols: list[str]
    missing_symbols: list[str]
    through_date: str
    price_snapshot_mode: str = "persisted_price_snapshot"

    def to_dict(self) -> dict:
        return asdict(self)


class PriceSnapshotValidationError(ValueError):
    """Raised when a requested persisted price snapshot cannot support a run."""


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


def _date_value(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "date"):
        return value.date()
    return date.fromisoformat(str(value))


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


def _json_dict(value: object) -> dict[str, int]:
    if not value:
        return {}
    payload = json.loads(str(value))
    return {str(key): int(payload[key]) for key in sorted(payload)}


def _json_list(value: object) -> list[str]:
    if not value:
        return []
    return [str(item) for item in json.loads(str(value))]


def validate_price_snapshot_usage(
    config: SectorScoutConfig,
    price_snapshot_id: str,
    *,
    required_symbols: Iterable[str],
    through_date: date,
    require_rows: bool = True,
) -> PriceSnapshotUsage:
    if price_snapshot_id == "":
        raise PriceSnapshotValidationError("EMPTY_PRICE_SNAPSHOT_ID")

    required = sorted({symbol.upper() for symbol in required_symbols})
    with connect_database(config.database.path) as connection:
        run = connection.execute(
            """
            SELECT
                asof_date,
                provider_priority_json,
                provider_mix_json,
                duplicate_provider_rows_dropped,
                min_price_date,
                max_price_date,
                raw_row_count,
                chosen_row_count
            FROM price_snapshot_runs
            WHERE price_snapshot_id = ?
            """,
            [price_snapshot_id],
        ).fetchone()
        if run is None:
            raise PriceSnapshotValidationError(f"UNKNOWN_PRICE_SNAPSHOT_ID: {price_snapshot_id}")

        if required:
            present_rows = connection.execute(
                """
                SELECT symbol, MAX(price_date) AS symbol_max_price_date
                FROM price_snapshot_rows
                WHERE price_snapshot_id = ?
                  AND symbol IN (SELECT unnest(?))
                GROUP BY symbol
                """,
                [price_snapshot_id, required],
            ).fetchall()
        else:
            present_rows = []

    (
        asof_date,
        priority_json,
        mix_json,
        duplicate_provider_rows_dropped,
        min_price_date,
        max_price_date,
        raw_row_count,
        chosen_row_count,
    ) = run
    if require_rows and int(chosen_row_count) == 0:
        raise PriceSnapshotValidationError(f"EMPTY_PRICE_SNAPSHOT: {price_snapshot_id}")
    if require_rows and max_price_date is None:
        raise PriceSnapshotValidationError(f"PRICE_SNAPSHOT_HAS_NO_MAX_DATE: {price_snapshot_id}")
    if max_price_date is not None and _date_value(max_price_date) < through_date:
        raise PriceSnapshotValidationError(
            f"PRICE_SNAPSHOT_UNDERCOVERED: {price_snapshot_id} max_price_date={_date_value(max_price_date).isoformat()} through_date={through_date.isoformat()}"
        )

    present_symbols = {
        str(symbol).upper()
        for symbol, symbol_max_price_date in present_rows
        if symbol_max_price_date is not None and _date_value(symbol_max_price_date) >= through_date
    }
    missing_symbols = sorted(set(required) - present_symbols)
    if require_rows and missing_symbols:
        raise PriceSnapshotValidationError(
            f"PRICE_SNAPSHOT_MISSING_SYMBOLS: {price_snapshot_id} missing={','.join(missing_symbols)}"
        )

    return PriceSnapshotUsage(
        price_snapshot_id=price_snapshot_id,
        asof_date=_date_value(asof_date).isoformat(),
        provider_priority=_json_list(priority_json),
        provider_mix=_json_dict(mix_json),
        duplicate_provider_rows_dropped=int(duplicate_provider_rows_dropped),
        min_price_date=_date_iso(min_price_date),
        max_price_date=_date_iso(max_price_date),
        raw_row_count=int(raw_row_count),
        chosen_row_count=int(chosen_row_count),
        required_symbols=required,
        missing_symbols=missing_symbols,
        through_date=through_date.isoformat(),
    )


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
