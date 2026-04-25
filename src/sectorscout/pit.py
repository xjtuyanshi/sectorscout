from __future__ import annotations

from datetime import date, datetime

from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database
from sectorscout.market_calendar import asof_market_close


def available_fundamental_facts(
    config: SectorScoutConfig,
    asof_date: date,
) -> list[dict]:
    """Return only facts whose availability is known and not after market close."""
    asof_close = asof_market_close(asof_date, config)
    with connect_database(config.database.path) as connection:
        rows = connection.execute(
            """
            SELECT
                symbol, fiscal_period, fiscal_year, fiscal_quarter, form_type,
                metric_name, metric_value, period_end_date, filing_date,
                accepted_at, earnings_release_datetime, available_at, source
            FROM fundamental_facts
            WHERE available_at IS NOT NULL
              AND available_at <= ?
            ORDER BY symbol, fiscal_period, metric_name
            """,
            [asof_close],
        ).fetchall()
    columns = [
        "symbol",
        "fiscal_period",
        "fiscal_year",
        "fiscal_quarter",
        "form_type",
        "metric_name",
        "metric_value",
        "period_end_date",
        "filing_date",
        "accepted_at",
        "earnings_release_datetime",
        "available_at",
        "source",
    ]
    return [dict(zip(columns, row, strict=True)) for row in rows]


def universe_asof(config: SectorScoutConfig, asof_date: date) -> list[str]:
    """Return lifecycle-aware active symbols for an as-of date."""
    with connect_database(config.database.path) as connection:
        rows = connection.execute(
            """
            SELECT symbol
            FROM symbols
            WHERE (ipo_date IS NULL OR ipo_date <= ?)
              AND (delist_date IS NULL OR delist_date > ?)
              AND first_seen_at <= ?
              AND last_seen_at >= ?
              AND is_active = true
            ORDER BY symbol
            """,
            [asof_date, asof_date, asof_date, asof_date],
        ).fetchall()
    return [row[0] for row in rows]


def theme_members_asof(
    config: SectorScoutConfig,
    asof_date: date,
    *,
    allow_historical_ex_post: bool = False,
) -> list[dict]:
    with connect_database(config.database.path) as connection:
        rows = connection.execute(
            """
            SELECT
                tm.theme_id, t.name, t.theme_type, t.discovery_date,
                tm.symbol, tm.valid_from, tm.valid_to, tm.source,
                tm.confidence, tm.evidence
            FROM theme_members tm
            JOIN themes t ON t.theme_id = tm.theme_id
            WHERE tm.valid_from <= ?
              AND (tm.valid_to IS NULL OR tm.valid_to >= ?)
              AND (
                t.theme_type != 'dynamic_theme'
                OR (t.discovery_date IS NOT NULL AND t.discovery_date <= ?)
              )
              AND (
                ? = true
                OR t.theme_type != 'historical_ex_post_theme'
              )
            ORDER BY tm.theme_id, tm.symbol
            """,
            [asof_date, asof_date, asof_date, allow_historical_ex_post],
        ).fetchall()
    columns = [
        "theme_id",
        "name",
        "theme_type",
        "discovery_date",
        "symbol",
        "valid_from",
        "valid_to",
        "source",
        "confidence",
        "evidence",
    ]
    return [dict(zip(columns, row, strict=True)) for row in rows]
