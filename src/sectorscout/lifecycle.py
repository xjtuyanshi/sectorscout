from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from uuid import uuid4

import pandas as pd

from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database
from sectorscout.lifecycle_inputs import (
    MARKET_REGIME_COLUMNS,
    THEME_SCORE_COLUMNS,
    expected_lifecycle_input_keys,
    lifecycle_input_snapshot_rows_hash,
    load_lifecycle_input_snapshot_market_rows,
    load_lifecycle_input_snapshot_theme_rows,
    validate_lifecycle_input_snapshot_usage,
)
from sectorscout.market_calendar import get_exchange_calendar
from sectorscout.metadata import build_run_metadata
from sectorscout.pit import BENCHMARK_SYMBOLS
from sectorscout.prices import (
    load_persisted_price_snapshot,
    load_price_snapshot,
    validate_price_snapshot_usage,
)


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
class LifecycleSkip:
    lifecycle_run_id: str
    execution_run_id: str
    asof_date: str
    symbol: str
    theme_id: str
    setup_type: str
    execution_model: str
    skip_reason: str
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
    price_snapshot_id: str | None
    lifecycle_input_snapshot_id: str | None
    positions: list[dict]
    exit_decisions: list[dict]
    skipped_executions: list[dict]
    qa_summary: dict
    input_qa: dict
    baseline_symbols: list[str]
    warning: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class LifecycleInputQA:
    lifecycle_run_id: str
    execution_run_id: str
    lifecycle_input_snapshot_id: str | None
    lifecycle_input_snapshot_rows_hash: str | None
    market_regime_rows: int
    theme_score_rows: int
    expected_market_regime_sessions: list[str]
    missing_market_regime_sessions: list[str]
    expected_theme_score_keys: list[str]
    missing_theme_score_keys: list[str]
    market_regime_config_hashes: list[str]
    market_regime_git_commits: list[str]
    market_regime_data_snapshot_ids: list[str]
    market_regime_universe_versions: list[str]
    market_regime_theme_versions: list[str]
    theme_score_config_hashes: list[str]
    theme_score_git_commits: list[str]
    theme_score_data_snapshot_ids: list[str]
    theme_score_universe_versions: list[str]
    theme_score_theme_versions: list[str]
    market_regime_source_mismatch_warning: bool
    theme_score_source_mismatch_warning: bool
    missing_market_regime_coverage_warning: bool
    missing_theme_score_coverage_warning: bool
    mixed_source_signal_metadata_warning: bool
    non_price_input_snapshot_warning: bool
    non_price_input_mode: str
    lifecycle_generated_at: str
    lifecycle_config_hash: str
    lifecycle_git_commit: str
    lifecycle_data_snapshot_id: str

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


def _load_execution_price_snapshot_id(
    config: SectorScoutConfig,
    execution_run_id: str,
) -> str | None:
    with connect_database(config.database.path) as connection:
        row = connection.execute(
            """
            SELECT price_snapshot_id
            FROM execution_runs
            WHERE execution_run_id = ?
            """,
            [execution_run_id],
        ).fetchone()
    return row[0] if row else None


def _load_execution_context(config: SectorScoutConfig, execution_run_id: str) -> dict:
    with connect_database(config.database.path) as connection:
        row = connection.execute(
            """
            SELECT
                execution_config_hash,
                execution_git_commit,
                execution_data_snapshot_id,
                source_signal_snapshot_id,
                source_signal_config_hash,
                source_signal_git_commit,
                source_universe_version,
                source_theme_version,
                mixed_source_signal_metadata
            FROM execution_runs
            WHERE execution_run_id = ?
            """,
            [execution_run_id],
        ).fetchone()
    if row is None:
        return {}
    columns = [
        "execution_config_hash",
        "execution_git_commit",
        "execution_data_snapshot_id",
        "source_signal_snapshot_id",
        "source_signal_config_hash",
        "source_signal_git_commit",
        "source_universe_version",
        "source_theme_version",
        "mixed_source_signal_metadata",
    ]
    return dict(zip(columns, row, strict=True))


def _distinct_metadata_values(rows: list[dict], key: str) -> list[str]:
    return sorted({str(row[key]) for row in rows if row.get(key) is not None})


def _metadata_mismatch(values: list[str], expected: str | None) -> bool:
    if expected is None or not values:
        return False
    return any(value != expected for value in values)


def _non_price_input_qa(
    config: SectorScoutConfig,
    lifecycle_run_id: str,
    execution_run_id: str,
    executions: list[dict],
    through_date: date,
    metadata,
    *,
    lifecycle_input_snapshot_id: str | None = None,
    lifecycle_input_snapshot_rows_hash_value: str | None = None,
) -> LifecycleInputQA:
    expected_market_regime_sessions, expected_theme_score_pairs = expected_lifecycle_input_keys(
        config,
        executions,
        through_date,
    )
    start_date = (
        min(expected_market_regime_sessions)
        if expected_market_regime_sessions
        else through_date
    )
    theme_ids = sorted({theme_id for theme_id, _score_date in expected_theme_score_pairs})
    if lifecycle_input_snapshot_id is not None:
        market_rows = load_lifecycle_input_snapshot_market_rows(
            config,
            lifecycle_input_snapshot_id,
            start_date,
            through_date,
        )
        theme_rows = load_lifecycle_input_snapshot_theme_rows(
            config,
            lifecycle_input_snapshot_id,
            theme_ids,
            start_date,
            through_date,
        )
        non_price_mode = "persisted_lifecycle_input_snapshot"
        snapshot_rows_hash = (
            lifecycle_input_snapshot_rows_hash_value
            or lifecycle_input_snapshot_rows_hash(market_rows, theme_rows)
        )
    else:
        with connect_database(config.database.path) as connection:
            market_rows = [
                dict(zip(MARKET_REGIME_COLUMNS, row, strict=True))
                for row in connection.execute(
                    f"""
                    SELECT {", ".join(MARKET_REGIME_COLUMNS)}
                    FROM market_regime
                    WHERE asof_date >= ?
                      AND asof_date <= ?
                    ORDER BY asof_date
                    """,
                    [start_date, through_date],
                ).fetchall()
            ]
            if theme_ids:
                theme_rows = [
                    dict(zip(THEME_SCORE_COLUMNS, row, strict=True))
                    for row in connection.execute(
                        f"""
                        SELECT {", ".join(THEME_SCORE_COLUMNS)}
                        FROM theme_scores
                        WHERE theme_id IN (SELECT unnest(?))
                          AND asof_date >= ?
                          AND asof_date <= ?
                        ORDER BY asof_date, theme_id
                        """,
                        [theme_ids, start_date, through_date],
                    ).fetchall()
                ]
            else:
                theme_rows = []
        non_price_mode = "live_table_version_guardrail"
        snapshot_rows_hash = None

    execution_context = _load_execution_context(config, execution_run_id)
    expected_config_hash = (
        execution_context.get("source_signal_config_hash")
        or execution_context.get("execution_config_hash")
    )
    expected_git_commit = (
        execution_context.get("source_signal_git_commit")
        or execution_context.get("execution_git_commit")
    )
    expected_data_snapshot_id = (
        execution_context.get("source_signal_snapshot_id")
        or execution_context.get("execution_data_snapshot_id")
    )
    expected_universe_version = execution_context.get("source_universe_version")
    expected_theme_version = execution_context.get("source_theme_version")
    mixed_source_warning = bool(execution_context.get("mixed_source_signal_metadata"))

    present_market_sessions = {_date_value(row["asof_date"]) for row in market_rows}
    missing_market_regime_sessions = [
        session.isoformat()
        for session in expected_market_regime_sessions
        if session not in present_market_sessions
    ]
    present_theme_score_pairs = {
        (str(row["theme_id"]), _date_value(row["asof_date"])) for row in theme_rows
    }
    missing_theme_score_keys = [
        f"{theme_id}:{score_date.isoformat()}"
        for theme_id, score_date in expected_theme_score_pairs
        if (theme_id, score_date) not in present_theme_score_pairs
    ]

    market_config_hashes = _distinct_metadata_values(market_rows, "config_hash")
    market_git_commits = _distinct_metadata_values(market_rows, "git_commit")
    market_snapshots = _distinct_metadata_values(market_rows, "data_snapshot_id")
    market_universe_versions = _distinct_metadata_values(market_rows, "universe_version")
    market_theme_versions = _distinct_metadata_values(market_rows, "theme_version")
    theme_config_hashes = _distinct_metadata_values(theme_rows, "config_hash")
    theme_git_commits = _distinct_metadata_values(theme_rows, "git_commit")
    theme_snapshots = _distinct_metadata_values(theme_rows, "data_snapshot_id")
    theme_universe_versions = _distinct_metadata_values(theme_rows, "universe_version")
    theme_theme_versions = _distinct_metadata_values(theme_rows, "theme_version")

    market_warning = any(
        (
            _metadata_mismatch(market_config_hashes, expected_config_hash),
            _metadata_mismatch(market_git_commits, expected_git_commit),
            _metadata_mismatch(market_snapshots, expected_data_snapshot_id),
            _metadata_mismatch(market_universe_versions, expected_universe_version),
            _metadata_mismatch(market_theme_versions, expected_theme_version),
        )
    )
    theme_warning = any(
        (
            _metadata_mismatch(theme_config_hashes, expected_config_hash),
            _metadata_mismatch(theme_git_commits, expected_git_commit),
            _metadata_mismatch(theme_snapshots, expected_data_snapshot_id),
            _metadata_mismatch(theme_universe_versions, expected_universe_version),
            _metadata_mismatch(theme_theme_versions, expected_theme_version),
        )
    )
    missing_market_warning = bool(missing_market_regime_sessions)
    missing_theme_warning = bool(missing_theme_score_keys)
    return LifecycleInputQA(
        lifecycle_run_id=lifecycle_run_id,
        execution_run_id=execution_run_id,
        lifecycle_input_snapshot_id=lifecycle_input_snapshot_id,
        lifecycle_input_snapshot_rows_hash=snapshot_rows_hash,
        market_regime_rows=len(market_rows),
        theme_score_rows=len(theme_rows),
        expected_market_regime_sessions=[
            session.isoformat() for session in expected_market_regime_sessions
        ],
        missing_market_regime_sessions=missing_market_regime_sessions,
        expected_theme_score_keys=[
            f"{theme_id}:{score_date.isoformat()}"
            for theme_id, score_date in expected_theme_score_pairs
        ],
        missing_theme_score_keys=missing_theme_score_keys,
        market_regime_config_hashes=market_config_hashes,
        market_regime_git_commits=market_git_commits,
        market_regime_data_snapshot_ids=market_snapshots,
        market_regime_universe_versions=market_universe_versions,
        market_regime_theme_versions=market_theme_versions,
        theme_score_config_hashes=theme_config_hashes,
        theme_score_git_commits=theme_git_commits,
        theme_score_data_snapshot_ids=theme_snapshots,
        theme_score_universe_versions=theme_universe_versions,
        theme_score_theme_versions=theme_theme_versions,
        market_regime_source_mismatch_warning=market_warning,
        theme_score_source_mismatch_warning=theme_warning,
        missing_market_regime_coverage_warning=missing_market_warning,
        missing_theme_score_coverage_warning=missing_theme_warning,
        mixed_source_signal_metadata_warning=mixed_source_warning,
        non_price_input_snapshot_warning=any(
            (
                market_warning,
                theme_warning,
                missing_market_warning,
                missing_theme_warning,
                mixed_source_warning,
            )
        ),
        non_price_input_mode=non_price_mode,
        lifecycle_generated_at=metadata.signal_generated_at,
        lifecycle_config_hash=metadata.config_hash,
        lifecycle_git_commit=metadata.git_commit,
        lifecycle_data_snapshot_id=metadata.data_snapshot_id,
    )


def _load_prices(
    config: SectorScoutConfig,
    symbol: str,
    through_date: date,
    *,
    price_snapshot_id: str | None = None,
) -> pd.DataFrame:
    if price_snapshot_id is not None:
        prices = load_persisted_price_snapshot(
            config,
            price_snapshot_id,
            symbols=[symbol],
            through_date=through_date,
        )
    else:
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


def _session_dates(config: SectorScoutConfig, start: date, end: date) -> list[date]:
    calendar = get_exchange_calendar(config)
    sessions = calendar.sessions_in_range(pd.Timestamp(start), pd.Timestamp(end))
    return [session.date() for session in sessions]


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
    lifecycle_input_snapshot_id: str | None = None,
) -> dict | None:
    if lifecycle_input_snapshot_id is not None:
        rows = load_lifecycle_input_snapshot_market_rows(
            config,
            lifecycle_input_snapshot_id,
            entry_date,
            through_date,
        )
        risk_off_dates = [
            _date_value(row["asof_date"])
            for row in rows
            if row["risk_state"] == "RISK_OFF"
        ]
    else:
        with connect_database(config.database.path) as connection:
            risk_off_dates = [
                _date_value(row[0])
                for row in connection.execute(
                    """
                    SELECT asof_date
                    FROM market_regime
                    WHERE asof_date >= ?
                      AND asof_date <= ?
                      AND risk_state = 'RISK_OFF'
                    ORDER BY asof_date
                    """,
                    [entry_date, through_date],
                ).fetchall()
            ]
    if not risk_off_dates:
        return None
    event_date = sorted(risk_off_dates)[0]
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
    lifecycle_input_snapshot_id: str | None = None,
) -> dict | None:
    if lifecycle_input_snapshot_id is not None:
        rows = load_lifecycle_input_snapshot_theme_rows(
            config,
            lifecycle_input_snapshot_id,
            [theme_id],
            entry_date,
            through_date,
        )
        score_by_date = {
            _date_value(row["asof_date"]): float(row["theme_score"]) for row in rows
        }
    else:
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
        score_by_date = {
            _date_value(score_date): float(theme_score) for score_date, theme_score in rows
        }
    streak = 0
    for score_date in _session_dates(config, entry_date, through_date):
        theme_score = score_by_date.get(score_date)
        if theme_score is None:
            streak = 0
            continue
        if theme_score < config.lifecycle.theme_failure_score_threshold:
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
                    "theme_score": theme_score,
                    "consecutive_days": streak,
                    "threshold": config.lifecycle.theme_failure_score_threshold,
                    "streak_policy": "strict_consecutive_market_sessions",
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
    *,
    price_snapshot_id: str | None = None,
    lifecycle_input_snapshot_id: str | None = None,
) -> dict | None:
    entry_date = _date_value(execution["entry_date"])
    prices = _load_prices(
        config,
        execution["symbol"],
        through_date,
        price_snapshot_id=price_snapshot_id,
    )
    if prices.empty:
        return None
    stop_loss = float(execution["initial_stop_loss"])
    entry_price = float(execution["entry_price"])
    risk_per_share = float(execution["risk_per_share"])
    events = _stop_events(prices, entry_date, stop_loss)
    for maybe_event in (
        _risk_off_event(
            config,
            prices,
            entry_date,
            through_date,
            lifecycle_input_snapshot_id,
        ),
        _trend_failure_event(prices, entry_date),
        _theme_failure_event(
            config,
            prices,
            execution["theme_id"],
            entry_date,
            through_date,
            lifecycle_input_snapshot_id,
        ),
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


def _skip(
    execution: dict,
    lifecycle_run_id: str,
    metadata,
    reason: str,
    evidence: dict,
) -> LifecycleSkip:
    return LifecycleSkip(
        lifecycle_run_id=lifecycle_run_id,
        execution_run_id=execution["execution_run_id"],
        asof_date=_date_value(execution["asof_date"]).isoformat(),
        symbol=execution["symbol"],
        theme_id=execution["theme_id"],
        setup_type=execution["setup_type"],
        execution_model=execution["execution_model"],
        skip_reason=reason,
        evidence=evidence,
        lifecycle_generated_at=metadata.signal_generated_at,
        lifecycle_config_hash=metadata.config_hash,
        lifecycle_git_commit=metadata.git_commit,
        lifecycle_data_snapshot_id=metadata.data_snapshot_id,
    )


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


def _baseline_rows(
    config: SectorScoutConfig,
    lifecycle_run_id: str,
    through_date: date,
    metadata,
    *,
    price_snapshot_id: str | None = None,
) -> list[dict]:
    rows: list[dict] = []
    for symbol in sorted(BENCHMARK_SYMBOLS):
        prices = _load_prices(config, symbol, through_date, price_snapshot_id=price_snapshot_id)
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
    skips: list[LifecycleSkip],
    baselines: list[dict],
    input_qa: LifecycleInputQA,
    metadata,
    price_snapshot_id: str | None,
    lifecycle_input_snapshot_id: str | None,
) -> None:
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            INSERT INTO lifecycle_runs (
                lifecycle_run_id, execution_run_id, through_date,
                lifecycle_generated_at_utc, lifecycle_config_hash,
                lifecycle_git_commit, lifecycle_data_snapshot_id,
                price_snapshot_id, lifecycle_input_snapshot_id, created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                lifecycle_run_id,
                execution_run_id,
                through_date,
                metadata.signal_generated_at,
                metadata.config_hash,
                metadata.git_commit,
                metadata.data_snapshot_id,
                price_snapshot_id,
                lifecycle_input_snapshot_id,
                metadata.created_at,
            ],
        )
        connection.execute(
            """
            INSERT INTO lifecycle_input_qa (
                lifecycle_run_id, execution_run_id, market_regime_rows,
                theme_score_rows, expected_market_regime_sessions_json,
                missing_market_regime_sessions_json, expected_theme_score_keys_json,
                missing_theme_score_keys_json, market_regime_config_hashes_json,
                market_regime_git_commits_json,
                market_regime_data_snapshot_ids_json,
                market_regime_universe_versions_json,
                market_regime_theme_versions_json,
                theme_score_config_hashes_json,
                theme_score_git_commits_json,
                theme_score_data_snapshot_ids_json,
                theme_score_universe_versions_json,
                theme_score_theme_versions_json,
                market_regime_source_mismatch_warning,
                theme_score_source_mismatch_warning,
                missing_market_regime_coverage_warning,
                missing_theme_score_coverage_warning,
                mixed_source_signal_metadata_warning,
                non_price_input_snapshot_warning, non_price_input_mode,
                lifecycle_input_snapshot_id,
                lifecycle_input_snapshot_rows_hash,
                lifecycle_generated_at_utc, lifecycle_config_hash,
                lifecycle_git_commit, lifecycle_data_snapshot_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                input_qa.lifecycle_run_id,
                input_qa.execution_run_id,
                input_qa.market_regime_rows,
                input_qa.theme_score_rows,
                json.dumps(input_qa.expected_market_regime_sessions, sort_keys=True),
                json.dumps(input_qa.missing_market_regime_sessions, sort_keys=True),
                json.dumps(input_qa.expected_theme_score_keys, sort_keys=True),
                json.dumps(input_qa.missing_theme_score_keys, sort_keys=True),
                json.dumps(input_qa.market_regime_config_hashes, sort_keys=True),
                json.dumps(input_qa.market_regime_git_commits, sort_keys=True),
                json.dumps(input_qa.market_regime_data_snapshot_ids, sort_keys=True),
                json.dumps(input_qa.market_regime_universe_versions, sort_keys=True),
                json.dumps(input_qa.market_regime_theme_versions, sort_keys=True),
                json.dumps(input_qa.theme_score_config_hashes, sort_keys=True),
                json.dumps(input_qa.theme_score_git_commits, sort_keys=True),
                json.dumps(input_qa.theme_score_data_snapshot_ids, sort_keys=True),
                json.dumps(input_qa.theme_score_universe_versions, sort_keys=True),
                json.dumps(input_qa.theme_score_theme_versions, sort_keys=True),
                input_qa.market_regime_source_mismatch_warning,
                input_qa.theme_score_source_mismatch_warning,
                input_qa.missing_market_regime_coverage_warning,
                input_qa.missing_theme_score_coverage_warning,
                input_qa.mixed_source_signal_metadata_warning,
                input_qa.non_price_input_snapshot_warning,
                input_qa.non_price_input_mode,
                input_qa.lifecycle_input_snapshot_id,
                input_qa.lifecycle_input_snapshot_rows_hash,
                input_qa.lifecycle_generated_at,
                input_qa.lifecycle_config_hash,
                input_qa.lifecycle_git_commit,
                input_qa.lifecycle_data_snapshot_id,
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
        for row in skips:
            connection.execute(
                """
                INSERT INTO lifecycle_skips (
                    lifecycle_run_id, execution_run_id, asof_date, symbol,
                    theme_id, setup_type, execution_model, skip_reason,
                    evidence_json, lifecycle_generated_at_utc,
                    lifecycle_config_hash, lifecycle_git_commit,
                    lifecycle_data_snapshot_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    row.lifecycle_run_id,
                    row.execution_run_id,
                    date.fromisoformat(row.asof_date),
                    row.symbol,
                    row.theme_id,
                    row.setup_type,
                    row.execution_model,
                    row.skip_reason,
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
    price_snapshot_id: str | None = None,
    lifecycle_input_snapshot_id: str | None = None,
) -> PositionLifecycleRunResult:
    metadata = build_run_metadata(config, "position-lifecycle", asof_date=through_date)
    lifecycle_run_id = str(uuid4())
    effective_price_snapshot_id = (
        price_snapshot_id
        if price_snapshot_id is not None
        else _load_execution_price_snapshot_id(config, execution_run_id)
    )
    executions = _load_accepted_executions(config, execution_run_id)
    lifecycle_input_snapshot_rows_hash_value = None
    if lifecycle_input_snapshot_id is not None:
        lifecycle_input_snapshot_usage = validate_lifecycle_input_snapshot_usage(
            config,
            lifecycle_input_snapshot_id,
            execution_run_id=execution_run_id,
            through_date=through_date,
            executions=executions,
        )
        lifecycle_input_snapshot_rows_hash_value = (
            lifecycle_input_snapshot_usage.snapshot_rows_hash
        )
    if effective_price_snapshot_id is not None:
        required_entry_dates: dict[str, set[date]] = {}
        for execution in executions:
            entry_date = _date_value(execution["entry_date"])
            if through_date >= entry_date:
                required_entry_dates.setdefault(execution["symbol"], set()).add(entry_date)
        for symbol in BENCHMARK_SYMBOLS:
            required_entry_dates.setdefault(symbol, set()).add(through_date)
        validate_price_snapshot_usage(
            config,
            effective_price_snapshot_id,
            required_symbols={
                execution["symbol"] for execution in executions
            } | set(BENCHMARK_SYMBOLS),
            required_symbol_dates=required_entry_dates,
            through_date=through_date,
            require_rows=bool(executions),
        )
    positions: list[SimulatedPosition] = []
    exits: list[ExitDecision] = []
    skips: list[LifecycleSkip] = []
    for execution in executions:
        entry_date = _date_value(execution["entry_date"])
        if through_date < entry_date:
            skips.append(
                _skip(
                    execution,
                    lifecycle_run_id,
                    metadata,
                    "THROUGH_DATE_BEFORE_ENTRY_DATE",
                    {
                        "entry_date": entry_date.isoformat(),
                        "through_date": through_date.isoformat(),
                    },
                )
            )
            continue
        prices = _load_prices(
            config,
            execution["symbol"],
            through_date,
            price_snapshot_id=effective_price_snapshot_id,
        )
        if prices.empty or prices[prices["date"] >= entry_date].empty:
            skips.append(
                _skip(
                    execution,
                    lifecycle_run_id,
                    metadata,
                    "MISSING_PRICE_PATH",
                    {
                        "entry_date": entry_date.isoformat(),
                        "through_date": through_date.isoformat(),
                    },
                )
            )
            continue
        if prices[prices["date"] == entry_date].empty:
            skips.append(
                _skip(
                    execution,
                    lifecycle_run_id,
                    metadata,
                    "MISSING_ENTRY_SESSION_PRICE",
                    {
                        "entry_date": entry_date.isoformat(),
                        "through_date": through_date.isoformat(),
                    },
                )
            )
            continue
        exit_event = _exit_for_execution(
            config,
            execution,
            through_date,
            price_snapshot_id=effective_price_snapshot_id,
            lifecycle_input_snapshot_id=lifecycle_input_snapshot_id,
        )
        positions.append(_build_position(execution, exit_event, lifecycle_run_id, metadata))
        if exit_event is not None:
            exits.append(_build_exit_decision(execution, exit_event, lifecycle_run_id, metadata))
    baselines = _baseline_rows(
        config,
        lifecycle_run_id,
        through_date,
        metadata,
        price_snapshot_id=effective_price_snapshot_id,
    )
    qa_summary = {
        "accepted_execution_count": len(executions),
        "simulated_position_count": len(positions),
        "closed_position_count": sum(1 for row in positions if row.status == "CLOSED"),
        "open_position_count": sum(1 for row in positions if row.status == "OPEN"),
        "skipped_count": len(skips),
        "missing_price_path_count": sum(
            1 for row in skips if row.skip_reason == "MISSING_PRICE_PATH"
        ),
        "missing_entry_session_price_count": sum(
            1 for row in skips if row.skip_reason == "MISSING_ENTRY_SESSION_PRICE"
        ),
        "baseline_rows_count": len(baselines),
    }
    input_qa = _non_price_input_qa(
        config,
        lifecycle_run_id,
        execution_run_id,
        executions,
        through_date,
        metadata,
        lifecycle_input_snapshot_id=lifecycle_input_snapshot_id,
        lifecycle_input_snapshot_rows_hash_value=lifecycle_input_snapshot_rows_hash_value,
    )
    if persist:
        persist_position_lifecycle(
            config,
            lifecycle_run_id,
            execution_run_id,
            through_date,
            positions,
            exits,
            skips,
            baselines,
            input_qa,
            metadata,
            effective_price_snapshot_id,
            lifecycle_input_snapshot_id,
        )
    return PositionLifecycleRunResult(
        lifecycle_run_id=lifecycle_run_id,
        execution_run_id=execution_run_id,
        through_date=through_date.isoformat(),
        price_snapshot_id=effective_price_snapshot_id,
        lifecycle_input_snapshot_id=lifecycle_input_snapshot_id,
        positions=[row.to_dict() for row in positions],
        exit_decisions=[row.to_dict() for row in exits],
        skipped_executions=[row.to_dict() for row in skips],
        qa_summary=qa_summary,
        input_qa=input_qa.to_dict(),
        baseline_symbols=sorted(BENCHMARK_SYMBOLS),
        warning=(
            "Phase 5B only: position lifecycle, exit-decision records, and input "
            "provenance are scaffolding, not a formal result report or strategy "
            "conclusion."
        ),
    )
