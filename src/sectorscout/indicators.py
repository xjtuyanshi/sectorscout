from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime

import pandas as pd

from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database
from sectorscout.metadata import build_run_metadata
from sectorscout.pit import BENCHMARK_SYMBOLS, tradable_universe_asof


@dataclass(frozen=True)
class TechnicalIndicatorRow:
    asof_date: str
    symbol: str
    close: float
    sma_50: float | None
    sma_150: float | None
    sma_200: float | None
    ema_21: float | None
    atr_14: float | None
    volume_10d_avg: float | None
    volume_50d_avg: float | None
    pct_from_252d_high: float | None
    rs_lookback_return: float | None
    rs_percentile: float | None
    trend_stage: str
    universe_eligible: bool
    benchmark_symbol: bool
    signal_generated_at: str
    config_hash: str
    git_commit: str
    data_snapshot_id: str
    universe_version: str
    theme_version: str

    def to_dict(self) -> dict:
        return asdict(self)


def _optional_float(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _load_adjusted_prices(config: SectorScoutConfig, asof_date: date) -> pd.DataFrame:
    with connect_database(config.database.path) as connection:
        return connection.execute(
            """
            SELECT
                symbol,
                price_date,
                adj_open,
                adj_high,
                adj_low,
                adj_close,
                adj_volume
            FROM daily_prices
            WHERE price_date <= ?
            ORDER BY symbol, price_date
            """,
            [asof_date],
        ).fetchdf()


def _date_value(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "date"):
        return value.date()
    return date.fromisoformat(str(value))


def _trend_stage(row: pd.Series, config: SectorScoutConfig) -> str:
    required = ["sma_50", "sma_150", "sma_200", "sma_200_prior_20", "high_252"]
    if any(pd.isna(row[column]) for column in required):
        return "UNKNOWN"
    close = float(row["adj_close"])
    above_moving_averages = close > row["sma_50"] > row["sma_150"] > row["sma_200"]
    high_ratio = close / float(row["high_252"]) if row["high_252"] else 0
    sma_200_rising = row["sma_200"] > row["sma_200_prior_20"]
    if above_moving_averages and sma_200_rising and high_ratio >= config.indicators.stage2_min_pct_of_high:
        return "Stage 2"
    if close < row["sma_50"] and close < row["sma_200"]:
        return "Stage 4"
    return "Stage 1/3"


def compute_technical_indicators(
    config: SectorScoutConfig,
    asof_date: date,
    *,
    persist: bool = True,
) -> list[TechnicalIndicatorRow]:
    prices = _load_adjusted_prices(config, asof_date)
    if prices.empty:
        return []
    tradable_symbols = set(tradable_universe_asof(config, asof_date, mode="historical"))
    symbols_to_compute = tradable_symbols | BENCHMARK_SYMBOLS
    prices = prices[prices["symbol"].isin(symbols_to_compute)]
    if prices.empty:
        return []

    rows: list[dict] = []
    rs_values: dict[str, float] = {}
    lookback = config.indicators.rs_lookback_days
    high_window = config.indicators.stage2_high_window_days
    metadata = build_run_metadata(config, "compute-indicators", asof_date=asof_date)

    for symbol, group in prices.groupby("symbol", sort=True):
        frame = group.sort_values("price_date").copy()
        if _date_value(frame.iloc[-1]["price_date"]) != asof_date:
            continue

        previous_close = frame["adj_close"].shift(1)
        true_range = pd.concat(
            [
                frame["adj_high"] - frame["adj_low"],
                (frame["adj_high"] - previous_close).abs(),
                (frame["adj_low"] - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)

        frame["sma_50"] = frame["adj_close"].rolling(50).mean()
        frame["sma_150"] = frame["adj_close"].rolling(150).mean()
        frame["sma_200"] = frame["adj_close"].rolling(200).mean()
        frame["sma_200_prior_20"] = frame["sma_200"].shift(20)
        frame["ema_21"] = frame["adj_close"].ewm(span=21, adjust=False).mean()
        frame["atr_14"] = true_range.rolling(14).mean()
        frame["volume_10d_avg"] = frame["adj_volume"].rolling(10).mean()
        frame["volume_50d_avg"] = frame["adj_volume"].rolling(50).mean()
        frame["high_252"] = frame["adj_high"].rolling(high_window, min_periods=1).max()
        frame["rs_lookback_return"] = frame["adj_close"] / frame["adj_close"].shift(lookback) - 1

        latest = frame.iloc[-1]
        rs_return = _optional_float(latest["rs_lookback_return"])
        universe_eligible = symbol in tradable_symbols
        benchmark_symbol = symbol in BENCHMARK_SYMBOLS
        if universe_eligible and rs_return is not None:
            rs_values[symbol] = rs_return
        rows.append(
            {
                "symbol": symbol,
                "close": float(latest["adj_close"]),
                "sma_50": _optional_float(latest["sma_50"]),
                "sma_150": _optional_float(latest["sma_150"]),
                "sma_200": _optional_float(latest["sma_200"]),
                "ema_21": _optional_float(latest["ema_21"]),
                "atr_14": _optional_float(latest["atr_14"]),
                "volume_10d_avg": _optional_float(latest["volume_10d_avg"]),
                "volume_50d_avg": _optional_float(latest["volume_50d_avg"]),
                "pct_from_252d_high": (
                    float(latest["adj_close"] / latest["high_252"])
                    if latest["high_252"]
                    else None
                ),
                "rs_lookback_return": rs_return,
                "trend_stage": _trend_stage(latest, config),
                "universe_eligible": universe_eligible,
                "benchmark_symbol": benchmark_symbol,
            }
        )

    ranked = pd.Series(rs_values, dtype="float64").rank(pct=True) * 100
    indicator_rows = [
        TechnicalIndicatorRow(
            asof_date=asof_date.isoformat(),
            symbol=row["symbol"],
            close=row["close"],
            sma_50=row["sma_50"],
            sma_150=row["sma_150"],
            sma_200=row["sma_200"],
            ema_21=row["ema_21"],
            atr_14=row["atr_14"],
            volume_10d_avg=row["volume_10d_avg"],
            volume_50d_avg=row["volume_50d_avg"],
            pct_from_252d_high=row["pct_from_252d_high"],
            rs_lookback_return=row["rs_lookback_return"],
            rs_percentile=_optional_float(ranked.get(row["symbol"])),
            trend_stage=row["trend_stage"],
            universe_eligible=row["universe_eligible"],
            benchmark_symbol=row["benchmark_symbol"],
            signal_generated_at=metadata.signal_generated_at,
            config_hash=metadata.config_hash,
            git_commit=metadata.git_commit,
            data_snapshot_id=metadata.data_snapshot_id,
            universe_version=metadata.universe_version,
            theme_version=metadata.theme_version,
        )
        for row in rows
    ]

    if persist:
        persist_technical_indicators(config, indicator_rows, asof_date=asof_date)
    return indicator_rows


def persist_technical_indicators(
    config: SectorScoutConfig,
    rows: list[TechnicalIndicatorRow],
    *,
    asof_date: date | None = None,
) -> None:
    if not rows and asof_date is None:
        return
    target_date = asof_date or date.fromisoformat(rows[0].asof_date)
    with connect_database(config.database.path) as connection:
        connection.execute("DELETE FROM technical_indicators WHERE asof_date = ?", [target_date])
        for row in rows:
            connection.execute(
                """
                INSERT INTO technical_indicators (
                    asof_date, symbol, close, sma_50, sma_150, sma_200,
                    ema_21, atr_14, volume_10d_avg, volume_50d_avg,
                    pct_from_252d_high, rs_lookback_return, rs_percentile,
                    trend_stage, universe_eligible, benchmark_symbol,
                    signal_generated_at_utc, config_hash,
                    git_commit, data_snapshot_id, universe_version, theme_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    target_date,
                    row.symbol,
                    row.close,
                    row.sma_50,
                    row.sma_150,
                    row.sma_200,
                    row.ema_21,
                    row.atr_14,
                    row.volume_10d_avg,
                    row.volume_50d_avg,
                    row.pct_from_252d_high,
                    row.rs_lookback_return,
                    row.rs_percentile,
                    row.trend_stage,
                    row.universe_eligible,
                    row.benchmark_symbol,
                    row.signal_generated_at,
                    row.config_hash,
                    row.git_commit,
                    row.data_snapshot_id,
                    row.universe_version,
                    row.theme_version,
                ],
            )
