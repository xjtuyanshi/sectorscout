from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database
from sectorscout.indicators import compute_technical_indicators
from sectorscout.metadata import build_run_metadata


@dataclass(frozen=True)
class MarketRegimeRow:
    asof_date: str
    spy_stage: str
    qqq_stage: str
    spy_above_50dma: bool
    spy_above_200dma: bool
    qqq_above_50dma: bool
    qqq_above_200dma: bool
    pct_universe_above_50dma: float
    pct_universe_above_200dma: float
    pct_universe_stage2: float
    risk_state: str
    signal_generated_at: str
    config_hash: str
    git_commit: str
    data_snapshot_id: str
    universe_version: str
    theme_version: str

    def to_dict(self) -> dict:
        return asdict(self)


def _load_indicator_rows(config: SectorScoutConfig, asof_date: date) -> list[dict]:
    with connect_database(config.database.path) as connection:
        rows = connection.execute(
            """
            SELECT
                symbol, close, sma_50, sma_200, trend_stage
            FROM technical_indicators
            WHERE asof_date = ?
            ORDER BY symbol
            """,
            [asof_date],
        ).fetchall()
    return [
        {
            "symbol": symbol,
            "close": close,
            "sma_50": sma_50,
            "sma_200": sma_200,
            "trend_stage": trend_stage,
        }
        for symbol, close, sma_50, sma_200, trend_stage in rows
    ]


def _above(close: float | None, average: float | None) -> bool:
    return close is not None and average is not None and close > average


def compute_market_regime(
    config: SectorScoutConfig,
    asof_date: date,
    *,
    persist: bool = True,
) -> MarketRegimeRow:
    rows = _load_indicator_rows(config, asof_date)
    if not rows:
        compute_technical_indicators(config, asof_date, persist=True)
        rows = _load_indicator_rows(config, asof_date)

    by_symbol = {row["symbol"]: row for row in rows}
    spy = by_symbol.get("SPY", {})
    qqq = by_symbol.get("QQQ", {})
    count = len(rows) or 1
    pct_above_50 = sum(_above(row["close"], row["sma_50"]) for row in rows) / count
    pct_above_200 = sum(_above(row["close"], row["sma_200"]) for row in rows) / count
    pct_stage2 = sum(row["trend_stage"] == "Stage 2" for row in rows) / count

    spy_above_50 = _above(spy.get("close"), spy.get("sma_50"))
    spy_above_200 = _above(spy.get("close"), spy.get("sma_200"))
    qqq_above_50 = _above(qqq.get("close"), qqq.get("sma_50"))
    qqq_above_200 = _above(qqq.get("close"), qqq.get("sma_200"))

    if (
        spy.get("trend_stage", "UNKNOWN") == "Stage 2"
        and qqq.get("trend_stage", "UNKNOWN") == "Stage 2"
        and spy_above_50
        and spy_above_200
        and qqq_above_50
        and qqq_above_200
        and pct_above_50 >= config.market_regime.risk_on_min_pct_above_50dma
        and pct_stage2 >= config.market_regime.risk_on_min_pct_stage2
    ):
        risk_state = "RISK_ON"
    elif (
        spy_above_200
        and qqq_above_200
        and pct_above_200 >= config.market_regime.neutral_min_pct_above_200dma
    ):
        risk_state = "NEUTRAL"
    else:
        risk_state = "RISK_OFF"

    metadata = build_run_metadata(config, "market-regime", asof_date=asof_date)
    regime = MarketRegimeRow(
        asof_date=asof_date.isoformat(),
        spy_stage=spy.get("trend_stage", "UNKNOWN"),
        qqq_stage=qqq.get("trend_stage", "UNKNOWN"),
        spy_above_50dma=spy_above_50,
        spy_above_200dma=spy_above_200,
        qqq_above_50dma=qqq_above_50,
        qqq_above_200dma=qqq_above_200,
        pct_universe_above_50dma=pct_above_50,
        pct_universe_above_200dma=pct_above_200,
        pct_universe_stage2=pct_stage2,
        risk_state=risk_state,
        signal_generated_at=metadata.signal_generated_at,
        config_hash=metadata.config_hash,
        git_commit=metadata.git_commit,
        data_snapshot_id=metadata.data_snapshot_id,
        universe_version=metadata.universe_version,
        theme_version=metadata.theme_version,
    )
    if persist:
        persist_market_regime(config, regime)
    return regime


def persist_market_regime(config: SectorScoutConfig, regime: MarketRegimeRow) -> None:
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO market_regime (
                asof_date, spy_stage, qqq_stage, spy_above_50dma,
                spy_above_200dma, qqq_above_50dma, qqq_above_200dma,
                pct_universe_above_50dma, pct_universe_above_200dma,
                pct_universe_stage2, risk_state, signal_generated_at_utc,
                config_hash, git_commit, data_snapshot_id, universe_version,
                theme_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                date.fromisoformat(regime.asof_date),
                regime.spy_stage,
                regime.qqq_stage,
                regime.spy_above_50dma,
                regime.spy_above_200dma,
                regime.qqq_above_50dma,
                regime.qqq_above_200dma,
                regime.pct_universe_above_50dma,
                regime.pct_universe_above_200dma,
                regime.pct_universe_stage2,
                regime.risk_state,
                regime.signal_generated_at,
                regime.config_hash,
                regime.git_commit,
                regime.data_snapshot_id,
                regime.universe_version,
                regime.theme_version,
            ],
        )
