from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from uuid import uuid4

import pandas as pd

from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database
from sectorscout.market_calendar import next_market_session
from sectorscout.metadata import build_run_metadata
from sectorscout.prices import (
    load_persisted_price_snapshot,
    load_price_snapshot,
    validate_price_snapshot_usage,
)


EXECUTION_MODEL = "next_open"


@dataclass(frozen=True)
class ExecutionDecision:
    execution_run_id: str
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
    require_next_open_above_trigger: bool
    max_entry_drop_below_trigger_pct: float
    execution_price_available: bool
    execution_data_quality_pass: bool
    execution_rule_pass: bool
    risk_rule_pass: bool
    source_signal_generated_at: str
    source_signal_config_hash: str
    source_signal_git_commit: str
    source_signal_data_snapshot_id: str
    source_universe_version: str
    source_theme_version: str
    execution_generated_at: str
    execution_config_hash: str
    execution_git_commit: str
    execution_data_snapshot_id: str
    price_snapshot_id: str | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionRunResult:
    execution_run_id: str
    asof_date: str
    execution_model: str
    decisions: list[dict]
    performance_metrics: dict
    price_snapshot_id: str | None
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


def _iso_timestamp(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _load_frozen_signal_candidates(config: SectorScoutConfig, asof_date: date) -> list[dict]:
    with connect_database(config.database.path) as connection:
        rows = connection.execute(
            """
            SELECT
                symbol,
                theme_id,
                setup_type,
                entry_trigger,
                stop_loss,
                signal_generated_at_utc,
                config_hash,
                git_commit,
                data_snapshot_id,
                universe_version,
                theme_version
            FROM signals
            WHERE asof_date = ?
              AND execution_model = ?
              AND state = 'TRIGGERED'
              AND action_category = 'Triggered setup candidate'
              AND actionable = false
              AND setup_data_present = true
              AND price_snapshot_quality_pass = true
              AND execution_data_quality_pass = false
              AND data_quality_pass = false
              AND market_gate_pass = true
              AND portfolio_risk_pass = true
            ORDER BY symbol, theme_id, setup_type
            """,
            [asof_date, EXECUTION_MODEL],
        ).fetchall()
    columns = [
        "symbol",
        "theme_id",
        "setup_type",
        "entry_trigger",
        "stop_loss",
        "source_signal_generated_at",
        "source_signal_config_hash",
        "source_signal_git_commit",
        "source_signal_data_snapshot_id",
        "source_universe_version",
        "source_theme_version",
    ]
    return [dict(zip(columns, row, strict=True)) for row in rows]


def _next_open_row(
    config: SectorScoutConfig,
    symbol: str,
    next_session: date,
    *,
    price_snapshot_id: str | None = None,
) -> dict | None:
    if price_snapshot_id is not None:
        prices = load_persisted_price_snapshot(
            config,
            price_snapshot_id,
            symbols=[symbol],
            through_date=next_session,
        )
    else:
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
    execution_run_id: str,
    price_snapshot_id: str | None,
) -> ExecutionDecision:
    entry_trigger = candidate["entry_trigger"]
    stop_loss = candidate["stop_loss"]
    next_open = _next_open_row(
        config,
        candidate["symbol"],
        next_session,
        price_snapshot_id=price_snapshot_id,
    )
    chosen_provider = next_open["provider"] if next_open else None
    actual_entry = next_open["open"] if next_open else None
    reject_reason: str | None = None
    execution_price_available = next_open is not None
    execution_data_quality_pass = (
        execution_price_available and entry_trigger is not None and stop_loss is not None
    )
    execution_rule_pass = execution_data_quality_pass
    risk_rule_pass = execution_data_quality_pass

    if entry_trigger is None:
        reject_reason = "MISSING_ENTRY_TRIGGER"
        execution_data_quality_pass = False
        execution_rule_pass = False
        risk_rule_pass = False
    elif stop_loss is None:
        reject_reason = "MISSING_STOP_LOSS"
        execution_data_quality_pass = False
        execution_rule_pass = False
        risk_rule_pass = False
    elif next_open is None:
        reject_reason = "MISSING_NEXT_OPEN"
        execution_data_quality_pass = False
        execution_rule_pass = False
        risk_rule_pass = False
    elif actual_entry is not None and actual_entry > float(entry_trigger) * (
        1 + config.execution.max_entry_extension_pct
    ):
        reject_reason = "ENTRY_EXTENSION_TOO_HIGH"
        execution_rule_pass = False
    elif (
        config.execution.require_next_open_above_trigger
        and actual_entry is not None
        and actual_entry
        < float(entry_trigger) * (1 - config.execution.max_entry_drop_below_trigger_pct)
    ):
        reject_reason = "ENTRY_BELOW_TRIGGER"
        execution_rule_pass = False
    elif actual_entry is not None and float(stop_loss) >= actual_entry:
        reject_reason = "INVALID_STOP_LOSS"
        risk_rule_pass = False
    else:
        assert actual_entry is not None
        risk_per_share = actual_entry - float(stop_loss)
        if risk_per_share / actual_entry > config.execution.max_initial_stop_pct:
            reject_reason = "INITIAL_STOP_TOO_WIDE"
            risk_rule_pass = False

    risk_per_share_value: float | None = None
    actual_stop_loss_value: float | None = None
    if actual_entry is not None and stop_loss is not None:
        actual_stop_loss_value = float(stop_loss)
        if actual_stop_loss_value < actual_entry:
            risk_per_share_value = actual_entry - actual_stop_loss_value

    decision = (
        "SIMULATED_NEXT_OPEN_REJECTED"
        if reject_reason
        else "SIMULATED_NEXT_OPEN_ACCEPTED"
    )
    target_2r: float | None = None
    target_3r: float | None = None
    reward_risk: float | None = None
    if decision == "SIMULATED_NEXT_OPEN_ACCEPTED":
        assert actual_entry is not None
        assert risk_per_share_value is not None
        target_2r = actual_entry + 2 * risk_per_share_value
        target_3r = actual_entry + 3 * risk_per_share_value
        reward_risk = (target_2r - actual_entry) / risk_per_share_value

    return ExecutionDecision(
        execution_run_id=execution_run_id,
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
        actual_stop_loss=actual_stop_loss_value,
        risk_per_share=risk_per_share_value,
        target_2r=target_2r,
        target_3r=target_3r,
        reward_risk=reward_risk,
        max_entry_extension_pct=config.execution.max_entry_extension_pct,
        max_initial_stop_pct=config.execution.max_initial_stop_pct,
        require_next_open_above_trigger=config.execution.require_next_open_above_trigger,
        max_entry_drop_below_trigger_pct=config.execution.max_entry_drop_below_trigger_pct,
        execution_price_available=execution_price_available,
        execution_data_quality_pass=execution_data_quality_pass,
        execution_rule_pass=execution_rule_pass,
        risk_rule_pass=risk_rule_pass,
        source_signal_generated_at=_iso_timestamp(candidate["source_signal_generated_at"]),
        source_signal_config_hash=candidate["source_signal_config_hash"],
        source_signal_git_commit=candidate["source_signal_git_commit"],
        source_signal_data_snapshot_id=candidate["source_signal_data_snapshot_id"],
        source_universe_version=candidate["source_universe_version"],
        source_theme_version=candidate["source_theme_version"],
        execution_generated_at=metadata.signal_generated_at,
        execution_config_hash=metadata.config_hash,
        execution_git_commit=metadata.git_commit,
        execution_data_snapshot_id=metadata.data_snapshot_id,
        price_snapshot_id=price_snapshot_id,
    )


def persist_execution_decisions(
    config: SectorScoutConfig,
    asof_date: date,
    metadata,
    execution_run_id: str,
    rows: list[ExecutionDecision],
    price_snapshot_id: str | None,
) -> None:
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            INSERT INTO execution_runs (
                execution_run_id,
                asof_date,
                execution_model,
                execution_generated_at_utc,
                execution_config_hash,
                execution_git_commit,
                execution_data_snapshot_id,
                source_signal_snapshot_id,
                source_signal_config_hash,
                source_signal_git_commit,
                source_universe_version,
                source_theme_version,
                mixed_source_signal_metadata,
                price_snapshot_id,
                created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                execution_run_id,
                asof_date,
                EXECUTION_MODEL,
                metadata.signal_generated_at,
                metadata.config_hash,
                metadata.git_commit,
                metadata.data_snapshot_id,
                _source_snapshot_id(rows),
                _source_field_summary(rows, "source_signal_config_hash"),
                _source_field_summary(rows, "source_signal_git_commit"),
                _source_field_summary(rows, "source_universe_version"),
                _source_field_summary(rows, "source_theme_version"),
                _mixed_source_metadata(rows),
                price_snapshot_id,
                metadata.created_at,
            ],
        )
        for row in rows:
            connection.execute(
                """
                INSERT INTO execution_decisions (
                    execution_run_id, asof_date, symbol, theme_id, setup_type, execution_model,
                    decision, reject_reason, signal_entry_trigger, signal_stop_loss,
                    next_session_date, chosen_provider, actual_entry_price,
                    actual_stop_loss, risk_per_share, target_2r, target_3r,
                    reward_risk, max_entry_extension_pct, max_initial_stop_pct,
                    require_next_open_above_trigger, max_entry_drop_below_trigger_pct,
                    execution_price_available, execution_data_quality_pass,
                    execution_rule_pass, risk_rule_pass,
                    source_signal_generated_at_utc, source_signal_config_hash,
                    source_signal_git_commit, source_signal_data_snapshot_id,
                    source_universe_version, source_theme_version,
                    execution_generated_at_utc, execution_config_hash,
                    execution_git_commit, execution_data_snapshot_id,
                    price_snapshot_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    row.execution_run_id,
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
                    row.require_next_open_above_trigger,
                    row.max_entry_drop_below_trigger_pct,
                    row.execution_price_available,
                    row.execution_data_quality_pass,
                    row.execution_rule_pass,
                    row.risk_rule_pass,
                    row.source_signal_generated_at,
                    row.source_signal_config_hash,
                    row.source_signal_git_commit,
                    row.source_signal_data_snapshot_id,
                    row.source_universe_version,
                    row.source_theme_version,
                    row.execution_generated_at,
                    row.execution_config_hash,
                    row.execution_git_commit,
                    row.execution_data_snapshot_id,
                    row.price_snapshot_id,
                ],
            )


def _source_snapshot_id(rows: list[ExecutionDecision]) -> str | None:
    return _source_field_summary(rows, "source_signal_data_snapshot_id")


def _source_field_summary(rows: list[ExecutionDecision], field_name: str) -> str | None:
    values = sorted({str(getattr(row, field_name)) for row in rows if getattr(row, field_name)})
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return "mixed:" + ",".join(values)


def _mixed_source_metadata(rows: list[ExecutionDecision]) -> bool:
    source_fields = [
        "source_signal_config_hash",
        "source_signal_git_commit",
        "source_signal_data_snapshot_id",
        "source_universe_version",
        "source_theme_version",
    ]
    for field_name in source_fields:
        values = {str(getattr(row, field_name)) for row in rows if getattr(row, field_name)}
        if len(values) > 1:
            return True
    return False


def generate_execution_decisions(
    config: SectorScoutConfig,
    asof_date: date,
    *,
    persist: bool = True,
    price_snapshot_id: str | None = None,
) -> ExecutionRunResult:
    metadata = build_run_metadata(config, "execution-decisions", asof_date=asof_date)
    execution_run_id = str(uuid4())
    next_session = next_market_session(asof_date, config)
    candidates = _load_frozen_signal_candidates(config, asof_date)
    if price_snapshot_id is not None:
        validate_price_snapshot_usage(
            config,
            price_snapshot_id,
            required_symbols={candidate["symbol"] for candidate in candidates},
            through_date=next_session,
            require_rows=bool(candidates),
        )
    decisions = [
        _decision_for_candidate(
            config,
            candidate,
            asof_date,
            next_session,
            metadata,
            execution_run_id,
            price_snapshot_id,
        )
        for candidate in candidates
    ]
    if persist:
        persist_execution_decisions(
            config,
            asof_date,
            metadata,
            execution_run_id,
            decisions,
            price_snapshot_id,
        )
    return ExecutionRunResult(
        execution_run_id=execution_run_id,
        asof_date=asof_date.isoformat(),
        execution_model=EXECUTION_MODEL,
        decisions=[row.to_dict() for row in decisions],
        performance_metrics={},
        price_snapshot_id=price_snapshot_id,
        warning=(
            "Phase 5A only: next-open execution decisions are research simulation records, "
            "not broker fills, trade logs, performance results, or strategy conclusions."
        ),
    )
