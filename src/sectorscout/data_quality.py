from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone

from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database
from sectorscout.market_calendar import asof_market_close


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

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=2)


def compute_data_quality(config: SectorScoutConfig, asof_date: date) -> DataQualityReport:
    asof_close = asof_market_close(asof_date, config)
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
