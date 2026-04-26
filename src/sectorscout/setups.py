from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date

import pandas as pd

from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database
from sectorscout.market_calendar import asof_market_close
from sectorscout.market_regime import compute_market_regime
from sectorscout.metadata import build_run_metadata
from sectorscout.portfolio import market_gate_allows_new_long, portfolio_risk_passes
from sectorscout.scoring import run_scoring


@dataclass(frozen=True)
class SetupRow:
    asof_date: str
    symbol: str
    theme_id: str
    setup_type: str
    state: str
    setup_quality: float
    trigger_price: float | None
    stop_loss: float | None
    reward_risk: float | None
    consolidation_start: str | None
    consolidation_end: str | None
    pivot: float | None
    depth: float | None
    atr_contraction: bool
    volume_contraction: bool
    evidence: dict
    signal_generated_at: str
    config_hash: str
    git_commit: str
    data_snapshot_id: str
    universe_version: str
    theme_version: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SignalRow:
    asof_date: str
    symbol: str
    theme_id: str
    setup_type: str
    state: str
    action_category: str
    actionable: bool
    reason: str
    execution_model: str
    entry_trigger: float | None
    stop_loss: float | None
    reward_risk: float | None
    data_quality_pass: bool
    data_quality_reason: str
    market_gate_pass: bool
    market_gate_reason: str
    market_regime_risk_state: str
    portfolio_risk_pass: bool
    portfolio_risk_reason: str
    signal_generated_at: str
    config_hash: str
    git_commit: str
    data_snapshot_id: str
    universe_version: str
    theme_version: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SetupRunResult:
    asof_date: str
    setups: list[dict]
    signals: list[dict]

    def to_dict(self) -> dict:
        return asdict(self)


def _load_prices(config: SectorScoutConfig, symbol: str, asof_date: date) -> pd.DataFrame:
    with connect_database(config.database.path) as connection:
        frame = connection.execute(
            """
            SELECT
                price_date, adj_open, adj_high, adj_low, adj_close, adj_volume
            FROM daily_prices
            WHERE symbol = ? AND price_date <= ?
            ORDER BY price_date
            """,
            [symbol, asof_date],
        ).fetchdf()
    return frame.rename(
        columns={
            "price_date": "date",
            "adj_open": "open",
            "adj_high": "high",
            "adj_low": "low",
            "adj_close": "close",
            "adj_volume": "volume",
        }
    )


def _load_stock_candidates(config: SectorScoutConfig, asof_date: date) -> list[dict]:
    with connect_database(config.database.path) as connection:
        rows = connection.execute(
            """
            SELECT
                ss.symbol, ss.theme_id, ss.theme_score, ss.rs_percentile,
                ss.state, ti.trend_stage, ti.ema_21, ti.sma_50,
                ti.atr_14, ti.pct_from_252d_high, ti.volume_50d_avg
            FROM stock_scores ss
            JOIN technical_indicators ti
              ON ti.asof_date = ss.asof_date
             AND ti.symbol = ss.symbol
            WHERE ss.asof_date = ?
            ORDER BY ss.stock_opportunity_score DESC, ss.symbol
            """,
            [asof_date],
        ).fetchall()
    columns = [
        "symbol",
        "theme_id",
        "theme_score",
        "rs_percentile",
        "state",
        "trend_stage",
        "ema_21",
        "sma_50",
        "atr_14",
        "pct_from_252d_high",
        "volume_50d_avg",
    ]
    return [dict(zip(columns, row, strict=True)) for row in rows]


def _date_str(value: object) -> str:
    if hasattr(value, "date"):
        return value.date().isoformat()
    return str(value)


def _true_range(frame: pd.DataFrame) -> pd.Series:
    previous_close = frame["close"].shift(1)
    return pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def _estimated_reward_risk(entry: float | None, stop: float | None) -> float | None:
    if entry is None or stop is None or stop >= entry:
        return None
    risk = entry - stop
    target_2r = entry + 2 * risk
    return (target_2r - entry) / risk


def _known_earnings_gap_dates(
    config: SectorScoutConfig,
    symbol: str,
    asof_date: date,
    prices: pd.DataFrame,
) -> set[date]:
    if prices.empty:
        return set()
    asof_close = asof_market_close(asof_date, config)
    with connect_database(config.database.path) as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT earnings_release_datetime
            FROM fundamental_facts
            WHERE symbol = ?
              AND earnings_release_datetime IS NOT NULL
              AND earnings_release_datetime <= ?
              AND available_at IS NOT NULL
              AND available_at <= ?
            ORDER BY earnings_release_datetime
            """,
            [symbol, asof_close, asof_close],
        ).fetchall()
    price_dates = [
        value.date() if hasattr(value, "date") else date.fromisoformat(str(value))
        for value in prices["date"].tolist()
    ]
    gap_dates: set[date] = set()
    for (release_datetime,) in rows:
        release_date = release_datetime.date()
        next_sessions = [session for session in price_dates if session > release_date]
        if next_sessions:
            gap_dates.add(next_sessions[0])
    return gap_dates


def _rs_line_holds(config: SectorScoutConfig, prices: pd.DataFrame, asof_date: date) -> bool:
    spy = _load_prices(config, "SPY", asof_date)
    if len(spy) < 20:
        return False
    merged = prices[["date", "close"]].merge(
        spy[["date", "close"]],
        on="date",
        how="inner",
        suffixes=("", "_spy"),
    )
    if len(merged) < 20:
        return False
    rs_line = merged["close"] / merged["close_spy"]
    recent_low = rs_line.tail(20).min()
    return bool(rs_line.iloc[-1] >= recent_low)


def _detect_vcp(
    config: SectorScoutConfig,
    candidate: dict,
    prices: pd.DataFrame,
    metadata,
) -> SetupRow | None:
    if candidate["trend_stage"] != "Stage 2":
        return None
    if candidate["rs_percentile"] is None or candidate["rs_percentile"] < config.setups.vcp_min_rs_percentile:
        return None
    if candidate["pct_from_252d_high"] is None or candidate["pct_from_252d_high"] < 0.85:
        return None
    if len(prices) < config.setups.vcp_max_window + 20:
        return None

    frame = prices.copy()
    frame["range"] = frame["high"] - frame["low"]
    frame["atr_pct"] = _true_range(frame).rolling(14).mean() / frame["close"]
    frame["volume_10d_avg"] = frame["volume"].rolling(10).mean()
    frame["volume_50d_avg"] = frame["volume"].rolling(50).mean()
    latest = frame.iloc[-1]
    candidates: list[dict] = []

    for window_length in range(config.setups.vcp_min_window, config.setups.vcp_max_window + 1):
        window = frame.iloc[-window_length:]
        previous = frame.iloc[-(window_length * 2) : -window_length]
        if len(previous) < window_length:
            continue
        window_high = float(window["high"].max())
        window_low = float(window["low"].min())
        depth = (window_high - window_low) / window_high
        if depth > config.setups.vcp_max_depth:
            continue
        pivot = float(window.iloc[:-1]["high"].max())
        current_range = float(window["range"].max())
        previous_range = float(previous["range"].max())
        range_contraction = current_range < previous_range
        atr_contraction = frame["atr_pct"].iloc[-10:].mean() < frame["atr_pct"].iloc[-30:-10].mean()
        volume_contraction = latest["volume_10d_avg"] < latest["volume_50d_avg"]
        if not (range_contraction and atr_contraction and volume_contraction):
            continue
        trigger = (
            latest["close"] > pivot
            and latest["volume"] > config.setups.vcp_min_volume_breakout_ratio * latest["volume_50d_avg"]
        )
        quality = (
            (1 - depth / config.setups.vcp_max_depth) * 35
            + min(float(candidate["rs_percentile"]), 100) * 0.35
            + (20 if atr_contraction else 0)
            + (10 if volume_contraction else 0)
        )
        candidates.append(
            {
                "window_length": window_length,
                "window": window,
                "depth": depth,
                "pivot": pivot,
                "trigger": trigger,
                "quality": max(0.0, min(100.0, quality)),
                "atr_contraction": atr_contraction,
                "volume_contraction": volume_contraction,
            }
        )

    if not candidates:
        return None
    selected = sorted(candidates, key=lambda row: (row["window_length"], -row["quality"]))[0]
    selected_window = selected["window"]
    entry = selected["pivot"] if selected["trigger"] else None
    stop = float(selected_window["low"].min())
    return SetupRow(
        asof_date=metadata.asof_date,
        symbol=candidate["symbol"],
        theme_id=candidate["theme_id"],
        setup_type="VCP",
        state="TRIGGERED" if selected["trigger"] else "SETUP",
        setup_quality=selected["quality"],
        trigger_price=entry,
        stop_loss=stop if entry else None,
        reward_risk=_estimated_reward_risk(entry, stop),
        consolidation_start=_date_str(selected_window.iloc[0]["date"]),
        consolidation_end=_date_str(selected_window.iloc[-1]["date"]),
        pivot=selected["pivot"],
        depth=selected["depth"],
        atr_contraction=bool(selected["atr_contraction"]),
        volume_contraction=bool(selected["volume_contraction"]),
        evidence={
            "window_length": selected["window_length"],
            "range_contraction": True,
            "volume_breakout_ratio": (
                float(latest["volume"] / latest["volume_50d_avg"])
                if latest["volume_50d_avg"]
                else None
            ),
        },
        signal_generated_at=metadata.signal_generated_at,
        config_hash=metadata.config_hash,
        git_commit=metadata.git_commit,
        data_snapshot_id=metadata.data_snapshot_id,
        universe_version=metadata.universe_version,
        theme_version=metadata.theme_version,
    )


def _detect_pullback(
    config: SectorScoutConfig,
    candidate: dict,
    prices: pd.DataFrame,
    metadata,
) -> SetupRow | None:
    if candidate["trend_stage"] != "Stage 2":
        return None
    if candidate["theme_score"] < 75 or candidate["rs_percentile"] < 80:
        return None
    if len(prices) < 60:
        return None
    frame = prices.copy()
    recent_high = float(frame["high"].iloc[-20:].max())
    latest = frame.iloc[-1]
    pullback_depth = (recent_high - float(latest["close"])) / recent_high
    if not (config.setups.pullback_min_depth <= pullback_depth <= config.setups.pullback_max_depth):
        return None
    near_ema = candidate["ema_21"] and latest["low"] <= candidate["ema_21"] * (1 + config.setups.pullback_ma_tolerance)
    near_sma = candidate["sma_50"] and latest["low"] <= candidate["sma_50"] * (1 + config.setups.pullback_ma_tolerance)
    volume_ok = frame["volume"].iloc[-5:].mean() < frame["volume"].iloc[-20:].mean()
    prior_3d_high = float(frame["high"].iloc[-4:-1].max())
    trigger = latest["close"] > prior_3d_high
    if not ((near_ema or near_sma) and volume_ok):
        return None
    if not _rs_line_holds(config, prices, date.fromisoformat(metadata.asof_date)):
        return None
    stop = float(frame["low"].iloc[-10:].min())
    entry = prior_3d_high if trigger else None
    return SetupRow(
        asof_date=metadata.asof_date,
        symbol=candidate["symbol"],
        theme_id=candidate["theme_id"],
        setup_type="PULLBACK",
        state="TRIGGERED" if trigger else "SETUP",
        setup_quality=max(0.0, min(100.0, 100 - pullback_depth * 300)),
        trigger_price=entry,
        stop_loss=stop if entry else None,
        reward_risk=_estimated_reward_risk(entry, stop),
        consolidation_start=_date_str(frame.iloc[-20]["date"]),
        consolidation_end=_date_str(latest["date"]),
        pivot=prior_3d_high,
        depth=pullback_depth,
        atr_contraction=False,
        volume_contraction=bool(volume_ok),
        evidence={"near_21ema": bool(near_ema), "near_50sma": bool(near_sma)},
        signal_generated_at=metadata.signal_generated_at,
        config_hash=metadata.config_hash,
        git_commit=metadata.git_commit,
        data_snapshot_id=metadata.data_snapshot_id,
        universe_version=metadata.universe_version,
        theme_version=metadata.theme_version,
    )


def _detect_earnings_gap_base(
    config: SectorScoutConfig,
    candidate: dict,
    prices: pd.DataFrame,
    metadata,
) -> SetupRow | None:
    if len(prices) < 80:
        return None
    known_gap_dates = _known_earnings_gap_dates(
        config,
        candidate["symbol"],
        date.fromisoformat(metadata.asof_date),
        prices,
    )
    if not known_gap_dates:
        return None
    frame = prices.copy()
    frame["prev_close"] = frame["close"].shift(1)
    frame["volume_50d_avg"] = frame["volume"].rolling(50).mean()
    frame["gap_pct"] = frame["open"] / frame["prev_close"] - 1
    gap_rows = frame[
        (frame["gap_pct"] >= config.setups.earnings_gap_min_gap_pct)
        & (frame["volume"] >= config.setups.earnings_gap_min_volume_ratio * frame["volume_50d_avg"])
    ].tail(1)
    if gap_rows.empty:
        return None
    gap_rows = gap_rows[
        gap_rows["date"].apply(
            lambda value: (value.date() if hasattr(value, "date") else date.fromisoformat(str(value)))
            in known_gap_dates
        )
    ]
    if gap_rows.empty:
        return None
    gap_index = gap_rows.index[-1]
    days_since_gap = len(frame.loc[gap_index:]) - 1
    if not (config.setups.earnings_gap_min_hold_days <= days_since_gap <= config.setups.earnings_gap_max_hold_days):
        return None
    gap_low = float(frame.loc[gap_index, "low"])
    post_gap = frame.loc[gap_index:]
    if float(post_gap["low"].min()) < gap_low:
        return None
    post_gap_high = float(post_gap.iloc[:-1]["high"].max())
    latest = frame.iloc[-1]
    trigger = latest["close"] > post_gap_high
    entry = post_gap_high if trigger else None
    return SetupRow(
        asof_date=metadata.asof_date,
        symbol=candidate["symbol"],
        theme_id=candidate["theme_id"],
        setup_type="EARNINGS_GAP_BASE",
        state="TRIGGERED" if trigger else "SETUP",
        setup_quality=75.0,
        trigger_price=entry,
        stop_loss=gap_low if entry else None,
        reward_risk=_estimated_reward_risk(entry, gap_low),
        consolidation_start=_date_str(frame.loc[gap_index, "date"]),
        consolidation_end=_date_str(latest["date"]),
        pivot=post_gap_high,
        depth=None,
        atr_contraction=False,
        volume_contraction=False,
        evidence={"gap_pct": float(frame.loc[gap_index, "gap_pct"])},
        signal_generated_at=metadata.signal_generated_at,
        config_hash=metadata.config_hash,
        git_commit=metadata.git_commit,
        data_snapshot_id=metadata.data_snapshot_id,
        universe_version=metadata.universe_version,
        theme_version=metadata.theme_version,
    )


def detect_setups(
    config: SectorScoutConfig,
    asof_date: date,
    *,
    persist: bool = True,
) -> SetupRunResult:
    run_scoring(config, asof_date)
    regime = compute_market_regime(config, asof_date)
    metadata = build_run_metadata(config, "detect-setups", asof_date=asof_date)
    candidates = _load_stock_candidates(config, asof_date)
    setups: list[SetupRow] = []
    for candidate in candidates:
        prices = _load_prices(config, candidate["symbol"], asof_date)
        if prices.empty:
            continue
        for detector in (_detect_vcp, _detect_pullback, _detect_earnings_gap_base):
            setup = detector(config, candidate, prices, metadata)
            if setup is not None:
                setups.append(setup)

    signals = build_signals(config, setups, regime.risk_state)
    if persist:
        persist_setups(config, asof_date, setups)
        persist_signals(config, asof_date, signals)
    return SetupRunResult(
        asof_date=asof_date.isoformat(),
        setups=[row.to_dict() for row in setups],
        signals=[row.to_dict() for row in signals],
    )


def build_signals(
    config: SectorScoutConfig,
    setups: list[SetupRow],
    market_risk_state: str,
) -> list[SignalRow]:
    triggered = sorted(
        [row for row in setups if row.state == "TRIGGERED"],
        key=lambda row: row.setup_quality,
        reverse=True,
    )
    rank_by_key = {
        (row.symbol, row.theme_id, row.setup_type): index for index, row in enumerate(triggered)
    }
    selected_theme_ids: list[str] = []
    selected_keys: set[tuple[str, str, str]] = set()
    signals: list[SignalRow] = []
    for setup in setups:
        market_gate_pass, market_gate_reason = market_gate_allows_new_long(market_risk_state)
        data_quality_pass = setup.trigger_price is not None and setup.stop_loss is not None
        data_quality_reason = (
            "Phase 4 setup data is present; true next-open R/R validation is deferred to Phase 5."
            if data_quality_pass
            else "Setup is not triggered or lacks entry/stop data."
        )
        rank = rank_by_key.get((setup.symbol, setup.theme_id, setup.setup_type), 10**6)
        portfolio_pass, portfolio_reason = portfolio_risk_passes(
            config,
            candidate_rank=rank,
            theme_id=setup.theme_id,
            selected_theme_ids=selected_theme_ids,
        )
        phase5_execution_ready = False
        passes_all_research_gates = (
            setup.state == "TRIGGERED"
            and market_gate_pass
            and data_quality_pass
            and portfolio_pass
        )
        key = (setup.symbol, setup.theme_id, setup.setup_type)
        if passes_all_research_gates:
            selected_theme_ids.append(setup.theme_id)
            selected_keys.add(key)

        actionable = passes_all_research_gates and phase5_execution_ready
        if not market_gate_pass:
            category = "Blocked by market regime"
            reason = market_gate_reason
        elif setup.state == "TRIGGERED" and key in selected_keys:
            category = "Triggered setup candidate"
            reason = "Triggered setup passes Phase 4 research gates; Phase 5 execution/RR validation required."
        elif setup.state == "SETUP":
            category = "Setup forming"
            reason = "Setup structure exists but trigger has not fired."
        else:
            category = "Watchlist"
            reason = portfolio_reason

        signals.append(
            SignalRow(
                asof_date=setup.asof_date,
                symbol=setup.symbol,
                theme_id=setup.theme_id,
                setup_type=setup.setup_type,
                state=setup.state if market_risk_state != "RISK_OFF" else "RISK_OFF",
                action_category=category,
                actionable=actionable,
                reason=reason,
                execution_model="next_open",
                entry_trigger=setup.trigger_price,
                stop_loss=setup.stop_loss,
                reward_risk=setup.reward_risk,
                data_quality_pass=data_quality_pass,
                data_quality_reason=data_quality_reason,
                market_gate_pass=market_gate_pass,
                market_gate_reason=market_gate_reason,
                market_regime_risk_state=market_risk_state,
                portfolio_risk_pass=portfolio_pass,
                portfolio_risk_reason=portfolio_reason,
                signal_generated_at=setup.signal_generated_at,
                config_hash=setup.config_hash,
                git_commit=setup.git_commit,
                data_snapshot_id=setup.data_snapshot_id,
                universe_version=setup.universe_version,
                theme_version=setup.theme_version,
            )
        )
    return signals


def persist_setups(config: SectorScoutConfig, asof_date: date, rows: list[SetupRow]) -> None:
    with connect_database(config.database.path) as connection:
        connection.execute("DELETE FROM setups WHERE asof_date = ?", [asof_date])
        for row in rows:
            connection.execute(
                """
                INSERT INTO setups (
                    asof_date, symbol, theme_id, setup_type, state,
                    setup_quality, trigger_price, stop_loss, reward_risk,
                    consolidation_start, consolidation_end, pivot_price, depth,
                    atr_contraction, volume_contraction, evidence_json,
                    signal_generated_at_utc, config_hash, git_commit,
                    data_snapshot_id, universe_version, theme_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    asof_date,
                    row.symbol,
                    row.theme_id,
                    row.setup_type,
                    row.state,
                    row.setup_quality,
                    row.trigger_price,
                    row.stop_loss,
                    row.reward_risk,
                    date.fromisoformat(row.consolidation_start) if row.consolidation_start else None,
                    date.fromisoformat(row.consolidation_end) if row.consolidation_end else None,
                    row.pivot,
                    row.depth,
                    row.atr_contraction,
                    row.volume_contraction,
                    json.dumps(row.evidence, sort_keys=True),
                    row.signal_generated_at,
                    row.config_hash,
                    row.git_commit,
                    row.data_snapshot_id,
                    row.universe_version,
                    row.theme_version,
                ],
            )


def persist_signals(config: SectorScoutConfig, asof_date: date, rows: list[SignalRow]) -> None:
    with connect_database(config.database.path) as connection:
        connection.execute("DELETE FROM signals WHERE asof_date = ?", [asof_date])
        for row in rows:
            connection.execute(
                """
                INSERT INTO signals (
                    asof_date, symbol, theme_id, setup_type, state,
                    action_category, actionable, reason, execution_model,
                    entry_trigger, stop_loss, reward_risk, data_quality_pass,
                    data_quality_reason, market_gate_pass, market_gate_reason,
                    market_regime_risk_state, portfolio_risk_pass,
                    portfolio_risk_reason,
                    signal_generated_at_utc, config_hash, git_commit,
                    data_snapshot_id, universe_version, theme_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    asof_date,
                    row.symbol,
                    row.theme_id,
                    row.setup_type,
                    row.state,
                    row.action_category,
                    row.actionable,
                    row.reason,
                    row.execution_model,
                    row.entry_trigger,
                    row.stop_loss,
                    row.reward_risk,
                    row.data_quality_pass,
                    row.data_quality_reason,
                    row.market_gate_pass,
                    row.market_gate_reason,
                    row.market_regime_risk_state,
                    row.portfolio_risk_pass,
                    row.portfolio_risk_reason,
                    row.signal_generated_at,
                    row.config_hash,
                    row.git_commit,
                    row.data_snapshot_id,
                    row.universe_version,
                    row.theme_version,
                ],
            )
