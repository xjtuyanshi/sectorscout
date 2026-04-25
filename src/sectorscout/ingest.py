from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database


REQUIRED_PRICE_COLUMNS = {
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "adj_open",
    "adj_high",
    "adj_low",
    "adj_close",
    "adj_volume",
}


class IngestionError(ValueError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _read_csv(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y"}


def _parse_date(value: str | None) -> date | None:
    if value is None or value == "":
        return None
    return date.fromisoformat(value)


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None or value == "":
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise IngestionError(f"Timestamp must include timezone: {value}")
    return parsed.astimezone(timezone.utc)


def _parse_float(row: dict[str, str], column: str) -> float:
    value = row.get(column)
    if value is None or value == "":
        raise IngestionError(f"Missing required numeric column {column}")
    parsed = float(value)
    if parsed <= 0:
        raise IngestionError(f"{column} must be positive")
    return parsed


def _parse_int(row: dict[str, str], column: str) -> int:
    value = row.get(column)
    if value is None or value == "":
        raise IngestionError(f"Missing required integer column {column}")
    parsed = int(float(value))
    if parsed < 0:
        raise IngestionError(f"{column} must be non-negative")
    return parsed


def validate_adjusted_ohlcv_row(row: dict[str, str]) -> None:
    missing = REQUIRED_PRICE_COLUMNS - set(row)
    if missing:
        raise IngestionError(
            "Adjusted OHLCV is required; missing columns: " + ", ".join(sorted(missing))
        )
    for column in ("open", "high", "low", "close", "adj_open", "adj_high", "adj_low", "adj_close"):
        _parse_float(row, column)
    _parse_int(row, "volume")
    _parse_int(row, "adj_volume")
    if _parse_float(row, "high") < _parse_float(row, "low"):
        raise IngestionError("high cannot be below low")
    if _parse_float(row, "adj_high") < _parse_float(row, "adj_low"):
        raise IngestionError("adj_high cannot be below adj_low")


def ingest_universe_csv(
    config: SectorScoutConfig,
    path: str | Path,
    *,
    provider: str,
    source: str = "fixture",
) -> int:
    rows = _read_csv(path)
    now = _utc_now()
    with connect_database(config.database.path) as connection:
        for row in rows:
            symbol = row["symbol"].strip().upper()
            connection.execute(
                """
                INSERT OR REPLACE INTO symbols (
                    symbol, name, exchange, security_type, is_etf, is_active,
                    ipo_date, delist_date, first_seen_at, last_seen_at,
                    source, provider, ingested_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    symbol,
                    row["name"],
                    row["exchange"],
                    row["security_type"],
                    _parse_bool(row.get("is_etf", "false")),
                    _parse_bool(row.get("is_active", "true")),
                    _parse_date(row.get("ipo_date")),
                    _parse_date(row.get("delist_date")),
                    _parse_date(row.get("first_seen_at")) or now.date(),
                    _parse_date(row.get("last_seen_at")) or now.date(),
                    source,
                    provider,
                    now,
                ],
            )
    return len(rows)


def ingest_prices_csv(
    config: SectorScoutConfig,
    path: str | Path,
    *,
    provider: str,
) -> int:
    rows = _read_csv(path)
    now = _utc_now()
    with connect_database(config.database.path) as connection:
        for row in rows:
            validate_adjusted_ohlcv_row(row)
            connection.execute(
                """
                INSERT OR REPLACE INTO daily_prices (
                    symbol, price_date, open, high, low, close, volume,
                    adj_open, adj_high, adj_low, adj_close, adj_volume,
                    provider, is_adjusted, adjustment_warning, ingested_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    row["symbol"].strip().upper(),
                    date.fromisoformat(row["date"]),
                    _parse_float(row, "open"),
                    _parse_float(row, "high"),
                    _parse_float(row, "low"),
                    _parse_float(row, "close"),
                    _parse_int(row, "volume"),
                    _parse_float(row, "adj_open"),
                    _parse_float(row, "adj_high"),
                    _parse_float(row, "adj_low"),
                    _parse_float(row, "adj_close"),
                    _parse_int(row, "adj_volume"),
                    provider,
                    True,
                    _parse_bool(row.get("adjustment_warning", "false")),
                    now,
                ],
            )
    return len(rows)


def ingest_corporate_actions_csv(
    config: SectorScoutConfig,
    path: str | Path,
    *,
    source: str = "fixture",
) -> int:
    rows = _read_csv(path)
    now = _utc_now()
    with connect_database(config.database.path) as connection:
        for row in rows:
            connection.execute(
                """
                INSERT OR REPLACE INTO corporate_actions (
                    symbol, action_type, effective_date, ratio_or_amount, source, ingested_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    row["symbol"].strip().upper(),
                    row["action_type"],
                    date.fromisoformat(row["effective_date"]),
                    float(row["ratio_or_amount"]),
                    source,
                    now,
                ],
            )
    return len(rows)


def ingest_fundamental_facts_csv(
    config: SectorScoutConfig,
    path: str | Path,
    *,
    source: str = "fixture",
) -> int:
    rows = _read_csv(path)
    now = _utc_now()
    with connect_database(config.database.path) as connection:
        for row in rows:
            connection.execute(
                """
                INSERT OR REPLACE INTO fundamental_facts (
                    symbol, cik, fiscal_period, fiscal_year, fiscal_quarter,
                    form_type, metric_name, metric_value, period_end_date,
                    filing_date, accepted_at, earnings_release_datetime,
                    available_at, source, provider_updated_at, ingested_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    row["symbol"].strip().upper(),
                    row.get("cik") or None,
                    row["fiscal_period"],
                    int(row["fiscal_year"]),
                    int(row["fiscal_quarter"]) if row.get("fiscal_quarter") else None,
                    row["form_type"],
                    row["metric_name"],
                    float(row["metric_value"]),
                    date.fromisoformat(row["period_end_date"]),
                    _parse_date(row.get("filing_date")),
                    _parse_datetime(row.get("accepted_at")),
                    _parse_datetime(row.get("earnings_release_datetime")),
                    _parse_datetime(row.get("available_at")),
                    source,
                    _parse_datetime(row.get("provider_updated_at")),
                    now,
                ],
            )
    return len(rows)


def ingest_themes_csv(
    config: SectorScoutConfig,
    path: str | Path,
    *,
    source: str = "fixture",
) -> int:
    rows = _read_csv(path)
    now = _utc_now()
    with connect_database(config.database.path) as connection:
        for row in rows:
            connection.execute(
                """
                INSERT OR REPLACE INTO themes (
                    theme_id, name, theme_type, discovery_date, source,
                    confidence, evidence, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    row["theme_id"],
                    row["name"],
                    row["theme_type"],
                    _parse_date(row.get("discovery_date")),
                    source,
                    float(row["confidence"]) if row.get("confidence") else None,
                    row.get("evidence") or None,
                    now,
                ],
            )
    return len(rows)


def ingest_theme_members_csv(
    config: SectorScoutConfig,
    path: str | Path,
    *,
    source: str = "fixture",
) -> int:
    rows = _read_csv(path)
    now = _utc_now()
    with connect_database(config.database.path) as connection:
        for row in rows:
            connection.execute(
                """
                INSERT OR REPLACE INTO theme_members (
                    theme_id, symbol, valid_from, valid_to, source,
                    confidence, evidence, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    row["theme_id"],
                    row["symbol"].strip().upper(),
                    date.fromisoformat(row["valid_from"]),
                    _parse_date(row.get("valid_to")),
                    source,
                    float(row.get("confidence") or 0),
                    row.get("evidence") or None,
                    now,
                ],
            )
    return len(rows)
