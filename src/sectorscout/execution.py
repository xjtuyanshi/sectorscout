from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime

import pandas as pd

from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database
from sectorscout.market_calendar import next_market_session
from sectorscout.metadata import build_run_metadata
from sectorscout.prices import load_price_snapshot


EXECUTION_MODEL = "next_open"


@dataclass(frozen=True)
class ExecutionDecision:
    asof_date: str
    symbol: str
    theme_id: str
    setup_type: str
    execution_model: str
    decision: str
    reject_reason: str | None
    signal_entry_trigger: float | None
    signal_stop_loss: float | None
    next_session_date: str
    chosen_provider: str | None
    actual_entry_price: float | None
    actual_stop_loss: float | None
    risk_per_share: float | None
    target_2r: float | None
    target_3r: float | None
    reward_risk: float | None
    max_entry_extension_pct: float
    max_initial_stop_pct: float
    execution_data_quality_pass: bool
    signal_generated_at: str
    config_hash: str
    git_commit: str
    data_snapshot_id: str
    universe_version: str
    theme_version: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionRunResult:
    asof_date: str
    execution_model: str
    decisions: list[dict]
    performance_metrics: dict
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


def _load_frozen_signal_candidates(config: SectorScoutConfig, asof_date: date) -> list[dict]:
    with connect_database(config.database.path) as connection:
        rows = connection.execute(
            """
            SELECT
                symbol,
                theme_id,
                setup_type,
                entry_trigger,
                stop_loss
            FROM signals
            WHERE asof_date = ?
              AND execution_model = ?
              AND action_category = 'Triggered setup candidate'
              AND setup_data_present = true
              AND market_gate_pass = true
              AND portfolio_risk_pass = true
            ORDER BY symbol, theme_id, setup_type
            """,
            [asof_date, EXECUTION_MODEL],
        ).fetchall()
    columns = ["symbol", "theme_id", "setup_type", "entry_trigger", "stop_loss"]
    return [dict(zip(columns, row, strict=True)) for row in rows]


def _next_open_row(
    config: SectorScoutConfig,
    symbol: str,
    next_session: date,
) -> dict | None:
    prices, _duplicate_count = load_price_snapshot(config, next_session, symbols=[symbol])
    if prices.empty:
        return None
    session_prices = prices[prices["price_date"].apply(_date_value) == next_session]
    if session_prices.empty:
        return None
    row = session_prices.sort_values(["symbol", "price_date"]).iloc[-1]
    if pd.isna(row["adj_open"]):
        return None
    return {
        "provider": row["provider"],
        "open": float(row["adj_open"]),
    }


def _decision_for_candidate(
    config: SectorScoutConfig,
    candidate: dict,
    asof_date: date,
    next_session: date,
    metadata,
) -> ExecutionDecision:
    entry_trigger = candidate["entry_trigger"]
    stop_loss = candidate["stop_loss"]
    next_open = _next_open_row(config, candidate["symbol"], next_session)
    chosen_provider = next_open["provider"] if next_open else None
    actual_entry = next_open["open"] if next_open else None
    reject_reason: str | None = None

    if entry_trigger is None:
        reject_reason = "MISSING_ENTRY_TRIGGER"
    elif stop_loss is None:
        reject_reason = "MISSING_STOP_LOSS"
    elif next_open is None:
        reject_reason = "MISSING_NEXT_OPEN"
    elif actual_entry is not None and actual_entry > float(entry_trigger) * (
        1 + config.execution.max_entry_extension_pct
    ):
        reject_reason = "ENTRY_EXTENSION_TOO_HIGH"
    elif actual_entry is not None and float(stop_loss) >= actual_entry:
        reject_reason = "INVALID_STOP_LOSS"
    else:
        assert actual_entry is not None
        risk_per_share = actual_entry - float(stop_loss)
        if risk_per_share / actual_entry > config.execution.max_initial_stop_pct:
            reject_reason = "INITIAL_STOP_TOO_WIDE"

    decision = "REJECTED" if reject_reason else "FILLED"
    risk_per_share_value: float | None = None
    target_2r: float | None = None
    target_3r: float | None = None
    reward_risk: float | None = None
    if decision == "FILLED":
        assert actual_entry is not None
        assert stop_loss is not None
        risk_per_share_value = actual_entry - float(stop_loss)
        target_2r = actual_entry + 2 * risk_per_share_value
        target_3r = actual_entry + 3 * risk_per_share_value
        reward_risk = (target_2r - actual_entry) / risk_per_share_value

    return ExecutionDecision(
        asof_date=asof_date.isoformat(),
        symbol=candidate["symbol"],
        theme_id=candidate["theme_id"],
        setup_type=candidate["setup_type"],
        execution_model=EXECUTION_MODEL,
        decision=decision,
        reject_reason=reject_reason,
        signal_entry_trigger=entry_trigger,
        signal_stop_loss=stop_loss,
        next_session_date=next_session.isoformat(),
        chosen_provider=chosen_provider,
        actual_entry_price=actual_entry,
        actual_stop_loss=float(stop_loss) if decision == "FILLED" and stop_loss is not None else None,
        risk_per_share=risk_per_share_value,
        target_2r=target_2r,
        target_3r=target_3r,
        reward_risk=reward_risk,
        max_entry_extension_pct=config.execution.max_entry_extension_pct,
        max_initial_stop_pct=config.execution.max_initial_stop_pct,
        execution_data_quality_pass=decision == "FILLED",
        signal_generated_at=metadata.signal_generated_at,
        config_hash=metadata.config_hash,
        git_commit=metadata.git_commit,
        data_snapshot_id=metadata.data_snapshot_id,
        universe_version=metadata.universe_version,
        theme_version=metadata.theme_version,
    )


def persist_execution_decisions(
    config: SectorScoutConfig,
    asof_date: date,
    rows: list[ExecutionDecision],
) -> None:
    with connect_database(config.database.path) as connection:
        connection.execute(
            "DELETE FROM execution_decisions WHERE asof_date = ? AND execution_model = ?",
            [asof_date, EXECUTION_MODEL],
        )
        for row in rows:
            connection.execute(
                """
                INSERT INTO execution_decisions (
                    asof_date, symbol, theme_id, setup_type, execution_model,
                    decision, reject_reason, signal_entry_trigger, signal_stop_loss,
                    next_session_date, chosen_provider, actual_entry_price,
                    actual_stop_loss, risk_per_share, target_2r, target_3r,
                    reward_risk, max_entry_extension_pct, max_initial_stop_pct,
                    execution_data_quality_pass, signal_generated_at_utc,
                    config_hash, git_commit, data_snapshot_id, universe_version,
                    theme_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    asof_date,
                    row.symbol,
                    row.theme_id,
                    row.setup_type,
                    row.execution_model,
                    row.decision,
                    row.reject_reason,
                    row.signal_entry_trigger,
                    row.signal_stop_loss,
                    date.fromisoformat(row.next_session_date),
                    row.chosen_provider,
                    row.actual_entry_price,
                    row.actual_stop_loss,
                    row.risk_per_share,
                    row.target_2r,
                    row.target_3r,
                    row.reward_risk,
                    row.max_entry_extension_pct,
                    row.max_initial_stop_pct,
                    row.execution_data_quality_pass,
                    row.signal_generated_at,
                    row.config_hash,
                    row.git_commit,
                    row.data_snapshot_id,
                    row.universe_version,
                    row.theme_version,
                ],
            )


def generate_execution_decisions(
    config: SectorScoutConfig,
    asof_date: date,
    *,
    persist: bool = True,
) -> ExecutionRunResult:
    metadata = build_run_metadata(config, "execution-decisions", asof_date=asof_date)
    next_session = next_market_session(asof_date, config)
    candidates = _load_frozen_signal_candidates(config, asof_date)
    decisions = [
        _decision_for_candidate(config, candidate, asof_date, next_session, metadata)
        for candidate in candidates
    ]
    if persist:
        persist_execution_decisions(config, asof_date, decisions)
    return ExecutionRunResult(
        asof_date=asof_date.isoformat(),
        execution_model=EXECUTION_MODEL,
        decisions=[row.to_dict() for row in decisions],
        performance_metrics={},
        warning=(
            "Phase 5A only: next-open execution records are not a performance backtest "
            "and do not include CAGR, Sharpe, max drawdown, annual returns, or strategy conclusions."
        ),
    )
