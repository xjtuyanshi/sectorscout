from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from uuid import uuid4

from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database
from sectorscout.market_calendar import get_exchange_calendar
from sectorscout.metadata import build_run_metadata
from sectorscout.source_signals import source_signal_snapshot_source_metadata


ACCEPTED_DECISION = "SIMULATED_NEXT_OPEN_ACCEPTED"

MARKET_REGIME_COLUMNS = [
    "asof_date",
    "spy_stage",
    "qqq_stage",
    "spy_above_50dma",
    "spy_above_200dma",
    "qqq_above_50dma",
    "qqq_above_200dma",
    "pct_universe_above_50dma",
    "pct_universe_above_200dma",
    "pct_universe_stage2",
    "risk_state",
    "signal_generated_at_utc",
    "config_hash",
    "git_commit",
    "data_snapshot_id",
    "universe_version",
    "theme_version",
]

THEME_SCORE_COLUMNS = [
    "asof_date",
    "theme_id",
    "theme_score",
    "technical_relative_strength",
    "breadth",
    "fundamental_acceleration",
    "catalyst_score",
    "risk_valuation_penalty",
    "component_coverage_pct",
    "members_count",
    "raw_members_count",
    "eligible_members_count",
    "excluded_members_count",
    "technical_coverage_pct",
    "theme_fundamental_coverage_pct",
    "members_with_valid_fundamentals",
    "signal_generated_at_utc",
    "config_hash",
    "git_commit",
    "data_snapshot_id",
    "universe_version",
    "theme_version",
]


@dataclass(frozen=True)
class FrozenLifecycleInputSnapshotResult:
    lifecycle_input_snapshot_id: str
    execution_run_id: str
    through_date: str
    expected_market_regime_sessions: list[str]
    expected_theme_score_keys: list[str]
    market_regime_rows: int
    theme_score_rows: int
    market_regime_rows_hash: str
    theme_score_rows_hash: str
    snapshot_rows_hash: str
    config_hash: str
    git_commit: str
    data_snapshot_id: str
    warning: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class LifecycleInputSnapshotUsage:
    lifecycle_input_snapshot_id: str
    execution_run_id: str
    through_date: str
    expected_market_regime_sessions: list[str]
    missing_market_regime_sessions: list[str]
    expected_theme_score_keys: list[str]
    missing_theme_score_keys: list[str]
    market_regime_rows: int
    theme_score_rows: int
    snapshot_rows_hash: str
    non_price_input_mode: str = "persisted_lifecycle_input_snapshot"

    def to_dict(self) -> dict:
        return asdict(self)


class LifecycleInputSnapshotValidationError(ValueError):
    """Raised when a frozen lifecycle input snapshot cannot support a run."""


def _date_value(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "date"):
        return value.date()
    return date.fromisoformat(str(value))


def _iso_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _json_list(items: list[str]) -> str:
    return json.dumps(items, sort_keys=True)


def _row_dicts(rows: list[tuple], columns: list[str]) -> list[dict]:
    return [dict(zip(columns, row, strict=True)) for row in rows]


def _session_dates(config: SectorScoutConfig, start: date, end: date) -> list[date]:
    calendar = get_exchange_calendar(config)
    sessions = calendar.sessions_in_range(start.isoformat(), end.isoformat())
    return [session.date() for session in sessions]


def load_accepted_lifecycle_execution_inputs(
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
                next_session_date
            FROM execution_decisions
            WHERE execution_run_id = ?
              AND decision = ?
              AND execution_data_quality_pass = true
              AND execution_rule_pass = true
              AND risk_rule_pass = true
            ORDER BY asof_date, symbol, theme_id, setup_type, execution_model
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
    ]
    return [dict(zip(columns, row, strict=True)) for row in rows]


def expected_lifecycle_input_keys(
    config: SectorScoutConfig,
    executions: list[dict],
    through_date: date,
) -> tuple[list[date], list[tuple[str, date]]]:
    active_executions = [
        execution
        for execution in executions
        if _date_value(execution["entry_date"]) <= through_date
    ]
    if not active_executions:
        return [], []

    start_date = min(_date_value(execution["entry_date"]) for execution in active_executions)
    market_sessions = _session_dates(config, start_date, through_date)
    theme_score_pairs = sorted(
        {
            (str(execution["theme_id"]), session)
            for execution in active_executions
            for session in _session_dates(
                config,
                _date_value(execution["entry_date"]),
                through_date,
            )
        }
    )
    return market_sessions, theme_score_pairs


def _fetch_market_regime_rows(config: SectorScoutConfig, sessions: list[date]) -> list[dict]:
    if not sessions:
        return []
    with connect_database(config.database.path) as connection:
        rows = connection.execute(
            f"""
            SELECT {", ".join(MARKET_REGIME_COLUMNS)}
            FROM market_regime
            WHERE asof_date IN (SELECT unnest(?))
            ORDER BY asof_date
            """,
            [sessions],
        ).fetchall()
    return _row_dicts(rows, MARKET_REGIME_COLUMNS)


def _fetch_theme_score_rows(
    config: SectorScoutConfig,
    theme_score_pairs: list[tuple[str, date]],
) -> list[dict]:
    if not theme_score_pairs:
        return []
    theme_ids = sorted({theme_id for theme_id, _score_date in theme_score_pairs})
    score_dates = sorted({score_date for _theme_id, score_date in theme_score_pairs})
    allowed = set(theme_score_pairs)
    with connect_database(config.database.path) as connection:
        rows = connection.execute(
            f"""
            SELECT {", ".join(THEME_SCORE_COLUMNS)}
            FROM theme_scores
            WHERE theme_id IN (SELECT unnest(?))
              AND asof_date IN (SELECT unnest(?))
            ORDER BY asof_date, theme_id
            """,
            [theme_ids, score_dates],
        ).fetchall()
    return [
        row
        for row in _row_dicts(rows, THEME_SCORE_COLUMNS)
        if (str(row["theme_id"]), _date_value(row["asof_date"])) in allowed
    ]


def _snapshot_hash(rows: list[dict], sort_keys: list[str]) -> str:
    records = []
    for row in sorted(rows, key=lambda item: tuple(str(item[key]) for key in sort_keys)):
        records.append({key: _iso_value(row[key]) for key in sorted(row)})
    payload = json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def lifecycle_input_snapshot_rows_hash(
    market_rows: list[dict],
    theme_rows: list[dict],
) -> str:
    payload = {
        "market_regime": _snapshot_hash(market_rows, ["asof_date"]),
        "theme_scores": _snapshot_hash(theme_rows, ["asof_date", "theme_id"]),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _metadata_values(rows: list[dict], key: str) -> list[str]:
    return sorted({str(row[key]) for row in rows if row.get(key) is not None})


def _load_execution_context(config: SectorScoutConfig, execution_run_id: str) -> dict:
    with connect_database(config.database.path) as connection:
        row = connection.execute(
            """
            SELECT
                execution_run_id,
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
        "execution_run_id",
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
    context = dict(zip(columns, row, strict=True))
    snapshot_metadata = source_signal_snapshot_source_metadata(
        config,
        context.get("source_signal_snapshot_id"),
    )
    context.update(
        {
            key: value
            for key, value in snapshot_metadata.items()
            if key != "mixed_source_signal_metadata" and value is not None
        }
    )
    context["mixed_source_signal_metadata"] = bool(
        context.get("mixed_source_signal_metadata")
    ) or bool(snapshot_metadata.get("mixed_source_signal_metadata"))
    return context


def _source_metadata_sets(market_rows: list[dict], theme_rows: list[dict]) -> dict[str, list[str]]:
    return {
        "market_regime_config_hashes": _metadata_values(market_rows, "config_hash"),
        "market_regime_git_commits": _metadata_values(market_rows, "git_commit"),
        "market_regime_data_snapshot_ids": _metadata_values(market_rows, "data_snapshot_id"),
        "market_regime_universe_versions": _metadata_values(market_rows, "universe_version"),
        "market_regime_theme_versions": _metadata_values(market_rows, "theme_version"),
        "theme_score_config_hashes": _metadata_values(theme_rows, "config_hash"),
        "theme_score_git_commits": _metadata_values(theme_rows, "git_commit"),
        "theme_score_data_snapshot_ids": _metadata_values(theme_rows, "data_snapshot_id"),
        "theme_score_universe_versions": _metadata_values(theme_rows, "universe_version"),
        "theme_score_theme_versions": _metadata_values(theme_rows, "theme_version"),
    }


def _expected_metadata(context: dict) -> dict[str, str | None]:
    return {
        "config_hash": context.get("source_signal_config_hash")
        or context.get("execution_config_hash"),
        "git_commit": context.get("source_signal_git_commit")
        or context.get("execution_git_commit"),
        "data_snapshot_id": context.get("source_signal_data_snapshot_id")
        or context.get("source_signal_snapshot_id")
        or context.get("execution_data_snapshot_id"),
        "universe_version": context.get("source_universe_version"),
        "theme_version": context.get("source_theme_version"),
    }


def _raise_on_source_conflict(context: dict, market_rows: list[dict], theme_rows: list[dict]) -> None:
    if context.get("mixed_source_signal_metadata"):
        raise LifecycleInputSnapshotValidationError(
            "LIFECYCLE_INPUT_SNAPSHOT_MIXED_SOURCE_SIGNAL_METADATA"
        )
    expected = _expected_metadata(context)
    for row_group_name, rows in (
        ("MARKET_REGIME", market_rows),
        ("THEME_SCORE", theme_rows),
    ):
        for key, expected_value in expected.items():
            if expected_value is None:
                continue
            values = _metadata_values(rows, key)
            if values and any(value != expected_value for value in values):
                raise LifecycleInputSnapshotValidationError(
                    f"LIFECYCLE_INPUT_SNAPSHOT_SOURCE_MISMATCH: {row_group_name} "
                    f"{key} expected={expected_value} actual={','.join(values)}"
                )


def create_frozen_lifecycle_input_snapshot(
    config: SectorScoutConfig,
    execution_run_id: str,
    through_date: date,
    *,
    persist: bool = True,
) -> FrozenLifecycleInputSnapshotResult:
    executions = load_accepted_lifecycle_execution_inputs(config, execution_run_id)
    market_sessions, theme_score_pairs = expected_lifecycle_input_keys(
        config,
        executions,
        through_date,
    )
    market_rows = _fetch_market_regime_rows(config, market_sessions)
    theme_rows = _fetch_theme_score_rows(config, theme_score_pairs)
    source_sets = _source_metadata_sets(market_rows, theme_rows)
    metadata = build_run_metadata(
        config,
        "lifecycle-input-snapshot",
        asof_date=through_date,
    )
    lifecycle_input_snapshot_id = str(uuid4())
    market_hash = _snapshot_hash(market_rows, ["asof_date"])
    theme_hash = _snapshot_hash(theme_rows, ["asof_date", "theme_id"])
    rows_hash = lifecycle_input_snapshot_rows_hash(market_rows, theme_rows)
    expected_market = [session.isoformat() for session in market_sessions]
    expected_theme = [
        f"{theme_id}:{score_date.isoformat()}"
        for theme_id, score_date in theme_score_pairs
    ]

    if persist:
        with connect_database(config.database.path) as connection:
            connection.execute(
                """
                INSERT INTO lifecycle_input_snapshot_runs (
                    lifecycle_input_snapshot_id, execution_run_id, through_date,
                    expected_market_regime_sessions_json,
                    expected_theme_score_keys_json,
                    market_regime_rows, theme_score_rows,
                    market_regime_rows_hash, theme_score_rows_hash,
                    snapshot_rows_hash,
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
                    config_hash, git_commit, data_snapshot_id, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    lifecycle_input_snapshot_id,
                    execution_run_id,
                    through_date,
                    _json_list(expected_market),
                    _json_list(expected_theme),
                    len(market_rows),
                    len(theme_rows),
                    market_hash,
                    theme_hash,
                    rows_hash,
                    _json_list(source_sets["market_regime_config_hashes"]),
                    _json_list(source_sets["market_regime_git_commits"]),
                    _json_list(source_sets["market_regime_data_snapshot_ids"]),
                    _json_list(source_sets["market_regime_universe_versions"]),
                    _json_list(source_sets["market_regime_theme_versions"]),
                    _json_list(source_sets["theme_score_config_hashes"]),
                    _json_list(source_sets["theme_score_git_commits"]),
                    _json_list(source_sets["theme_score_data_snapshot_ids"]),
                    _json_list(source_sets["theme_score_universe_versions"]),
                    _json_list(source_sets["theme_score_theme_versions"]),
                    metadata.config_hash,
                    metadata.git_commit,
                    metadata.data_snapshot_id,
                    metadata.created_at,
                ],
            )
            for row in market_rows:
                connection.execute(
                    f"""
                    INSERT INTO lifecycle_market_regime_snapshot_rows (
                        lifecycle_input_snapshot_id, {", ".join(MARKET_REGIME_COLUMNS)}
                    ) VALUES ({", ".join(["?"] * (len(MARKET_REGIME_COLUMNS) + 1))})
                    """,
                    [lifecycle_input_snapshot_id] + [row[column] for column in MARKET_REGIME_COLUMNS],
                )
            for row in theme_rows:
                connection.execute(
                    f"""
                    INSERT INTO lifecycle_theme_score_snapshot_rows (
                        lifecycle_input_snapshot_id, {", ".join(THEME_SCORE_COLUMNS)}
                    ) VALUES ({", ".join(["?"] * (len(THEME_SCORE_COLUMNS) + 1))})
                    """,
                    [lifecycle_input_snapshot_id] + [row[column] for column in THEME_SCORE_COLUMNS],
                )

    return FrozenLifecycleInputSnapshotResult(
        lifecycle_input_snapshot_id=lifecycle_input_snapshot_id,
        execution_run_id=execution_run_id,
        through_date=through_date.isoformat(),
        expected_market_regime_sessions=expected_market,
        expected_theme_score_keys=expected_theme,
        market_regime_rows=len(market_rows),
        theme_score_rows=len(theme_rows),
        market_regime_rows_hash=market_hash,
        theme_score_rows_hash=theme_hash,
        snapshot_rows_hash=rows_hash,
        config_hash=metadata.config_hash,
        git_commit=metadata.git_commit,
        data_snapshot_id=metadata.data_snapshot_id,
        warning=(
            "Phase 5B only: frozen lifecycle input snapshots are reproducibility "
            "scaffolding for market_regime/theme_scores, not performance evidence."
        ),
    )


def _load_snapshot_run(config: SectorScoutConfig, lifecycle_input_snapshot_id: str) -> dict:
    with connect_database(config.database.path) as connection:
        row = connection.execute(
            """
            SELECT
                lifecycle_input_snapshot_id,
                execution_run_id,
                through_date,
                expected_market_regime_sessions_json,
                expected_theme_score_keys_json,
                market_regime_rows,
                theme_score_rows,
                snapshot_rows_hash
            FROM lifecycle_input_snapshot_runs
            WHERE lifecycle_input_snapshot_id = ?
            """,
            [lifecycle_input_snapshot_id],
        ).fetchone()
    if row is None:
        raise LifecycleInputSnapshotValidationError(
            f"UNKNOWN_LIFECYCLE_INPUT_SNAPSHOT_ID: {lifecycle_input_snapshot_id}"
        )
    columns = [
        "lifecycle_input_snapshot_id",
        "execution_run_id",
        "through_date",
        "expected_market_regime_sessions",
        "expected_theme_score_keys",
        "market_regime_rows",
        "theme_score_rows",
        "snapshot_rows_hash",
    ]
    payload = dict(zip(columns, row, strict=True))
    payload["expected_market_regime_sessions"] = json.loads(
        str(payload["expected_market_regime_sessions"])
    )
    payload["expected_theme_score_keys"] = json.loads(str(payload["expected_theme_score_keys"]))
    return payload


def load_lifecycle_input_snapshot_market_rows(
    config: SectorScoutConfig,
    lifecycle_input_snapshot_id: str,
    start_date: date,
    through_date: date,
) -> list[dict]:
    with connect_database(config.database.path) as connection:
        rows = connection.execute(
            f"""
            SELECT {", ".join(MARKET_REGIME_COLUMNS)}
            FROM lifecycle_market_regime_snapshot_rows
            WHERE lifecycle_input_snapshot_id = ?
              AND asof_date >= ?
              AND asof_date <= ?
            ORDER BY asof_date
            """,
            [lifecycle_input_snapshot_id, start_date, through_date],
        ).fetchall()
    return _row_dicts(rows, MARKET_REGIME_COLUMNS)


def load_lifecycle_input_snapshot_theme_rows(
    config: SectorScoutConfig,
    lifecycle_input_snapshot_id: str,
    theme_ids: list[str],
    start_date: date,
    through_date: date,
) -> list[dict]:
    if not theme_ids:
        return []
    with connect_database(config.database.path) as connection:
        rows = connection.execute(
            f"""
            SELECT {", ".join(THEME_SCORE_COLUMNS)}
            FROM lifecycle_theme_score_snapshot_rows
            WHERE lifecycle_input_snapshot_id = ?
              AND theme_id IN (SELECT unnest(?))
              AND asof_date >= ?
              AND asof_date <= ?
            ORDER BY asof_date, theme_id
            """,
            [lifecycle_input_snapshot_id, theme_ids, start_date, through_date],
        ).fetchall()
    return _row_dicts(rows, THEME_SCORE_COLUMNS)


def _load_all_snapshot_rows(
    config: SectorScoutConfig,
    lifecycle_input_snapshot_id: str,
) -> tuple[list[dict], list[dict]]:
    with connect_database(config.database.path) as connection:
        market_rows = connection.execute(
            f"""
            SELECT {", ".join(MARKET_REGIME_COLUMNS)}
            FROM lifecycle_market_regime_snapshot_rows
            WHERE lifecycle_input_snapshot_id = ?
            ORDER BY asof_date
            """,
            [lifecycle_input_snapshot_id],
        ).fetchall()
        theme_rows = connection.execute(
            f"""
            SELECT {", ".join(THEME_SCORE_COLUMNS)}
            FROM lifecycle_theme_score_snapshot_rows
            WHERE lifecycle_input_snapshot_id = ?
            ORDER BY asof_date, theme_id
            """,
            [lifecycle_input_snapshot_id],
        ).fetchall()
    return _row_dicts(market_rows, MARKET_REGIME_COLUMNS), _row_dicts(
        theme_rows,
        THEME_SCORE_COLUMNS,
    )


def validate_lifecycle_input_snapshot_usage(
    config: SectorScoutConfig,
    lifecycle_input_snapshot_id: str,
    *,
    execution_run_id: str,
    through_date: date,
    executions: list[dict] | None = None,
) -> LifecycleInputSnapshotUsage:
    if lifecycle_input_snapshot_id == "":
        raise LifecycleInputSnapshotValidationError("EMPTY_LIFECYCLE_INPUT_SNAPSHOT_ID")

    run = _load_snapshot_run(config, lifecycle_input_snapshot_id)
    if run["execution_run_id"] != execution_run_id:
        raise LifecycleInputSnapshotValidationError(
            "LIFECYCLE_INPUT_SNAPSHOT_EXECUTION_RUN_MISMATCH: "
            f"{lifecycle_input_snapshot_id}"
        )
    executions = executions or load_accepted_lifecycle_execution_inputs(config, execution_run_id)
    expected_market_sessions, expected_theme_pairs = expected_lifecycle_input_keys(
        config,
        executions,
        through_date,
    )
    market_rows, theme_rows = _load_all_snapshot_rows(config, lifecycle_input_snapshot_id)
    calculated_hash = lifecycle_input_snapshot_rows_hash(market_rows, theme_rows)
    if run["snapshot_rows_hash"] and calculated_hash != run["snapshot_rows_hash"]:
        raise LifecycleInputSnapshotValidationError(
            f"LIFECYCLE_INPUT_SNAPSHOT_HASH_MISMATCH: {lifecycle_input_snapshot_id}"
        )

    present_market_sessions = {_date_value(row["asof_date"]) for row in market_rows}
    missing_market = [
        session.isoformat()
        for session in expected_market_sessions
        if session not in present_market_sessions
    ]
    present_theme_pairs = {
        (str(row["theme_id"]), _date_value(row["asof_date"])) for row in theme_rows
    }
    missing_theme = [
        f"{theme_id}:{score_date.isoformat()}"
        for theme_id, score_date in expected_theme_pairs
        if (theme_id, score_date) not in present_theme_pairs
    ]
    if missing_market:
        raise LifecycleInputSnapshotValidationError(
            "LIFECYCLE_INPUT_SNAPSHOT_MISSING_MARKET_REGIME_SESSIONS: "
            + ",".join(missing_market)
        )
    if missing_theme:
        raise LifecycleInputSnapshotValidationError(
            "LIFECYCLE_INPUT_SNAPSHOT_MISSING_THEME_SCORE_KEYS: "
            + ",".join(missing_theme)
        )

    _raise_on_source_conflict(
        _load_execution_context(config, execution_run_id),
        market_rows,
        theme_rows,
    )

    return LifecycleInputSnapshotUsage(
        lifecycle_input_snapshot_id=lifecycle_input_snapshot_id,
        execution_run_id=execution_run_id,
        through_date=through_date.isoformat(),
        expected_market_regime_sessions=[
            session.isoformat() for session in expected_market_sessions
        ],
        missing_market_regime_sessions=missing_market,
        expected_theme_score_keys=[
            f"{theme_id}:{score_date.isoformat()}"
            for theme_id, score_date in expected_theme_pairs
        ],
        missing_theme_score_keys=missing_theme,
        market_regime_rows=len(market_rows),
        theme_score_rows=len(theme_rows),
        snapshot_rows_hash=run["snapshot_rows_hash"],
    )
