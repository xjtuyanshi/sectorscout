from __future__ import annotations

from datetime import date
from typing import Iterable

import pandas as pd

from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database

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
