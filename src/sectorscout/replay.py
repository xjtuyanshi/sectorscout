from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime

from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database
from sectorscout.execution import EXECUTION_MODEL, _decision_for_candidate
from sectorscout.market_calendar import next_market_session
from sectorscout.metadata import RunMetadata
from sectorscout.run_manifest import (
    _execution_decision_columns,
    _hash_records,
    execution_decision_rows_hash,
    validate_run_manifest,
)
from sectorscout.source_signals import source_signal_snapshot_candidates


@dataclass(frozen=True)
class FrozenReplayValidationResult:
    run_manifest_id: str
    execution_run_id: str | None
    source_signal_snapshot_id: str | None
    price_snapshot_id: str | None
    persisted_execution_decision_rows: int
    replayed_execution_decision_rows: int
    manifest_execution_decision_rows_hash: str | None
    persisted_execution_decision_rows_hash: str | None
    replayed_execution_decision_rows_hash: str | None
    manifest_validation_status: str
    validation_status: str
    failure_reasons: list[str]
    manifest_validation_errors: list[str]
    manifest_validation_warnings: list[str]
    warning: str

    def to_dict(self) -> dict:
        return asdict(self)


def _iso_timestamp(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _date_value(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "date"):
        return value.date()
    return date.fromisoformat(str(value))


def _load_replay_context(config: SectorScoutConfig, run_manifest_id: str) -> dict:
    with connect_database(config.database.path) as connection:
        row = connection.execute(
            """
            SELECT
                rm.run_manifest_id,
                rm.execution_run_id,
                rm.source_signal_snapshot_id,
                rm.price_snapshot_id,
                rm.execution_decision_rows_hash,
                er.asof_date,
                er.execution_model,
                er.execution_generated_at_utc,
                er.execution_config_hash,
                er.execution_git_commit,
                er.execution_data_snapshot_id,
                er.created_at_utc
            FROM run_manifests rm
            LEFT JOIN execution_runs er
              ON er.execution_run_id = rm.execution_run_id
            WHERE rm.run_manifest_id = ?
            """,
            [run_manifest_id],
        ).fetchone()
    if row is None:
        raise ValueError(f"Unknown run_manifest_id: {run_manifest_id}")
    columns = [
        "run_manifest_id",
        "execution_run_id",
        "source_signal_snapshot_id",
        "price_snapshot_id",
        "manifest_execution_decision_rows_hash",
        "asof_date",
        "execution_model",
        "execution_generated_at_utc",
        "execution_config_hash",
        "execution_git_commit",
        "execution_data_snapshot_id",
        "created_at_utc",
    ]
    return dict(zip(columns, row, strict=True))


def _metadata_from_execution_context(context: dict) -> RunMetadata:
    asof_value = _date_value(context["asof_date"]).isoformat()
    generated_at = _iso_timestamp(context["execution_generated_at_utc"])
    return RunMetadata(
        command="execution-decisions",
        asof_date=asof_value,
        signal_generated_at=generated_at,
        config_hash=str(context["execution_config_hash"]),
        git_commit=str(context["execution_git_commit"]),
        data_snapshot_id=str(context["execution_data_snapshot_id"]),
        provider_versions={},
        universe_version="",
        theme_version="",
        created_at=_iso_timestamp(context["created_at_utc"]),
    )


def _decision_record_hash(
    config: SectorScoutConfig,
    rows,
) -> tuple[int, str]:
    columns = _execution_decision_columns(config)
    aliases = {
        "source_signal_generated_at_utc": "source_signal_generated_at",
        "execution_generated_at_utc": "execution_generated_at",
    }
    records: list[dict] = []
    for row in rows:
        payload = row.to_dict()
        records.append(
            {
                column: payload[aliases.get(column, column)]
                for column in columns
            }
        )
    records.sort(
        key=lambda record: (
            str(record["asof_date"]),
            str(record["symbol"]),
            str(record["theme_id"]),
            str(record["setup_type"]),
            str(record["execution_model"]),
        )
    )
    return len(records), _hash_records(records)


def _warning() -> str:
    return (
        "Phase 5B13 only: frozen replay validation compares deterministic "
        "execution-decision rowset hashes; it is not a result report, benchmark "
        "comparison, or strategy conclusion."
    )


def run_frozen_replay_validation(
    config: SectorScoutConfig,
    run_manifest_id: str,
) -> FrozenReplayValidationResult:
    context = _load_replay_context(config, run_manifest_id)
    manifest = validate_run_manifest(config, run_manifest_id).to_dict()
    execution_run_id = context.get("execution_run_id")
    source_signal_snapshot_id = context.get("source_signal_snapshot_id")
    price_snapshot_id = context.get("price_snapshot_id")
    persisted_rows = 0
    persisted_hash: str | None = None
    if execution_run_id is not None:
        persisted_rows, persisted_hash = execution_decision_rows_hash(
            config,
            str(execution_run_id),
        )

    failure_reasons: list[str] = []
    if manifest["validation_status"] != "PASS":
        failure_reasons.append("MANIFEST_VALIDATION_FAIL")
    if execution_run_id is None:
        failure_reasons.append("MISSING_EXECUTION_RUN")
    if source_signal_snapshot_id is None:
        failure_reasons.append("MISSING_SOURCE_SIGNAL_SNAPSHOT_ID")
    if price_snapshot_id is None:
        failure_reasons.append("MISSING_PRICE_SNAPSHOT_ID")
    if context.get("execution_model") != EXECUTION_MODEL:
        failure_reasons.append("UNSUPPORTED_EXECUTION_MODEL")

    replayed_rows = 0
    replayed_hash: str | None = None
    if not failure_reasons:
        asof_date = _date_value(context["asof_date"])
        next_session = next_market_session(asof_date, config)
        metadata = _metadata_from_execution_context(context)
        candidates = source_signal_snapshot_candidates(
            config,
            str(source_signal_snapshot_id),
        )
        decisions = [
            _decision_for_candidate(
                config,
                candidate,
                asof_date,
                next_session,
                metadata,
                str(execution_run_id),
                str(price_snapshot_id),
            )
            for candidate in candidates
        ]
        replayed_rows, replayed_hash = _decision_record_hash(config, decisions)
        if replayed_rows != persisted_rows:
            failure_reasons.append("EXECUTION_DECISION_REPLAY_ROW_COUNT_MISMATCH")
        if replayed_hash != persisted_hash:
            failure_reasons.append("EXECUTION_DECISION_REPLAY_HASH_MISMATCH")

    return FrozenReplayValidationResult(
        run_manifest_id=run_manifest_id,
        execution_run_id=str(execution_run_id) if execution_run_id is not None else None,
        source_signal_snapshot_id=(
            str(source_signal_snapshot_id)
            if source_signal_snapshot_id is not None
            else None
        ),
        price_snapshot_id=str(price_snapshot_id) if price_snapshot_id is not None else None,
        persisted_execution_decision_rows=persisted_rows,
        replayed_execution_decision_rows=replayed_rows,
        manifest_execution_decision_rows_hash=context.get(
            "manifest_execution_decision_rows_hash"
        ),
        persisted_execution_decision_rows_hash=persisted_hash,
        replayed_execution_decision_rows_hash=replayed_hash,
        manifest_validation_status=manifest["validation_status"],
        validation_status="FAIL" if failure_reasons else "PASS",
        failure_reasons=list(dict.fromkeys(failure_reasons)),
        manifest_validation_errors=manifest["validation_errors"],
        manifest_validation_warnings=manifest["validation_warnings"],
        warning=_warning(),
    )
