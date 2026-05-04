from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from uuid import uuid4

import pandas as pd

from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database
from sectorscout.metadata import build_run_metadata


SOURCE_SIGNAL_COLUMNS = [
    "asof_date",
    "symbol",
    "theme_id",
    "setup_type",
    "state",
    "action_category",
    "actionable",
    "reason",
    "execution_model",
    "entry_trigger",
    "stop_loss",
    "reward_risk",
    "setup_data_present",
    "execution_data_quality_pass",
    "price_snapshot_quality_pass",
    "data_quality_pass",
    "data_quality_reason",
    "market_gate_pass",
    "market_gate_reason",
    "market_regime_risk_state",
    "portfolio_risk_pass",
    "portfolio_risk_reason",
    "signal_generated_at_utc",
    "config_hash",
    "git_commit",
    "data_snapshot_id",
    "universe_version",
    "theme_version",
]

DEFAULT_EXECUTION_MODEL = "next_open"


@dataclass(frozen=True)
class FrozenSourceSignalSnapshotResult:
    source_signal_snapshot_id: str
    asof_date: str
    execution_model: str
    source_signal_rows: int
    source_signal_rows_hash: str
    source_signal_config_hashes: list[str]
    source_signal_git_commits: list[str]
    source_signal_data_snapshot_ids: list[str]
    source_universe_versions: list[str]
    source_theme_versions: list[str]
    config_hash: str
    git_commit: str
    data_snapshot_id: str
    warning: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SourceSignalSnapshotUsage:
    source_signal_snapshot_id: str
    asof_date: str
    execution_model: str
    source_signal_rows: int
    source_signal_rows_hash: str
    required_keys: list[str]
    missing_keys: list[str]
    extra_keys: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


class SourceSignalSnapshotValidationError(ValueError):
    """Raised when a frozen source-signal snapshot cannot support an execution run."""


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


def _json_list(value: object) -> list[str]:
    if not value:
        return []
    return [str(item) for item in json.loads(str(value))]


def _unique_values(rows: pd.DataFrame, column: str) -> list[str]:
    if rows.empty:
        return []
    return sorted({str(value) for value in rows[column].dropna().tolist()})


def _summary(values: list[str]) -> str | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return "mixed:" + ",".join(values)


def _optional_float(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _bool_value(value: object) -> bool | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text == "true":
        return True
    if text == "false":
        return False
    return None


def _key(row: dict) -> str:
    return (
        f"{_date_value(row['asof_date']).isoformat()}:"
        f"{str(row['symbol']).upper()}:"
        f"{row['theme_id']}:{row['setup_type']}:{row['execution_model']}"
    )


def source_signal_snapshot_rows_hash(rows: pd.DataFrame | list[dict]) -> str:
    frame = pd.DataFrame(rows, columns=SOURCE_SIGNAL_COLUMNS)
    records: list[dict] = []
    if not frame.empty:
        sorted_rows = frame.sort_values(["asof_date", "symbol", "theme_id", "setup_type"])
        for _, row in sorted_rows.iterrows():
            records.append(
                {
                    column: (
                        _date_value(row[column]).isoformat()
                        if column == "asof_date"
                        else _iso_value(row[column])
                    )
                    for column in SOURCE_SIGNAL_COLUMNS
                }
            )
    payload = json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_candidate_predicates(
    rows: pd.DataFrame,
    source_signal_snapshot_id: str,
) -> None:
    text_expectations = {
        "state": "TRIGGERED",
        "action_category": "Triggered setup candidate",
        "execution_model": DEFAULT_EXECUTION_MODEL,
    }
    bool_expectations = {
        "actionable": False,
        "setup_data_present": True,
        "price_snapshot_quality_pass": True,
        "execution_data_quality_pass": False,
        "data_quality_pass": False,
        "market_gate_pass": True,
        "portfolio_risk_pass": True,
    }
    for _, row in rows.iterrows():
        payload = row.to_dict()
        row_key = _key(payload)
        for field_name, expected in text_expectations.items():
            if str(payload[field_name]) != expected:
                raise SourceSignalSnapshotValidationError(
                    f"SOURCE_SIGNAL_SNAPSHOT_INELIGIBLE_ROW: "
                    f"{source_signal_snapshot_id} key={row_key} field={field_name}"
                )
        for field_name, expected in bool_expectations.items():
            if _bool_value(payload[field_name]) is not expected:
                raise SourceSignalSnapshotValidationError(
                    f"SOURCE_SIGNAL_SNAPSHOT_INELIGIBLE_ROW: "
                    f"{source_signal_snapshot_id} key={row_key} field={field_name}"
                )


def load_live_source_signal_candidates(
    config: SectorScoutConfig,
    asof_date: date,
    *,
    execution_model: str = DEFAULT_EXECUTION_MODEL,
) -> pd.DataFrame:
    with connect_database(config.database.path) as connection:
        return connection.execute(
            f"""
            SELECT {", ".join(SOURCE_SIGNAL_COLUMNS)}
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
            [asof_date, execution_model],
        ).fetchdf()


def _load_snapshot_run(config: SectorScoutConfig, source_signal_snapshot_id: str) -> dict:
    with connect_database(config.database.path) as connection:
        row = connection.execute(
            """
            SELECT
                source_signal_snapshot_id,
                asof_date,
                execution_model,
                source_signal_rows,
                snapshot_rows_hash,
                source_signal_config_hashes_json,
                source_signal_git_commits_json,
                source_signal_data_snapshot_ids_json,
                source_universe_versions_json,
                source_theme_versions_json
            FROM source_signal_snapshot_runs
            WHERE source_signal_snapshot_id = ?
            """,
            [source_signal_snapshot_id],
        ).fetchone()
    if row is None:
        raise SourceSignalSnapshotValidationError(
            f"UNKNOWN_SOURCE_SIGNAL_SNAPSHOT_ID: {source_signal_snapshot_id}"
        )
    columns = [
        "source_signal_snapshot_id",
        "asof_date",
        "execution_model",
        "source_signal_rows",
        "snapshot_rows_hash",
        "source_signal_config_hashes",
        "source_signal_git_commits",
        "source_signal_data_snapshot_ids",
        "source_universe_versions",
        "source_theme_versions",
    ]
    payload = dict(zip(columns, row, strict=True))
    for key in (
        "source_signal_config_hashes",
        "source_signal_git_commits",
        "source_signal_data_snapshot_ids",
        "source_universe_versions",
        "source_theme_versions",
    ):
        payload[key] = _json_list(payload[key])
    return payload


def source_signal_snapshot_source_metadata(
    config: SectorScoutConfig,
    source_signal_snapshot_id: str | None,
) -> dict:
    if not source_signal_snapshot_id:
        return {}
    try:
        run = _load_snapshot_run(config, source_signal_snapshot_id)
    except SourceSignalSnapshotValidationError:
        return {}
    return {
        "source_signal_config_hash": _summary(run["source_signal_config_hashes"]),
        "source_signal_git_commit": _summary(run["source_signal_git_commits"]),
        "source_signal_data_snapshot_id": _summary(run["source_signal_data_snapshot_ids"]),
        "source_universe_version": _summary(run["source_universe_versions"]),
        "source_theme_version": _summary(run["source_theme_versions"]),
        "mixed_source_signal_metadata": any(
            len(run[key]) > 1
            for key in (
                "source_signal_config_hashes",
                "source_signal_git_commits",
                "source_signal_data_snapshot_ids",
                "source_universe_versions",
                "source_theme_versions",
            )
        ),
    }


def load_source_signal_snapshot_rows(
    config: SectorScoutConfig,
    source_signal_snapshot_id: str,
) -> pd.DataFrame:
    with connect_database(config.database.path) as connection:
        return connection.execute(
            f"""
            SELECT {", ".join(SOURCE_SIGNAL_COLUMNS)}
            FROM source_signal_snapshot_rows
            WHERE source_signal_snapshot_id = ?
            ORDER BY asof_date, symbol, theme_id, setup_type
            """,
            [source_signal_snapshot_id],
        ).fetchdf()


def create_frozen_source_signal_snapshot(
    config: SectorScoutConfig,
    asof_date: date,
    *,
    execution_model: str = DEFAULT_EXECUTION_MODEL,
    persist: bool = True,
) -> FrozenSourceSignalSnapshotResult:
    rows = load_live_source_signal_candidates(
        config,
        asof_date,
        execution_model=execution_model,
    )
    metadata = build_run_metadata(config, "source-signal-snapshot", asof_date=asof_date)
    source_signal_snapshot_id = str(uuid4())
    rows_hash = source_signal_snapshot_rows_hash(rows)
    config_hashes = _unique_values(rows, "config_hash")
    git_commits = _unique_values(rows, "git_commit")
    data_snapshot_ids = _unique_values(rows, "data_snapshot_id")
    universe_versions = _unique_values(rows, "universe_version")
    theme_versions = _unique_values(rows, "theme_version")

    if persist:
        with connect_database(config.database.path) as connection:
            connection.execute(
                """
                INSERT INTO source_signal_snapshot_runs (
                    source_signal_snapshot_id, asof_date, execution_model,
                    source_signal_rows, snapshot_rows_hash,
                    source_signal_config_hashes_json,
                    source_signal_git_commits_json,
                    source_signal_data_snapshot_ids_json,
                    source_universe_versions_json,
                    source_theme_versions_json,
                    config_hash, git_commit, data_snapshot_id, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    source_signal_snapshot_id,
                    asof_date,
                    execution_model,
                    int(len(rows)),
                    rows_hash,
                    json.dumps(config_hashes, sort_keys=True),
                    json.dumps(git_commits, sort_keys=True),
                    json.dumps(data_snapshot_ids, sort_keys=True),
                    json.dumps(universe_versions, sort_keys=True),
                    json.dumps(theme_versions, sort_keys=True),
                    metadata.config_hash,
                    metadata.git_commit,
                    metadata.data_snapshot_id,
                    metadata.created_at,
                ],
            )
            for _, row in rows.iterrows():
                connection.execute(
                    f"""
                    INSERT INTO source_signal_snapshot_rows (
                        source_signal_snapshot_id, {", ".join(SOURCE_SIGNAL_COLUMNS)}
                    ) VALUES ({", ".join(["?"] * (len(SOURCE_SIGNAL_COLUMNS) + 1))})
                    """,
                    [source_signal_snapshot_id]
                    + [row[column] for column in SOURCE_SIGNAL_COLUMNS],
                )

    return FrozenSourceSignalSnapshotResult(
        source_signal_snapshot_id=source_signal_snapshot_id,
        asof_date=asof_date.isoformat(),
        execution_model=execution_model,
        source_signal_rows=int(len(rows)),
        source_signal_rows_hash=rows_hash,
        source_signal_config_hashes=config_hashes,
        source_signal_git_commits=git_commits,
        source_signal_data_snapshot_ids=data_snapshot_ids,
        source_universe_versions=universe_versions,
        source_theme_versions=theme_versions,
        config_hash=metadata.config_hash,
        git_commit=metadata.git_commit,
        data_snapshot_id=metadata.data_snapshot_id,
        warning=(
            "Phase 5B+ only: frozen source-signal snapshots are input "
            "provenance scaffolding and are not result reports or strategy conclusions."
        ),
    )


def source_signal_snapshot_candidates(
    config: SectorScoutConfig,
    source_signal_snapshot_id: str,
) -> list[dict]:
    rows = load_source_signal_snapshot_rows(config, source_signal_snapshot_id)
    candidates: list[dict] = []
    for _, row in rows.iterrows():
        candidates.append(
            {
                "symbol": row["symbol"],
                "theme_id": row["theme_id"],
                "setup_type": row["setup_type"],
                "entry_trigger": _optional_float(row["entry_trigger"]),
                "stop_loss": _optional_float(row["stop_loss"]),
                "source_signal_generated_at": row["signal_generated_at_utc"],
                "source_signal_config_hash": row["config_hash"],
                "source_signal_git_commit": row["git_commit"],
                "source_signal_data_snapshot_id": row["data_snapshot_id"],
                "source_universe_version": row["universe_version"],
                "source_theme_version": row["theme_version"],
            }
        )
    return candidates


def _load_execution_decision_source_rows(
    config: SectorScoutConfig,
    execution_run_id: str,
) -> list[dict]:
    with connect_database(config.database.path) as connection:
        rows = connection.execute(
            """
            SELECT
                asof_date,
                symbol,
                theme_id,
                setup_type,
                execution_model,
                signal_entry_trigger,
                signal_stop_loss,
                source_signal_generated_at_utc,
                source_signal_config_hash,
                source_signal_git_commit,
                source_signal_data_snapshot_id,
                source_universe_version,
                source_theme_version
            FROM execution_decisions
            WHERE execution_run_id = ?
            ORDER BY asof_date, symbol, theme_id, setup_type, execution_model
            """,
            [execution_run_id],
        ).fetchall()
    columns = [
        "asof_date",
        "symbol",
        "theme_id",
        "setup_type",
        "execution_model",
        "signal_entry_trigger",
        "signal_stop_loss",
        "source_signal_generated_at",
        "source_signal_config_hash",
        "source_signal_git_commit",
        "source_signal_data_snapshot_id",
        "source_universe_version",
        "source_theme_version",
    ]
    return [dict(zip(columns, row, strict=True)) for row in rows]


def _execution_run_context(config: SectorScoutConfig, execution_run_id: str) -> dict | None:
    with connect_database(config.database.path) as connection:
        row = connection.execute(
            """
            SELECT asof_date, execution_model
            FROM execution_runs
            WHERE execution_run_id = ?
            """,
            [execution_run_id],
        ).fetchone()
    if row is None:
        return None
    return {"asof_date": _date_value(row[0]), "execution_model": str(row[1])}


def validate_source_signal_snapshot_usage(
    config: SectorScoutConfig,
    source_signal_snapshot_id: str,
    *,
    execution_run_id: str | None = None,
    asof_date: date | None = None,
    execution_model: str | None = None,
    require_rows: bool = True,
) -> SourceSignalSnapshotUsage:
    if source_signal_snapshot_id == "":
        raise SourceSignalSnapshotValidationError("EMPTY_SOURCE_SIGNAL_SNAPSHOT_ID")

    run = _load_snapshot_run(config, source_signal_snapshot_id)
    rows = load_source_signal_snapshot_rows(config, source_signal_snapshot_id)
    calculated_hash = source_signal_snapshot_rows_hash(rows)
    if str(run["snapshot_rows_hash"]) != calculated_hash:
        raise SourceSignalSnapshotValidationError(
            f"SOURCE_SIGNAL_SNAPSHOT_HASH_MISMATCH: {source_signal_snapshot_id}"
        )
    if int(run["source_signal_rows"]) != len(rows):
        raise SourceSignalSnapshotValidationError(
            f"SOURCE_SIGNAL_SNAPSHOT_ROW_COUNT_MISMATCH: {source_signal_snapshot_id}"
        )
    if require_rows and rows.empty:
        raise SourceSignalSnapshotValidationError(
            f"EMPTY_SOURCE_SIGNAL_SNAPSHOT: {source_signal_snapshot_id}"
        )
    if not rows.empty:
        _validate_candidate_predicates(rows, source_signal_snapshot_id)

    effective_asof = asof_date
    effective_model = execution_model
    if execution_run_id is not None:
        context = _execution_run_context(config, execution_run_id)
        if context is not None:
            effective_asof = context["asof_date"]
            effective_model = context["execution_model"]
    if effective_asof is not None and _date_value(run["asof_date"]) != effective_asof:
        raise SourceSignalSnapshotValidationError(
            f"SOURCE_SIGNAL_SNAPSHOT_ASOF_MISMATCH: {source_signal_snapshot_id}"
        )
    if effective_model is not None and str(run["execution_model"]) != effective_model:
        raise SourceSignalSnapshotValidationError(
            f"SOURCE_SIGNAL_SNAPSHOT_EXECUTION_MODEL_MISMATCH: {source_signal_snapshot_id}"
        )

    snapshot_records = rows.to_dict("records")
    snapshot_by_key = {_key(row): row for row in snapshot_records}
    required_keys: list[str] = []
    missing_keys: list[str] = []
    extra_keys: list[str] = []

    if execution_run_id is not None:
        decision_rows = _load_execution_decision_source_rows(config, execution_run_id)
        required_keys = sorted({_key(row) for row in decision_rows})
        snapshot_keys = set(snapshot_by_key)
        missing_keys = sorted(set(required_keys) - snapshot_keys)
        extra_keys = sorted(snapshot_keys - set(required_keys))
        if missing_keys:
            raise SourceSignalSnapshotValidationError(
                f"SOURCE_SIGNAL_SNAPSHOT_MISSING_KEYS: {source_signal_snapshot_id} "
                f"missing={','.join(missing_keys)}"
            )
        if extra_keys:
            raise SourceSignalSnapshotValidationError(
                f"SOURCE_SIGNAL_SNAPSHOT_EXTRA_KEYS: {source_signal_snapshot_id} "
                f"extra={','.join(extra_keys)}"
            )
        for row in decision_rows:
            snapshot_row = snapshot_by_key[_key(row)]
            checks = {
                "entry_trigger": (snapshot_row["entry_trigger"], row["signal_entry_trigger"]),
                "stop_loss": (snapshot_row["stop_loss"], row["signal_stop_loss"]),
                "signal_generated_at_utc": (
                    _iso_value(snapshot_row["signal_generated_at_utc"]),
                    _iso_value(row["source_signal_generated_at"]),
                ),
                "config_hash": (snapshot_row["config_hash"], row["source_signal_config_hash"]),
                "git_commit": (snapshot_row["git_commit"], row["source_signal_git_commit"]),
                "data_snapshot_id": (
                    snapshot_row["data_snapshot_id"],
                    row["source_signal_data_snapshot_id"],
                ),
                "universe_version": (
                    snapshot_row["universe_version"],
                    row["source_universe_version"],
                ),
                "theme_version": (snapshot_row["theme_version"], row["source_theme_version"]),
            }
            for field_name, (snapshot_value, decision_value) in checks.items():
                if str(snapshot_value) != str(decision_value):
                    raise SourceSignalSnapshotValidationError(
                        f"SOURCE_SIGNAL_SNAPSHOT_DECISION_FIELD_MISMATCH: "
                        f"{source_signal_snapshot_id} key={_key(row)} field={field_name}"
                    )

    return SourceSignalSnapshotUsage(
        source_signal_snapshot_id=source_signal_snapshot_id,
        asof_date=_date_value(run["asof_date"]).isoformat(),
        execution_model=str(run["execution_model"]),
        source_signal_rows=int(run["source_signal_rows"]),
        source_signal_rows_hash=calculated_hash,
        required_keys=required_keys,
        missing_keys=missing_keys,
        extra_keys=extra_keys,
    )
