from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from uuid import uuid4

import pandas as pd

from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database
from sectorscout.metadata import build_run_metadata
from sectorscout.pit import BENCHMARK_SYMBOLS
from sectorscout.prices import load_price_snapshot


ACCEPTED_DECISION = "SIMULATED_NEXT_OPEN_ACCEPTED"
EXIT_PRIORITIES = {
    "GAP_DOWN_STOP_AT_OPEN": 0,
    "HARD_STOP_INTRADAY": 1,
    "RISK_OFF_POLICY_PLACEHOLDER": 2,
    "TREND_FAILURE_CLOSE_BELOW_50SMA": 3,
    "THEME_SCORE_DETERIORATION_PLACEHOLDER": 4,
    "TIME_STOP_NO_1R_WITHIN_20D": 5,
}


@dataclass(frozen=True)
class SimulatedPosition:
    lifecycle_run_id: str
    execution_run_id: str
    asof_date: str
    symbol: str
    theme_id: str
    setup_type: str
    execution_model: str
    entry_date: str
    entry_price: float
    initial_stop_loss: float
    risk_per_share: float
    target_1r: float
    target_2r: float | None
    target_3r: float | None
    status: str
    exit_date: str | None
    exit_price: float | None
    exit_reason: str | None
    lifecycle_generated_at: str
    lifecycle_config_hash: str
    lifecycle_git_commit: str
    lifecycle_data_snapshot_id: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ExitDecision:
    lifecycle_run_id: str
    execution_run_id: str
    asof_date: str
    symbol: str
    theme_id: str
    setup_type: str
    execution_model: str
    exit_reason: str
    exit_date: str
    exit_price: float
    evidence: dict
    lifecycle_generated_at: str
    lifecycle_config_hash: str
    lifecycle_git_commit: str
    lifecycle_data_snapshot_id: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PositionLifecycleRunResult:
    lifecycle_run_id: str
    execution_run_id: str
    through_date: str
    positions: list[dict]
    exit_decisions: list[dict]
    baseline_symbols: list[str]
    warning: str

    def to_dict(self) -> dict:
        return asdict(self)


def _date_value(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "date"):
        return value.date()
    return date.fromisoformat(str(value))


def _load_accepted_executions(
    config: SectorScoutConfig,
    execution_run_id: str,
) -> list[dict]:
    with connect_database(config.database.path) as connection:
        rows = connection.execute(
            """
            SELECT
                execution_run_id,
                asof_date,
                symbol,
                theme_id,
                setup_type,
                execution_model,
                next_session_date,
                actual_entry_price,
                actual_stop_loss,
                risk_per_share,
                target_2r,
                target_3r
            FROM execution_decisions
            WHERE execution_run_id = ?
              AND decision = ?
              AND execution_data_quality_pass = true
              AND execution_rule_pass = true
              AND risk_rule_pass = true
            ORDER BY asof_date, symbol, theme_id, setup_type
            """,
            [execution_run_id, ACCEPTED_DECISION],
        ).fetchall()
    columns = [
        "execution_run_id",
        "asof_date",
        "symbol",
        "theme_id",
        "setup_type",
        "execution_model",
        "entry_date",
        "entry_price",
        "initial_stop_loss",
        "risk_per_share",
        "target_2r",
        "target_3r",
    ]
    return [dict(zip(columns, row, strict=True)) for row in rows]


def _load_prices(config: SectorScoutConfig, symbol: str, through_date: date) -> pd.DataFrame:
    prices, _duplicate_count = load_price_snapshot(config, through_date, symbols=[symbol])
    if prices.empty:
        return prices
    frame = prices.rename(
        columns={
            "price_date": "date",
            "adj_open": "open",
            "adj_high": "high",
            "adj_low": "low",
            "adj_close": "close",
            "adj_volume": "volume",
        }
    ).copy()
    frame["date"] = frame["date"].apply(_date_value)
    return frame.sort_values("date").reset_index(drop=True)


def _next_price_row_after(prices: pd.DataFrame, event_date: date) -> pd.Series | None:
    later = prices[prices["date"] > event_date]
    if later.empty:
        return None
    return later.iloc[0]


def _event(
    reason: str,
    exit_date: date,
    exit_price: float,
    evidence: dict,
) -> dict:
    return {
        "exit_reason": reason,
        "exit_date": exit_date,
        "exit_price": exit_price,
        "evidence": evidence,
    }


def _stop_events(prices: pd.DataFrame, entry_date: date, stop_loss: float) -> list[dict]:
    rows = prices[prices["date"] >= entry_date]
    events: list[dict] = []
    for _, row in rows.iterrows():
        row_date = _date_value(row["date"])
        if float(row["open"]) <= stop_loss:
            events.append(
                _event(
                    "GAP_DOWN_STOP_AT_OPEN",
                    row_date,
                    float(row["open"]),
                    {"open": float(row["open"]), "stop_loss": stop_loss},
                )
            )
            break
        if float(row["low"]) <= stop_loss:
            events.append(
                _event(
                    "HARD_STOP_INTRADAY",
                    row_date,
                    stop_loss,
                    {"low": float(row["low"]), "stop_loss": stop_loss},
                )
            )
            break
    return events


def _time_stop_event(
    prices: pd.DataFrame,
    entry_date: date,
    entry_price: float,
    risk_per_share: float,
    time_stop_days: int,
) -> dict | None:
    rows = prices[prices["date"] >= entry_date].reset_index(drop=True)
    if len(rows) <= time_stop_days:
        return None
    target_1r = entry_price + risk_per_share
    window = rows.iloc[:time_stop_days]
    if float(window["high"].max()) >= target_1r:
        return None
    exit_row = rows.iloc[time_stop_days]
    return _event(
        "TIME_STOP_NO_1R_WITHIN_20D",
        _date_value(exit_row["date"]),
        float(exit_row["open"]),
        {"target_1r": target_1r, "observed_days": time_stop_days},
    )


def _trend_failure_event(
    prices: pd.DataFrame,
    entry_date: date,
) -> dict | None:
    frame = prices.copy()
    frame["sma_50"] = frame["close"].rolling(50).mean()
    entry_rows = frame[frame["date"] >= entry_date]
    for index, row in entry_rows.iterrows():
        if pd.isna(row["sma_50"]):
            continue
        if float(row["close"]) < float(row["sma_50"]):
            next_row = _next_price_row_after(frame, _date_value(row["date"]))
            if next_row is None:
                return None
            return _event(
                "TREND_FAILURE_CLOSE_BELOW_50SMA",
                _date_value(next_row["date"]),
                float(next_row["open"]),
                {"close": float(row["close"]), "sma_50": float(row["sma_50"])},
            )
    return None


def _risk_off_event(
    config: SectorScoutConfig,
    prices: pd.DataFrame,
    entry_date: date,
    through_date: date,
) -> dict | None:
    with connect_database(config.database.path) as connection:
        row = connection.execute(
            """
            SELECT asof_date
            FROM market_regime
            WHERE asof_date >= ?
              AND asof_date <= ?
              AND risk_state = 'RISK_OFF'
            ORDER BY asof_date
            LIMIT 1
            """,
            [entry_date, through_date],
        ).fetchone()
    if row is None:
        return None
    event_date = _date_value(row[0])
    next_row = _next_price_row_after(prices, event_date)
    if next_row is None:
        return None
    return _event(
        "RISK_OFF_POLICY_PLACEHOLDER",
        _date_value(next_row["date"]),
        float(next_row["open"]),
        {"risk_off_date": event_date.isoformat()},
    )


def _theme_failure_event(
    config: SectorScoutConfig,
    prices: pd.DataFrame,
    theme_id: str,
    entry_date: date,
    through_date: date,
) -> dict | None:
    with connect_database(config.database.path) as connection:
        rows = connection.execute(
            """
            SELECT asof_date, theme_score
            FROM theme_scores
            WHERE theme_id = ?
              AND asof_date >= ?
              AND asof_date <= ?
            ORDER BY asof_date
            """,
            [theme_id, entry_date, through_date],
        ).fetchall()
    streak = 0
    for score_date_raw, theme_score in rows:
        score_date = _date_value(score_date_raw)
        if float(theme_score) < config.lifecycle.theme_failure_score_threshold:
            streak += 1
        else:
            streak = 0
        if streak >= config.lifecycle.theme_failure_consecutive_days:
            next_row = _next_price_row_after(prices, score_date)
            if next_row is None:
                return None
            return _event(
                "THEME_SCORE_DETERIORATION_PLACEHOLDER",
                _date_value(next_row["date"]),
                float(next_row["open"]),
                {
                    "theme_score": float(theme_score),
                    "consecutive_days": streak,
                    "threshold": config.lifecycle.theme_failure_score_threshold,
                },
            )
    return None


def _select_exit_event(events: list[dict]) -> dict | None:
    if not events:
        return None
    return sorted(
        events,
        key=lambda row: (
            row["exit_date"],
            EXIT_PRIORITIES.get(row["exit_reason"], 99),
        ),
    )[0]


def _exit_for_execution(
    config: SectorScoutConfig,
    execution: dict,
    through_date: date,
) -> dict | None:
    entry_date = _date_value(execution["entry_date"])
    prices = _load_prices(config, execution["symbol"], through_date)
    if prices.empty:
        return None
    stop_loss = float(execution["initial_stop_loss"])
    entry_price = float(execution["entry_price"])
    risk_per_share = float(execution["risk_per_share"])
    events = _stop_events(prices, entry_date, stop_loss)
    for maybe_event in (
        _risk_off_event(config, prices, entry_date, through_date),
        _trend_failure_event(prices, entry_date),
        _theme_failure_event(config, prices, execution["theme_id"], entry_date, through_date),
        _time_stop_event(
            prices,
            entry_date,
            entry_price,
            risk_per_share,
            config.lifecycle.time_stop_days,
        ),
    ):
        if maybe_event is not None:
            events.append(maybe_event)
    return _select_exit_event(events)


def _build_position(
    execution: dict,
    exit_event: dict | None,
    lifecycle_run_id: str,
    metadata,
) -> SimulatedPosition:
    entry_price = float(execution["entry_price"])
    risk_per_share = float(execution["risk_per_share"])
    return SimulatedPosition(
        lifecycle_run_id=lifecycle_run_id,
        execution_run_id=execution["execution_run_id"],
        asof_date=_date_value(execution["asof_date"]).isoformat(),
        symbol=execution["symbol"],
        theme_id=execution["theme_id"],
        setup_type=execution["setup_type"],
        execution_model=execution["execution_model"],
        entry_date=_date_value(execution["entry_date"]).isoformat(),
        entry_price=entry_price,
        initial_stop_loss=float(execution["initial_stop_loss"]),
        risk_per_share=risk_per_share,
        target_1r=entry_price + risk_per_share,
        target_2r=execution["target_2r"],
        target_3r=execution["target_3r"],
        status="CLOSED" if exit_event else "OPEN",
        exit_date=exit_event["exit_date"].isoformat() if exit_event else None,
        exit_price=exit_event["exit_price"] if exit_event else None,
        exit_reason=exit_event["exit_reason"] if exit_event else None,
        lifecycle_generated_at=metadata.signal_generated_at,
        lifecycle_config_hash=metadata.config_hash,
        lifecycle_git_commit=metadata.git_commit,
        lifecycle_data_snapshot_id=metadata.data_snapshot_id,
    )


def _build_exit_decision(
    execution: dict,
    exit_event: dict,
    lifecycle_run_id: str,
    metadata,
) -> ExitDecision:
    return ExitDecision(
        lifecycle_run_id=lifecycle_run_id,
        execution_run_id=execution["execution_run_id"],
        asof_date=_date_value(execution["asof_date"]).isoformat(),
        symbol=execution["symbol"],
        theme_id=execution["theme_id"],
        setup_type=execution["setup_type"],
        execution_model=execution["execution_model"],
        exit_reason=exit_event["exit_reason"],
        exit_date=exit_event["exit_date"].isoformat(),
        exit_price=exit_event["exit_price"],
        evidence=exit_event["evidence"],
        lifecycle_generated_at=metadata.signal_generated_at,
        lifecycle_config_hash=metadata.config_hash,
        lifecycle_git_commit=metadata.git_commit,
        lifecycle_data_snapshot_id=metadata.data_snapshot_id,
    )


def _baseline_rows(config: SectorScoutConfig, lifecycle_run_id: str, through_date: date, metadata) -> list[dict]:
    rows: list[dict] = []
    for symbol in sorted(BENCHMARK_SYMBOLS):
        prices = _load_prices(config, symbol, through_date)
        for _, row in prices.iterrows():
            rows.append(
                {
                    "lifecycle_run_id": lifecycle_run_id,
                    "symbol": symbol,
                    "price_date": _date_value(row["date"]),
                    "adj_open": float(row["open"]),
                    "adj_high": float(row["high"]),
                    "adj_low": float(row["low"]),
                    "adj_close": float(row["close"]),
                    "adj_volume": int(row["volume"]),
                    "provider": row["provider"],
                    "lifecycle_generated_at": metadata.signal_generated_at,
                    "lifecycle_config_hash": metadata.config_hash,
                    "lifecycle_git_commit": metadata.git_commit,
                    "lifecycle_data_snapshot_id": metadata.data_snapshot_id,
                }
            )
    return rows


def persist_position_lifecycle(
    config: SectorScoutConfig,
    lifecycle_run_id: str,
    execution_run_id: str,
    through_date: date,
    positions: list[SimulatedPosition],
    exits: list[ExitDecision],
    baselines: list[dict],
    metadata,
) -> None:
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            INSERT INTO lifecycle_runs (
                lifecycle_run_id, execution_run_id, through_date,
                lifecycle_generated_at_utc, lifecycle_config_hash,
                lifecycle_git_commit, lifecycle_data_snapshot_id, created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                lifecycle_run_id,
                execution_run_id,
                through_date,
                metadata.signal_generated_at,
                metadata.config_hash,
                metadata.git_commit,
                metadata.data_snapshot_id,
                metadata.created_at,
            ],
        )
        for row in positions:
            connection.execute(
                """
                INSERT INTO simulated_positions (
                    lifecycle_run_id, execution_run_id, asof_date, symbol,
                    theme_id, setup_type, execution_model, entry_date,
                    entry_price, initial_stop_loss, risk_per_share, target_1r,
                    target_2r, target_3r, status, exit_date, exit_price,
                    exit_reason, lifecycle_generated_at_utc,
                    lifecycle_config_hash, lifecycle_git_commit,
                    lifecycle_data_snapshot_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    row.lifecycle_run_id,
                    row.execution_run_id,
                    date.fromisoformat(row.asof_date),
                    row.symbol,
                    row.theme_id,
                    row.setup_type,
                    row.execution_model,
                    date.fromisoformat(row.entry_date),
                    row.entry_price,
                    row.initial_stop_loss,
                    row.risk_per_share,
                    row.target_1r,
                    row.target_2r,
                    row.target_3r,
                    row.status,
                    date.fromisoformat(row.exit_date) if row.exit_date else None,
                    row.exit_price,
                    row.exit_reason,
                    row.lifecycle_generated_at,
                    row.lifecycle_config_hash,
                    row.lifecycle_git_commit,
                    row.lifecycle_data_snapshot_id,
                ],
            )
        for row in exits:
            connection.execute(
                """
                INSERT INTO exit_decisions (
                    lifecycle_run_id, execution_run_id, asof_date, symbol,
                    theme_id, setup_type, execution_model, exit_reason,
                    exit_date, exit_price, evidence_json,
                    lifecycle_generated_at_utc, lifecycle_config_hash,
                    lifecycle_git_commit, lifecycle_data_snapshot_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    row.lifecycle_run_id,
                    row.execution_run_id,
                    date.fromisoformat(row.asof_date),
                    row.symbol,
                    row.theme_id,
                    row.setup_type,
                    row.execution_model,
                    row.exit_reason,
                    date.fromisoformat(row.exit_date),
                    row.exit_price,
                    json.dumps(row.evidence, sort_keys=True),
                    row.lifecycle_generated_at,
                    row.lifecycle_config_hash,
                    row.lifecycle_git_commit,
                    row.lifecycle_data_snapshot_id,
                ],
            )
        for row in baselines:
            connection.execute(
                """
                INSERT INTO baseline_price_series (
                    lifecycle_run_id, symbol, price_date, adj_open, adj_high,
                    adj_low, adj_close, adj_volume, provider,
                    lifecycle_generated_at_utc, lifecycle_config_hash,
                    lifecycle_git_commit, lifecycle_data_snapshot_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    row["lifecycle_run_id"],
                    row["symbol"],
                    row["price_date"],
                    row["adj_open"],
                    row["adj_high"],
                    row["adj_low"],
                    row["adj_close"],
                    row["adj_volume"],
                    row["provider"],
                    row["lifecycle_generated_at"],
                    row["lifecycle_config_hash"],
                    row["lifecycle_git_commit"],
                    row["lifecycle_data_snapshot_id"],
                ],
            )


def generate_position_lifecycle(
    config: SectorScoutConfig,
    execution_run_id: str,
    through_date: date,
    *,
    persist: bool = True,
) -> PositionLifecycleRunResult:
    metadata = build_run_metadata(config, "position-lifecycle", asof_date=through_date)
    lifecycle_run_id = str(uuid4())
    executions = _load_accepted_executions(config, execution_run_id)
    positions: list[SimulatedPosition] = []
    exits: list[ExitDecision] = []
    for execution in executions:
        exit_event = _exit_for_execution(config, execution, through_date)
        positions.append(_build_position(execution, exit_event, lifecycle_run_id, metadata))
        if exit_event is not None:
            exits.append(_build_exit_decision(execution, exit_event, lifecycle_run_id, metadata))
    baselines = _baseline_rows(config, lifecycle_run_id, through_date, metadata)
    if persist:
        persist_position_lifecycle(
            config,
            lifecycle_run_id,
            execution_run_id,
            through_date,
            positions,
            exits,
            baselines,
            metadata,
        )
    return PositionLifecycleRunResult(
        lifecycle_run_id=lifecycle_run_id,
        execution_run_id=execution_run_id,
        through_date=through_date.isoformat(),
        positions=[row.to_dict() for row in positions],
        exit_decisions=[row.to_dict() for row in exits],
        baseline_symbols=sorted(BENCHMARK_SYMBOLS),
        warning=(
            "Phase 5B1 only: position lifecycle and exit-decision records are "
            "scaffolding, not a formal result report or strategy conclusion."
        ),
    )
