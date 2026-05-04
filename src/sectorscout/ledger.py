from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime

from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database
from sectorscout.market_calendar import get_exchange_calendar
from sectorscout.pit import BENCHMARK_SYMBOLS


IN_MEMORY_PRICE_SNAPSHOT_MODE = "in_memory_provider_priority"
PERSISTED_PRICE_SNAPSHOT_MODE = "persisted_price_snapshot"


@dataclass(frozen=True)
class TradeLedgerRow:
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
    status: str
    exit_date: str | None
    exit_price: float | None
    exit_reason: str | None
    holding_days: int | None
    calendar_holding_days: int | None
    trading_holding_sessions: int | None
    gross_r_multiple: float | None
    qa_status: str
    source_signal_snapshot_id: str | None
    execution_config_hash: str | None
    execution_data_snapshot_id: str | None
    lifecycle_generated_at: str
    lifecycle_config_hash: str
    lifecycle_git_commit: str
    lifecycle_data_snapshot_id: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class TradeLedgerQAResult:
    lifecycle_run_id: str
    execution_run_id: str
    trade_ledger_rows: list[dict]
    baseline_qa: dict
    provenance: dict
    warnings: dict
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


def _load_lifecycle_context(config: SectorScoutConfig, lifecycle_run_id: str) -> dict:
    with connect_database(config.database.path) as connection:
        row = connection.execute(
            """
            SELECT
                lr.lifecycle_run_id,
                lr.execution_run_id,
                lr.through_date,
                lr.lifecycle_generated_at_utc,
                lr.lifecycle_config_hash,
                lr.lifecycle_git_commit,
                lr.lifecycle_data_snapshot_id,
                lr.price_snapshot_id,
                lr.lifecycle_input_snapshot_id,
                er.execution_config_hash,
                er.execution_git_commit,
                er.execution_data_snapshot_id,
                er.price_snapshot_id,
                er.source_signal_snapshot_id,
                er.source_signal_config_hash,
                er.source_signal_git_commit,
                er.source_universe_version,
                er.source_theme_version,
                er.mixed_source_signal_metadata
            FROM lifecycle_runs lr
            LEFT JOIN execution_runs er
              ON er.execution_run_id = lr.execution_run_id
            WHERE lr.lifecycle_run_id = ?
            """,
            [lifecycle_run_id],
        ).fetchone()
    if row is None:
        raise ValueError(f"Unknown lifecycle_run_id: {lifecycle_run_id}")
    columns = [
        "lifecycle_run_id",
        "execution_run_id",
        "through_date",
        "lifecycle_generated_at",
        "lifecycle_config_hash",
        "lifecycle_git_commit",
        "lifecycle_data_snapshot_id",
        "lifecycle_price_snapshot_id",
        "lifecycle_input_snapshot_id",
        "execution_config_hash",
        "execution_git_commit",
        "execution_data_snapshot_id",
        "execution_price_snapshot_id",
        "source_signal_snapshot_id",
        "source_signal_config_hash",
        "source_signal_git_commit",
        "source_universe_version",
        "source_theme_version",
        "mixed_source_signal_metadata",
    ]
    return dict(zip(columns, row, strict=True))


def _load_positions(config: SectorScoutConfig, lifecycle_run_id: str) -> list[dict]:
    with connect_database(config.database.path) as connection:
        rows = connection.execute(
            """
            SELECT
                lifecycle_run_id,
                execution_run_id,
                asof_date,
                symbol,
                theme_id,
                setup_type,
                execution_model,
                entry_date,
                entry_price,
                initial_stop_loss,
                risk_per_share,
                status,
                exit_date,
                exit_price,
                exit_reason,
                lifecycle_generated_at_utc,
                lifecycle_config_hash,
                lifecycle_git_commit,
                lifecycle_data_snapshot_id
            FROM simulated_positions
            WHERE lifecycle_run_id = ?
            ORDER BY entry_date, symbol, theme_id, setup_type
            """,
            [lifecycle_run_id],
        ).fetchall()
    columns = [
        "lifecycle_run_id",
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
        "status",
        "exit_date",
        "exit_price",
        "exit_reason",
        "lifecycle_generated_at",
        "lifecycle_config_hash",
        "lifecycle_git_commit",
        "lifecycle_data_snapshot_id",
    ]
    return [dict(zip(columns, row, strict=True)) for row in rows]


def _load_skips(config: SectorScoutConfig, lifecycle_run_id: str) -> list[dict]:
    with connect_database(config.database.path) as connection:
        rows = connection.execute(
            """
            SELECT skip_reason
            FROM lifecycle_skips
            WHERE lifecycle_run_id = ?
            """,
            [lifecycle_run_id],
        ).fetchall()
    return [{"skip_reason": row[0]} for row in rows]


def _load_baseline_rows(config: SectorScoutConfig, lifecycle_run_id: str) -> list[dict]:
    with connect_database(config.database.path) as connection:
        rows = connection.execute(
            """
            SELECT symbol, price_date, provider
            FROM baseline_price_series
            WHERE lifecycle_run_id = ?
            ORDER BY symbol, price_date
            """,
            [lifecycle_run_id],
        ).fetchall()
    columns = ["symbol", "price_date", "provider"]
    return [dict(zip(columns, row, strict=True)) for row in rows]


def _json_list(value: object) -> list[str]:
    if not value:
        return []
    return [str(item) for item in json.loads(str(value))]


def _load_lifecycle_input_qa(config: SectorScoutConfig, lifecycle_run_id: str) -> dict:
    with connect_database(config.database.path) as connection:
        row = connection.execute(
            """
            SELECT
                market_regime_rows,
                theme_score_rows,
                expected_market_regime_sessions_json,
                missing_market_regime_sessions_json,
                expected_theme_score_keys_json,
                missing_theme_score_keys_json,
                market_regime_config_hashes_json,
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
                non_price_input_snapshot_warning,
                non_price_input_mode,
                lifecycle_input_snapshot_id,
                lifecycle_input_snapshot_rows_hash
            FROM lifecycle_input_qa
            WHERE lifecycle_run_id = ?
            """,
            [lifecycle_run_id],
        ).fetchone()
    if row is None:
        return {
            "market_regime_rows": 0,
            "theme_score_rows": 0,
            "expected_market_regime_sessions": [],
            "missing_market_regime_sessions": [],
            "expected_theme_score_keys": [],
            "missing_theme_score_keys": [],
            "market_regime_config_hashes": [],
            "market_regime_git_commits": [],
            "market_regime_data_snapshot_ids": [],
            "market_regime_universe_versions": [],
            "market_regime_theme_versions": [],
            "theme_score_config_hashes": [],
            "theme_score_git_commits": [],
            "theme_score_data_snapshot_ids": [],
            "theme_score_universe_versions": [],
            "theme_score_theme_versions": [],
            "market_regime_source_mismatch_warning": False,
            "theme_score_source_mismatch_warning": False,
            "missing_market_regime_coverage_warning": False,
            "missing_theme_score_coverage_warning": False,
            "missing_lifecycle_input_qa_warning": True,
            "mixed_source_signal_metadata_warning": False,
            "non_price_input_snapshot_warning": True,
            "non_price_input_mode": "missing_lifecycle_input_qa",
            "lifecycle_input_snapshot_id": None,
            "lifecycle_input_snapshot_rows_hash": None,
        }
    columns = [
        "market_regime_rows",
        "theme_score_rows",
        "expected_market_regime_sessions",
        "missing_market_regime_sessions",
        "expected_theme_score_keys",
        "missing_theme_score_keys",
        "market_regime_config_hashes",
        "market_regime_git_commits",
        "market_regime_data_snapshot_ids",
        "market_regime_universe_versions",
        "market_regime_theme_versions",
        "theme_score_config_hashes",
        "theme_score_git_commits",
        "theme_score_data_snapshot_ids",
        "theme_score_universe_versions",
        "theme_score_theme_versions",
        "market_regime_source_mismatch_warning",
        "theme_score_source_mismatch_warning",
        "missing_market_regime_coverage_warning",
        "missing_theme_score_coverage_warning",
        "mixed_source_signal_metadata_warning",
        "non_price_input_snapshot_warning",
        "non_price_input_mode",
        "lifecycle_input_snapshot_id",
        "lifecycle_input_snapshot_rows_hash",
    ]
    payload = dict(zip(columns, row, strict=True))
    for key in (
        "market_regime_config_hashes",
        "market_regime_git_commits",
        "market_regime_data_snapshot_ids",
        "market_regime_universe_versions",
        "market_regime_theme_versions",
        "expected_market_regime_sessions",
        "missing_market_regime_sessions",
        "expected_theme_score_keys",
        "missing_theme_score_keys",
        "theme_score_config_hashes",
        "theme_score_git_commits",
        "theme_score_data_snapshot_ids",
        "theme_score_universe_versions",
        "theme_score_theme_versions",
    ):
        payload[key] = _json_list(payload[key])
    payload["missing_lifecycle_input_qa_warning"] = False
    return payload


def _qa_status(position: dict) -> str:
    if position["risk_per_share"] is None or float(position["risk_per_share"]) <= 0:
        return "INVALID_RISK"
    if position["status"] == "CLOSED":
        if not position["exit_reason"]:
            return "MISSING_EXIT_REASON"
        if not position["exit_date"]:
            return "MISSING_EXIT_DATE"
        if position["exit_price"] is None:
            return "MISSING_EXIT_PRICE"
        return "CLOSED_WITH_COMPLETE_EXIT"
    if position["status"] == "OPEN":
        return "OPEN_POSITION"
    return "UNKNOWN_STATUS"


def _trading_sessions(config: SectorScoutConfig, start: date, end: date) -> int:
    calendar = get_exchange_calendar(config)
    sessions = calendar.sessions_in_range(start.isoformat(), end.isoformat())
    return int(len(sessions))


def _ledger_row(config: SectorScoutConfig, context: dict, position: dict) -> TradeLedgerRow:
    entry_date = _date_value(position["entry_date"])
    exit_date = _date_value(position["exit_date"]) if position["exit_date"] else None
    risk_per_share = float(position["risk_per_share"])
    gross_r_multiple: float | None = None
    if exit_date is not None and position["exit_price"] is not None and risk_per_share > 0:
        gross_r_multiple = (float(position["exit_price"]) - float(position["entry_price"])) / risk_per_share
    calendar_holding_days = (exit_date - entry_date).days if exit_date else None
    trading_holding_sessions = (
        _trading_sessions(config, entry_date, exit_date) if exit_date else None
    )
    return TradeLedgerRow(
        lifecycle_run_id=position["lifecycle_run_id"],
        execution_run_id=position["execution_run_id"],
        asof_date=_date_value(position["asof_date"]).isoformat(),
        symbol=position["symbol"],
        theme_id=position["theme_id"],
        setup_type=position["setup_type"],
        execution_model=position["execution_model"],
        entry_date=entry_date.isoformat(),
        entry_price=float(position["entry_price"]),
        initial_stop_loss=float(position["initial_stop_loss"]),
        risk_per_share=risk_per_share,
        status=position["status"],
        exit_date=exit_date.isoformat() if exit_date else None,
        exit_price=float(position["exit_price"]) if position["exit_price"] is not None else None,
        exit_reason=position["exit_reason"],
        holding_days=calendar_holding_days,
        calendar_holding_days=calendar_holding_days,
        trading_holding_sessions=trading_holding_sessions,
        gross_r_multiple=gross_r_multiple,
        qa_status=_qa_status(position),
        source_signal_snapshot_id=context.get("source_signal_snapshot_id"),
        execution_config_hash=context.get("execution_config_hash"),
        execution_data_snapshot_id=context.get("execution_data_snapshot_id"),
        lifecycle_generated_at=_iso_timestamp(position["lifecycle_generated_at"]),
        lifecycle_config_hash=position["lifecycle_config_hash"],
        lifecycle_git_commit=position["lifecycle_git_commit"],
        lifecycle_data_snapshot_id=position["lifecycle_data_snapshot_id"],
    )


def _expected_baseline_sessions(config: SectorScoutConfig, rows: list[dict], through_date: date) -> list[date]:
    if not rows:
        return []
    start_date = min(_date_value(row["price_date"]) for row in rows)
    calendar = get_exchange_calendar(config)
    sessions = calendar.sessions_in_range(start_date.isoformat(), through_date.isoformat())
    return [session.date() for session in sessions]


def _provider_mix(rows: list[dict]) -> dict[str, int]:
    mix: dict[str, int] = {}
    for row in rows:
        mix[row["provider"]] = mix.get(row["provider"], 0) + 1
    return dict(sorted(mix.items()))


def _baseline_qa(config: SectorScoutConfig, rows: list[dict], through_date: date) -> dict:
    expected = sorted(BENCHMARK_SYMBOLS)
    present = sorted({row["symbol"] for row in rows})
    missing = sorted(set(expected) - set(present))
    expected_sessions = _expected_baseline_sessions(config, rows, through_date)
    expected_session_values = {session.isoformat() for session in expected_sessions}
    by_symbol: dict[str, list[dict]] = {symbol: [] for symbol in expected}
    for row in rows:
        by_symbol.setdefault(row["symbol"], []).append(row)
    per_symbol: dict[str, dict] = {}
    for symbol in expected:
        symbol_rows = by_symbol.get(symbol, [])
        symbol_dates = {_date_value(row["price_date"]).isoformat() for row in symbol_rows}
        symbol_raw_dates = [_date_value(row["price_date"]) for row in symbol_rows]
        per_symbol[symbol] = {
            "expected_sessions": len(expected_sessions),
            "present_sessions": len(symbol_dates),
            "missing_sessions": len(expected_session_values - symbol_dates),
            "coverage_start": min(symbol_raw_dates).isoformat() if symbol_raw_dates else None,
            "coverage_end": max(symbol_raw_dates).isoformat() if symbol_raw_dates else None,
            "provider_mix": _provider_mix(symbol_rows),
        }
    dates = [_date_value(row["price_date"]) for row in rows]
    return {
        "baseline_symbols_expected": expected,
        "baseline_symbols_present": present,
        "baseline_symbols_missing": missing,
        "baseline_rows_count": len(rows),
        "provider_mix": _provider_mix(rows),
        "coverage_start": min(dates).isoformat() if dates else None,
        "coverage_end": max(dates).isoformat() if dates else None,
        "per_symbol": per_symbol,
    }


def _warning_flags(
    context: dict,
    baseline_qa: dict,
    skips: list[dict],
    input_qa: dict,
) -> dict:
    lifecycle_snapshot_id = context.get("lifecycle_price_snapshot_id")
    execution_snapshot_id = context.get("execution_price_snapshot_id")
    component_non_price_warning = any(
        bool(input_qa.get(key))
        for key in (
            "market_regime_source_mismatch_warning",
            "theme_score_source_mismatch_warning",
            "missing_market_regime_coverage_warning",
            "missing_theme_score_coverage_warning",
            "missing_lifecycle_input_qa_warning",
            "mixed_source_signal_metadata_warning",
        )
    )
    return {
        "config_mismatch_warning": (
            context.get("execution_config_hash") is not None
            and context["execution_config_hash"] != context["lifecycle_config_hash"]
        ),
        "snapshot_mismatch_warning": (
            context.get("execution_data_snapshot_id") is not None
            and context["execution_data_snapshot_id"] != context["lifecycle_data_snapshot_id"]
        ),
        "missing_baseline_coverage_warning": bool(baseline_qa["baseline_symbols_missing"]),
        "missing_entry_session_price_warning": any(
            row["skip_reason"] == "MISSING_ENTRY_SESSION_PRICE" for row in skips
        ),
        "price_snapshot_mismatch_warning": (
            lifecycle_snapshot_id is not None
            and execution_snapshot_id is not None
            and lifecycle_snapshot_id != execution_snapshot_id
        ),
        "non_price_input_snapshot_warning": bool(
            input_qa.get("non_price_input_snapshot_warning")
            or component_non_price_warning
        ),
        "market_regime_source_mismatch_warning": bool(
            input_qa.get("market_regime_source_mismatch_warning")
        ),
        "theme_score_source_mismatch_warning": bool(
            input_qa.get("theme_score_source_mismatch_warning")
        ),
        "missing_market_regime_coverage_warning": bool(
            input_qa.get("missing_market_regime_coverage_warning")
        ),
        "missing_theme_score_coverage_warning": bool(
            input_qa.get("missing_theme_score_coverage_warning")
        ),
        "missing_lifecycle_input_qa_warning": bool(
            input_qa.get("missing_lifecycle_input_qa_warning")
        ),
        "mixed_source_signal_metadata_warning": bool(
            input_qa.get("mixed_source_signal_metadata_warning")
        ),
    }


def _price_snapshot_mode(context: dict) -> str:
    if context.get("lifecycle_price_snapshot_id") or context.get("execution_price_snapshot_id"):
        return PERSISTED_PRICE_SNAPSHOT_MODE
    return IN_MEMORY_PRICE_SNAPSHOT_MODE


def _provenance(context: dict, input_qa: dict) -> dict:
    return {
        "lifecycle_run_id": context["lifecycle_run_id"],
        "execution_run_id": context["execution_run_id"],
        "source_signal_snapshot_id": context.get("source_signal_snapshot_id"),
        "source_signal_config_hash": context.get("source_signal_config_hash"),
        "source_signal_git_commit": context.get("source_signal_git_commit"),
        "source_universe_version": context.get("source_universe_version"),
        "source_theme_version": context.get("source_theme_version"),
        "mixed_source_signal_metadata": bool(context.get("mixed_source_signal_metadata")),
        "execution_config_hash": context.get("execution_config_hash"),
        "execution_git_commit": context.get("execution_git_commit"),
        "execution_data_snapshot_id": context.get("execution_data_snapshot_id"),
        "lifecycle_config_hash": context["lifecycle_config_hash"],
        "lifecycle_git_commit": context["lifecycle_git_commit"],
        "lifecycle_data_snapshot_id": context["lifecycle_data_snapshot_id"],
        "lifecycle_price_snapshot_id": context.get("lifecycle_price_snapshot_id"),
        "execution_price_snapshot_id": context.get("execution_price_snapshot_id"),
        "price_snapshot_mode": _price_snapshot_mode(context),
        "non_price_input_mode": input_qa.get(
            "non_price_input_mode",
            "live_table_version_guardrail",
        ),
        "lifecycle_input_snapshot_id": input_qa.get("lifecycle_input_snapshot_id")
        or context.get("lifecycle_input_snapshot_id"),
        "lifecycle_input_snapshot_rows_hash": input_qa.get(
            "lifecycle_input_snapshot_rows_hash"
        ),
        "market_regime_rows": input_qa.get("market_regime_rows", 0),
        "theme_score_rows": input_qa.get("theme_score_rows", 0),
        "expected_market_regime_sessions": input_qa.get(
            "expected_market_regime_sessions",
            [],
        ),
        "missing_market_regime_sessions": input_qa.get(
            "missing_market_regime_sessions",
            [],
        ),
        "expected_theme_score_keys": input_qa.get("expected_theme_score_keys", []),
        "missing_theme_score_keys": input_qa.get("missing_theme_score_keys", []),
        "market_regime_data_snapshot_ids": input_qa.get(
            "market_regime_data_snapshot_ids",
            [],
        ),
        "theme_score_data_snapshot_ids": input_qa.get("theme_score_data_snapshot_ids", []),
        "market_regime_config_hashes": input_qa.get("market_regime_config_hashes", []),
        "theme_score_config_hashes": input_qa.get("theme_score_config_hashes", []),
        "market_regime_theme_versions": input_qa.get("market_regime_theme_versions", []),
        "theme_score_theme_versions": input_qa.get("theme_score_theme_versions", []),
    }


def persist_trade_ledger_qa(
    config: SectorScoutConfig,
    context: dict,
    ledger_rows: list[TradeLedgerRow],
    baseline_qa: dict,
    warnings: dict,
    skips: list[dict],
    input_qa: dict,
) -> None:
    lifecycle_run_id = context["lifecycle_run_id"]
    with connect_database(config.database.path) as connection:
        connection.execute("DELETE FROM trade_ledger WHERE lifecycle_run_id = ?", [lifecycle_run_id])
        connection.execute("DELETE FROM lifecycle_qa WHERE lifecycle_run_id = ?", [lifecycle_run_id])
        for row in ledger_rows:
            connection.execute(
                """
                INSERT INTO trade_ledger (
                    lifecycle_run_id, execution_run_id, asof_date, symbol,
                    theme_id, setup_type, execution_model, entry_date,
                    entry_price, initial_stop_loss, risk_per_share,
                    status, exit_date, exit_price, exit_reason, holding_days,
                    calendar_holding_days, trading_holding_sessions,
                    gross_r_multiple, qa_status, source_signal_snapshot_id,
                    execution_config_hash, execution_data_snapshot_id,
                    lifecycle_generated_at_utc, lifecycle_config_hash,
                    lifecycle_git_commit, lifecycle_data_snapshot_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    row.status,
                    date.fromisoformat(row.exit_date) if row.exit_date else None,
                    row.exit_price,
                    row.exit_reason,
                    row.holding_days,
                    row.calendar_holding_days,
                    row.trading_holding_sessions,
                    row.gross_r_multiple,
                    row.qa_status,
                    row.source_signal_snapshot_id,
                    row.execution_config_hash,
                    row.execution_data_snapshot_id,
                    row.lifecycle_generated_at,
                    row.lifecycle_config_hash,
                    row.lifecycle_git_commit,
                    row.lifecycle_data_snapshot_id,
                ],
            )
        connection.execute(
            """
            INSERT INTO lifecycle_qa (
                lifecycle_run_id, execution_run_id, accepted_execution_count,
                simulated_position_count, closed_position_count, open_position_count,
                skipped_count, missing_price_path_count,
                missing_entry_session_price_count, baseline_rows_count,
                baseline_symbols_expected_json, baseline_symbols_present_json,
                baseline_symbols_missing_json, baseline_provider_mix_json,
                baseline_symbol_qa_json, baseline_coverage_start, baseline_coverage_end,
                config_mismatch_warning, snapshot_mismatch_warning,
                missing_baseline_coverage_warning,
                missing_entry_session_price_warning,
                price_snapshot_mismatch_warning,
                non_price_input_snapshot_warning,
                market_regime_source_mismatch_warning,
                theme_score_source_mismatch_warning,
                missing_market_regime_coverage_warning,
                missing_theme_score_coverage_warning,
                missing_lifecycle_input_qa_warning,
                mixed_source_signal_metadata_warning,
                non_price_input_mode, lifecycle_input_snapshot_id,
                lifecycle_input_snapshot_rows_hash, non_price_input_qa_json,
                price_snapshot_mode,
                lifecycle_generated_at_utc, lifecycle_config_hash,
                lifecycle_git_commit, lifecycle_data_snapshot_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                lifecycle_run_id,
                context["execution_run_id"],
                len(ledger_rows) + len(skips),
                len(ledger_rows),
                sum(1 for row in ledger_rows if row.status == "CLOSED"),
                sum(1 for row in ledger_rows if row.status == "OPEN"),
                len(skips),
                sum(1 for row in skips if row["skip_reason"] == "MISSING_PRICE_PATH"),
                sum(1 for row in skips if row["skip_reason"] == "MISSING_ENTRY_SESSION_PRICE"),
                baseline_qa["baseline_rows_count"],
                json.dumps(baseline_qa["baseline_symbols_expected"], sort_keys=True),
                json.dumps(baseline_qa["baseline_symbols_present"], sort_keys=True),
                json.dumps(baseline_qa["baseline_symbols_missing"], sort_keys=True),
                json.dumps(baseline_qa["provider_mix"], sort_keys=True),
                json.dumps(baseline_qa["per_symbol"], sort_keys=True),
                date.fromisoformat(baseline_qa["coverage_start"]) if baseline_qa["coverage_start"] else None,
                date.fromisoformat(baseline_qa["coverage_end"]) if baseline_qa["coverage_end"] else None,
                warnings["config_mismatch_warning"],
                warnings["snapshot_mismatch_warning"],
                warnings["missing_baseline_coverage_warning"],
                warnings["missing_entry_session_price_warning"],
                warnings["price_snapshot_mismatch_warning"],
                warnings["non_price_input_snapshot_warning"],
                warnings["market_regime_source_mismatch_warning"],
                warnings["theme_score_source_mismatch_warning"],
                warnings["missing_market_regime_coverage_warning"],
                warnings["missing_theme_score_coverage_warning"],
                warnings["missing_lifecycle_input_qa_warning"],
                warnings["mixed_source_signal_metadata_warning"],
                input_qa.get("non_price_input_mode", "live_table_version_guardrail"),
                input_qa.get("lifecycle_input_snapshot_id")
                or context.get("lifecycle_input_snapshot_id"),
                input_qa.get("lifecycle_input_snapshot_rows_hash"),
                json.dumps(input_qa, sort_keys=True),
                _price_snapshot_mode(context),
                context["lifecycle_generated_at"],
                context["lifecycle_config_hash"],
                context["lifecycle_git_commit"],
                context["lifecycle_data_snapshot_id"],
            ],
        )


def generate_trade_ledger_qa(
    config: SectorScoutConfig,
    lifecycle_run_id: str,
    *,
    persist: bool = True,
) -> TradeLedgerQAResult:
    context = _load_lifecycle_context(config, lifecycle_run_id)
    positions = _load_positions(config, lifecycle_run_id)
    skips = _load_skips(config, lifecycle_run_id)
    baseline_rows = _load_baseline_rows(config, lifecycle_run_id)
    input_qa = _load_lifecycle_input_qa(config, lifecycle_run_id)
    ledger_rows = [_ledger_row(config, context, position) for position in positions]
    baseline = _baseline_qa(config, baseline_rows, _date_value(context["through_date"]))
    warnings = _warning_flags(context, baseline, skips, input_qa)
    provenance = _provenance(context, input_qa)
    if persist:
        persist_trade_ledger_qa(
            config,
            context,
            ledger_rows,
            baseline,
            warnings,
            skips,
            input_qa,
        )
    return TradeLedgerQAResult(
        lifecycle_run_id=context["lifecycle_run_id"],
        execution_run_id=context["execution_run_id"],
        trade_ledger_rows=[row.to_dict() for row in ledger_rows],
        baseline_qa=baseline,
        provenance=provenance,
        warnings=warnings,
        warning=(
            "Phase 5B only: trade ledger rows, baseline coverage, and provenance are QA "
            "scaffolding, not a result report or strategy conclusion."
        ),
    )
