from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone

from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database
from sectorscout.market_calendar import asof_market_close
from sectorscout.pit import BENCHMARK_SYMBOLS, tradable_universe_asof
from sectorscout.prices import load_price_snapshot


@dataclass(frozen=True)
class DataQualityReport:
    asof_date: str
    total_symbols: int
    symbols_with_price_data: int
    symbols_missing_price_data: int
    symbols_with_fundamental_data: int
    stale_price_count: int
    stale_fundamental_count: int
    split_adjustment_warnings: int
    provider_rate_limit_events: int
    fallback_to_yfinance_count: int
    provider_mix: dict[str, int]
    universe_mode: str = "live"
    duplicate_provider_rows_dropped: int = 0
    price_snapshot_provider_mix: dict[str, int] = field(default_factory=dict)
    benchmark_symbols_with_price_data: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=2)


def compute_data_quality(config: SectorScoutConfig, asof_date: date) -> DataQualityReport:
    asof_close = asof_market_close(asof_date, config)
    chosen_prices, duplicate_provider_rows_dropped = load_price_snapshot(config, asof_date)
    with connect_database(config.database.path) as connection:
        total_symbols = connection.execute(
            "SELECT COUNT(*) FROM symbols WHERE is_active = true"
        ).fetchone()[0]
        symbols_with_price_data = connection.execute(
            """
            SELECT COUNT(DISTINCT symbol)
            FROM daily_prices
            WHERE price_date <= ?
            """,
            [asof_date],
        ).fetchone()[0]
        symbols_missing_price_data = max(total_symbols - symbols_with_price_data, 0)
        stale_price_count = connection.execute(
            """
            WITH latest AS (
                SELECT symbol, MAX(price_date) AS latest_price_date
                FROM daily_prices
                WHERE price_date <= ?
                GROUP BY symbol
            )
            SELECT COUNT(*)
            FROM latest
            WHERE date_diff('day', latest_price_date, ?) > ?
            """,
            [asof_date, asof_date, config.data_quality.stale_price_days],
        ).fetchone()[0]
        split_adjustment_warnings = connection.execute(
            """
            SELECT COUNT(*)
            FROM daily_prices
            WHERE price_date <= ? AND adjustment_warning = true
            """,
            [asof_date],
        ).fetchone()[0]
        provider_rows = connection.execute(
            """
            SELECT provider, COUNT(*) AS row_count
            FROM daily_prices
            WHERE price_date <= ?
            GROUP BY provider
            ORDER BY provider
            """,
            [asof_date],
        ).fetchall()
        provider_mix = {provider: int(row_count) for provider, row_count in provider_rows}
        fallback_to_yfinance_count = int(provider_mix.get(config.providers.prices_fallback, 0))
        symbols_with_fundamental_data = connection.execute(
            """
            SELECT COUNT(DISTINCT symbol)
            FROM fundamental_facts
            WHERE available_at IS NOT NULL
              AND available_at <= ?
            """,
            [asof_close],
        ).fetchone()[0]

    price_snapshot_provider_mix = (
        chosen_prices.groupby("provider").size().astype(int).to_dict()
        if not chosen_prices.empty
        else {}
    )
    benchmark_symbols_with_price_data = int(
        chosen_prices[chosen_prices["symbol"].isin(BENCHMARK_SYMBOLS)]["symbol"].nunique()
    )
    return DataQualityReport(
        asof_date=asof_date.isoformat(),
        total_symbols=total_symbols,
        symbols_with_price_data=symbols_with_price_data,
        symbols_missing_price_data=symbols_missing_price_data,
        symbols_with_fundamental_data=symbols_with_fundamental_data,
        stale_price_count=stale_price_count,
        stale_fundamental_count=0,
        split_adjustment_warnings=split_adjustment_warnings,
        provider_rate_limit_events=0,
        fallback_to_yfinance_count=fallback_to_yfinance_count,
        provider_mix=provider_mix,
        duplicate_provider_rows_dropped=duplicate_provider_rows_dropped,
        price_snapshot_provider_mix=price_snapshot_provider_mix,
        benchmark_symbols_with_price_data=benchmark_symbols_with_price_data,
    )


def compute_historical_data_quality(
    config: SectorScoutConfig,
    asof_date: date,
) -> DataQualityReport:
    asof_close = asof_market_close(asof_date, config)
    eligible_symbols = set(tradable_universe_asof(config, asof_date, mode="historical"))
    snapshot_symbols = eligible_symbols | BENCHMARK_SYMBOLS
    chosen_prices, duplicate_provider_rows_dropped = load_price_snapshot(
        config,
        asof_date,
        symbols=snapshot_symbols,
    )
    eligible_prices = chosen_prices[chosen_prices["symbol"].isin(eligible_symbols)]
    price_snapshot_provider_mix = (
        chosen_prices.groupby("provider").size().astype(int).to_dict()
        if not chosen_prices.empty
        else {}
    )
    fallback_to_yfinance_count = int(
        price_snapshot_provider_mix.get(config.providers.prices_fallback, 0)
    )
    with connect_database(config.database.path) as connection:
        split_adjustment_warnings = connection.execute(
            """
            SELECT COUNT(*)
            FROM daily_prices
            WHERE price_date <= ?
              AND adjustment_warning = true
              AND symbol IN (SELECT unnest(?))
            """,
            [asof_date, sorted(snapshot_symbols)],
        ).fetchone()[0] if snapshot_symbols else 0
        symbols_with_fundamental_data = connection.execute(
            """
            SELECT COUNT(DISTINCT symbol)
            FROM fundamental_facts
            WHERE available_at IS NOT NULL
              AND available_at <= ?
              AND symbol IN (SELECT unnest(?))
            """,
            [asof_close, sorted(eligible_symbols)],
        ).fetchone()[0] if eligible_symbols else 0

    latest_by_symbol = (
        eligible_prices.groupby("symbol")["price_date"].max().to_dict()
        if not eligible_prices.empty
        else {}
    )
    stale_price_count = sum(
        (asof_date - price_date.date()).days > config.data_quality.stale_price_days
        if hasattr(price_date, "date")
        else (asof_date - price_date).days > config.data_quality.stale_price_days
        for price_date in latest_by_symbol.values()
    )
    return DataQualityReport(
        asof_date=asof_date.isoformat(),
        total_symbols=len(eligible_symbols),
        symbols_with_price_data=int(eligible_prices["symbol"].nunique()),
        symbols_missing_price_data=max(len(eligible_symbols) - int(eligible_prices["symbol"].nunique()), 0),
        symbols_with_fundamental_data=symbols_with_fundamental_data,
        stale_price_count=stale_price_count,
        stale_fundamental_count=0,
        split_adjustment_warnings=split_adjustment_warnings,
        provider_rate_limit_events=0,
        fallback_to_yfinance_count=fallback_to_yfinance_count,
        provider_mix=price_snapshot_provider_mix,
        universe_mode="historical",
        duplicate_provider_rows_dropped=duplicate_provider_rows_dropped,
        price_snapshot_provider_mix=price_snapshot_provider_mix,
        benchmark_symbols_with_price_data=int(
            chosen_prices[chosen_prices["symbol"].isin(BENCHMARK_SYMBOLS)]["symbol"].nunique()
        ),
    )


def persist_data_quality(config: SectorScoutConfig, report: DataQualityReport) -> None:
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO data_quality_daily (
                asof_date,
                total_symbols,
                symbols_with_price_data,
                symbols_missing_price_data,
                symbols_with_fundamental_data,
                stale_price_count,
                stale_fundamental_count,
                split_adjustment_warnings,
                provider_rate_limit_events,
                fallback_to_yfinance_count,
                provider_mix_json,
                created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                date.fromisoformat(report.asof_date),
                report.total_symbols,
                report.symbols_with_price_data,
                report.symbols_missing_price_data,
                report.symbols_with_fundamental_data,
                report.stale_price_count,
                report.stale_fundamental_count,
                report.split_adjustment_warnings,
                report.provider_rate_limit_events,
                report.fallback_to_yfinance_count,
                json.dumps(report.provider_mix, sort_keys=True),
                datetime.now(timezone.utc),
            ],
        )
