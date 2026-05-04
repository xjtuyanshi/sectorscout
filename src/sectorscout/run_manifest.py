from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from uuid import uuid4

import pandas as pd

from sectorscout.config import SectorScoutConfig
from sectorscout.db import SCHEMA_VERSION, connect_database
from sectorscout.lifecycle_inputs import (
    MARKET_REGIME_COLUMNS,
    THEME_SCORE_COLUMNS,
    LifecycleInputSnapshotValidationError,
    lifecycle_input_snapshot_rows_hash,
    validate_lifecycle_input_snapshot_usage,
)
from sectorscout.metadata import build_run_metadata
from sectorscout.pit import BENCHMARK_SYMBOLS
from sectorscout.prices import (
    PRICE_SNAPSHOT_COLUMNS,
    snapshot_rows_hash,
    validate_price_snapshot_usage,
)
from sectorscout.source_signals import validate_source_signal_snapshot_usage


@dataclass(frozen=True)
class RunManifestResult:
    run_manifest_id: str
    lifecycle_run_id: str
    execution_run_id: str
    source_signal_snapshot_id: str | None
    source_signal_rows_hash: str | None
    source_signal_config_hash: str | None
    source_signal_git_commit: str | None
    source_universe_version: str | None
    source_theme_version: str | None
    execution_model: str
    execution_decision_rows: int
    execution_decision_rows_hash: str
    price_snapshot_id: str | None
    price_snapshot_rows_hash: str | None
    lifecycle_input_snapshot_id: str | None
    lifecycle_input_snapshot_rows_hash: str | None
    schema_version: int
    config_hash: str
    git_commit: str
    data_snapshot_id: str
    validation_status: str
    validation_errors: list[str]
    validation_warnings: list[str]
    warning: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RunManifestValidationResult:
    run_manifest_id: str
    lifecycle_run_id: str
    execution_run_id: str
    validation_status: str
    validation_errors: list[str]
    validation_warnings: list[str]
    execution_decision_rows_hash: str
    source_signal_rows_hash: str | None
    price_snapshot_rows_hash: str | None
    lifecycle_input_snapshot_rows_hash: str | None
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


def _iso_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _hash_records(records: list[dict]) -> str:
    normalized = [{key: _iso_value(row[key]) for key in sorted(row)} for row in records]
    payload = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_lifecycle_context(config: SectorScoutConfig, lifecycle_run_id: str) -> dict:
    with connect_database(config.database.path) as connection:
        row = connection.execute(
            """
            SELECT
                lr.lifecycle_run_id,
                lr.execution_run_id,
                lr.through_date,
                lr.lifecycle_config_hash,
                lr.lifecycle_git_commit,
                lr.lifecycle_data_snapshot_id,
                lr.price_snapshot_id,
                lr.lifecycle_input_snapshot_id,
                er.execution_model,
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
        "lifecycle_config_hash",
        "lifecycle_git_commit",
        "lifecycle_data_snapshot_id",
        "lifecycle_price_snapshot_id",
        "lifecycle_input_snapshot_id",
        "execution_model",
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


def _execution_decision_columns(config: SectorScoutConfig) -> list[str]:
    with connect_database(config.database.path) as connection:
        rows = connection.execute("PRAGMA table_info('execution_decisions')").fetchall()
    return [str(row[1]) for row in sorted(rows, key=lambda item: int(item[0]))]


def execution_decision_rows_hash(
    config: SectorScoutConfig,
    execution_run_id: str,
) -> tuple[int, str]:
    columns = _execution_decision_columns(config)
    with connect_database(config.database.path) as connection:
        rows = connection.execute(
            f"""
            SELECT {", ".join(columns)}
            FROM execution_decisions
            WHERE execution_run_id = ?
            ORDER BY asof_date, symbol, theme_id, setup_type, execution_model
            """,
            [execution_run_id],
        ).fetchall()
    records = [dict(zip(columns, row, strict=True)) for row in rows]
    return len(records), _hash_records(records)


def _accepted_execution_price_requirements(
    config: SectorScoutConfig,
    execution_run_id: str,
    through_date: date,
) -> tuple[set[str], dict[str, set[date]]]:
    with connect_database(config.database.path) as connection:
        rows = connection.execute(
            """
            SELECT symbol, next_session_date
            FROM execution_decisions
            WHERE execution_run_id = ?
              AND decision = 'SIMULATED_NEXT_OPEN_ACCEPTED'
              AND execution_data_quality_pass = true
              AND execution_rule_pass = true
              AND risk_rule_pass = true
            ORDER BY symbol, next_session_date
            """,
            [execution_run_id],
        ).fetchall()
    required_symbols = {str(symbol).upper() for symbol, _entry_date in rows}
    required_symbol_dates: dict[str, set[date]] = {}
    for symbol, entry_date in rows:
        required_symbol_dates.setdefault(str(symbol).upper(), set()).add(
            _date_value(entry_date)
        )
    for symbol in BENCHMARK_SYMBOLS:
        required_symbols.add(symbol)
        required_symbol_dates.setdefault(symbol, set()).add(through_date)
    return required_symbols, required_symbol_dates


def _price_snapshot_hash(
    config: SectorScoutConfig,
    price_snapshot_id: str,
) -> tuple[str | None, str, list[str]]:
    warnings: list[str] = []
    with connect_database(config.database.path) as connection:
        run = connection.execute(
            """
            SELECT snapshot_rows_hash
            FROM price_snapshot_runs
            WHERE price_snapshot_id = ?
            """,
            [price_snapshot_id],
        ).fetchone()
        if run is None:
            raise ValueError(f"UNKNOWN_PRICE_SNAPSHOT_ID: {price_snapshot_id}")
        rows = connection.execute(
            f"""
            SELECT {", ".join(PRICE_SNAPSHOT_COLUMNS)}
            FROM price_snapshot_rows
            WHERE price_snapshot_id = ?
            ORDER BY symbol, price_date, provider
            """,
            [price_snapshot_id],
        ).fetchdf()
    calculated_hash = snapshot_rows_hash(pd.DataFrame(rows, columns=PRICE_SNAPSHOT_COLUMNS))
    stored_hash = run[0]
    if stored_hash is None:
        warnings.append(f"PRICE_SNAPSHOT_RUN_MISSING_STORED_HASH: {price_snapshot_id}")
    elif str(stored_hash) != calculated_hash:
        raise ValueError(f"PRICE_SNAPSHOT_HASH_MISMATCH: {price_snapshot_id}")
    return stored_hash, calculated_hash, warnings


def _row_dicts(rows: list[tuple], columns: list[str]) -> list[dict]:
    return [dict(zip(columns, row, strict=True)) for row in rows]


def _lifecycle_input_snapshot_hash(
    config: SectorScoutConfig,
    lifecycle_input_snapshot_id: str,
) -> tuple[str, str]:
    with connect_database(config.database.path) as connection:
        run = connection.execute(
            """
            SELECT snapshot_rows_hash
            FROM lifecycle_input_snapshot_runs
            WHERE lifecycle_input_snapshot_id = ?
            """,
            [lifecycle_input_snapshot_id],
        ).fetchone()
        if run is None:
            raise ValueError(
                f"UNKNOWN_LIFECYCLE_INPUT_SNAPSHOT_ID: {lifecycle_input_snapshot_id}"
            )
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
    calculated_hash = lifecycle_input_snapshot_rows_hash(
        _row_dicts(market_rows, MARKET_REGIME_COLUMNS),
        _row_dicts(theme_rows, THEME_SCORE_COLUMNS),
    )
    stored_hash = str(run[0])
    if stored_hash != calculated_hash:
        raise ValueError(
            f"LIFECYCLE_INPUT_SNAPSHOT_HASH_MISMATCH: {lifecycle_input_snapshot_id}"
        )
    return stored_hash, calculated_hash


def _effective_price_snapshot_id(context: dict) -> tuple[str | None, list[str]]:
    errors: list[str] = []
    lifecycle_id = context.get("lifecycle_price_snapshot_id")
    execution_id = context.get("execution_price_snapshot_id")
    if lifecycle_id and execution_id and lifecycle_id != execution_id:
        errors.append(
            f"PRICE_SNAPSHOT_ID_MISMATCH: lifecycle={lifecycle_id} execution={execution_id}"
        )
    return lifecycle_id or execution_id, errors


def _manifest_warning() -> str:
    return (
        "Phase 5B+ only: run manifests validate reproducibility inputs and "
        "rowset hashes; they are not result reports or strategy conclusions."
    )


def _validation_status(errors: list[str]) -> str:
    return "FAIL" if errors else "PASS"


def _build_manifest_payload(
    config: SectorScoutConfig,
    lifecycle_run_id: str,
) -> tuple[dict, list[str], list[str]]:
    context = _load_lifecycle_context(config, lifecycle_run_id)
    errors: list[str] = []
    warnings: list[str] = []
    if context.get("execution_model") is None:
        errors.append("MISSING_EXECUTION_RUN")

    decision_count, decision_hash = execution_decision_rows_hash(
        config,
        context["execution_run_id"],
    )
    if decision_count == 0:
        errors.append("MISSING_EXECUTION_DECISIONS")

    source_signal_snapshot_id = context.get("source_signal_snapshot_id")
    source_signal_rows_hash: str | None = None
    if source_signal_snapshot_id is None:
        errors.append("MISSING_SOURCE_SIGNAL_SNAPSHOT_ID")
    else:
        try:
            source_usage = validate_source_signal_snapshot_usage(
                config,
                str(source_signal_snapshot_id),
                execution_run_id=context["execution_run_id"],
                require_rows=bool(decision_count),
            )
            source_signal_rows_hash = source_usage.source_signal_rows_hash
        except ValueError as exc:
            errors.append(str(exc))

    price_snapshot_id, price_errors = _effective_price_snapshot_id(context)
    errors.extend(price_errors)
    price_rows_hash: str | None = None
    if price_snapshot_id is None:
        errors.append("MISSING_PRICE_SNAPSHOT_ID")
    else:
        try:
            required_symbols, required_symbol_dates = _accepted_execution_price_requirements(
                config,
                context["execution_run_id"],
                _date_value(context["through_date"]),
            )
            validate_price_snapshot_usage(
                config,
                price_snapshot_id,
                required_symbols=required_symbols,
                required_symbol_dates=required_symbol_dates,
                through_date=_date_value(context["through_date"]),
                require_rows=bool(decision_count),
            )
            stored_price_hash, calculated_price_hash, hash_warnings = _price_snapshot_hash(
                config,
                price_snapshot_id,
            )
            warnings.extend(hash_warnings)
            price_rows_hash = stored_price_hash or calculated_price_hash
        except ValueError as exc:
            errors.append(str(exc))

    lifecycle_input_snapshot_id = context.get("lifecycle_input_snapshot_id")
    lifecycle_input_rows_hash: str | None = None
    if lifecycle_input_snapshot_id is None:
        errors.append("MISSING_LIFECYCLE_INPUT_SNAPSHOT_ID")
    else:
        try:
            validate_lifecycle_input_snapshot_usage(
                config,
                lifecycle_input_snapshot_id,
                execution_run_id=context["execution_run_id"],
                through_date=_date_value(context["through_date"]),
            )
            stored_input_hash, calculated_input_hash = _lifecycle_input_snapshot_hash(
                config,
                lifecycle_input_snapshot_id,
            )
            lifecycle_input_rows_hash = stored_input_hash or calculated_input_hash
        except (LifecycleInputSnapshotValidationError, ValueError) as exc:
            errors.append(str(exc))

    if bool(context.get("mixed_source_signal_metadata")):
        errors.append("MIXED_SOURCE_SIGNAL_METADATA")

    metadata = build_run_metadata(
        config,
        "run-manifest",
        asof_date=_date_value(context["through_date"]),
    )
    payload = {
        "lifecycle_run_id": context["lifecycle_run_id"],
        "execution_run_id": context["execution_run_id"],
        "source_signal_snapshot_id": source_signal_snapshot_id,
        "source_signal_rows_hash": source_signal_rows_hash,
        "source_signal_config_hash": context.get("source_signal_config_hash"),
        "source_signal_git_commit": context.get("source_signal_git_commit"),
        "source_universe_version": context.get("source_universe_version"),
        "source_theme_version": context.get("source_theme_version"),
        "execution_model": context.get("execution_model") or "unknown",
        "execution_decision_rows": decision_count,
        "execution_decision_rows_hash": decision_hash,
        "price_snapshot_id": price_snapshot_id,
        "price_snapshot_rows_hash": price_rows_hash,
        "lifecycle_input_snapshot_id": lifecycle_input_snapshot_id,
        "lifecycle_input_snapshot_rows_hash": lifecycle_input_rows_hash,
        "schema_version": SCHEMA_VERSION,
        "config_hash": metadata.config_hash,
        "git_commit": metadata.git_commit,
        "data_snapshot_id": metadata.data_snapshot_id,
        "created_at_utc": metadata.created_at,
    }
    return payload, _dedupe(errors), _dedupe(warnings)


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def persist_run_manifest(config: SectorScoutConfig, result: RunManifestResult) -> None:
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            INSERT INTO run_manifests (
                run_manifest_id, lifecycle_run_id, execution_run_id,
                source_signal_snapshot_id, source_signal_rows_hash,
                source_signal_config_hash,
                source_signal_git_commit, source_universe_version,
                source_theme_version, execution_model, execution_decision_rows,
                execution_decision_rows_hash, price_snapshot_id,
                price_snapshot_rows_hash, lifecycle_input_snapshot_id,
                lifecycle_input_snapshot_rows_hash, schema_version, config_hash,
                git_commit, data_snapshot_id, validation_status,
                validation_errors_json, validation_warnings_json, created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                result.run_manifest_id,
                result.lifecycle_run_id,
                result.execution_run_id,
                result.source_signal_snapshot_id,
                result.source_signal_rows_hash,
                result.source_signal_config_hash,
                result.source_signal_git_commit,
                result.source_universe_version,
                result.source_theme_version,
                result.execution_model,
                result.execution_decision_rows,
                result.execution_decision_rows_hash,
                result.price_snapshot_id,
                result.price_snapshot_rows_hash,
                result.lifecycle_input_snapshot_id,
                result.lifecycle_input_snapshot_rows_hash,
                result.schema_version,
                result.config_hash,
                result.git_commit,
                result.data_snapshot_id,
                result.validation_status,
                json.dumps(result.validation_errors, sort_keys=True),
                json.dumps(result.validation_warnings, sort_keys=True),
                datetime.now(timezone.utc),
            ],
        )


def generate_run_manifest(
    config: SectorScoutConfig,
    lifecycle_run_id: str,
    *,
    persist: bool = True,
) -> RunManifestResult:
    payload, errors, warnings = _build_manifest_payload(config, lifecycle_run_id)
    result = RunManifestResult(
        run_manifest_id=str(uuid4()),
        validation_status=_validation_status(errors),
        validation_errors=errors,
        validation_warnings=warnings,
        warning=_manifest_warning(),
        **{key: value for key, value in payload.items() if key != "created_at_utc"},
    )
    if persist:
        persist_run_manifest(config, result)
    return result


def _load_manifest(config: SectorScoutConfig, run_manifest_id: str) -> dict:
    with connect_database(config.database.path) as connection:
        row = connection.execute(
            """
            SELECT
                run_manifest_id,
                lifecycle_run_id,
                execution_run_id,
                execution_decision_rows_hash,
                source_signal_snapshot_id,
                source_signal_rows_hash,
                price_snapshot_id,
                price_snapshot_rows_hash,
                lifecycle_input_snapshot_id,
                lifecycle_input_snapshot_rows_hash,
                schema_version,
                config_hash,
                git_commit,
                data_snapshot_id,
                validation_warnings_json
            FROM run_manifests
            WHERE run_manifest_id = ?
            """,
            [run_manifest_id],
        ).fetchone()
    if row is None:
        raise ValueError(f"Unknown run_manifest_id: {run_manifest_id}")
    columns = [
        "run_manifest_id",
        "lifecycle_run_id",
        "execution_run_id",
        "execution_decision_rows_hash",
        "source_signal_snapshot_id",
        "source_signal_rows_hash",
        "price_snapshot_id",
        "price_snapshot_rows_hash",
        "lifecycle_input_snapshot_id",
        "lifecycle_input_snapshot_rows_hash",
        "schema_version",
        "config_hash",
        "git_commit",
        "data_snapshot_id",
        "validation_warnings_json",
    ]
    payload = dict(zip(columns, row, strict=True))
    payload["validation_warnings"] = json.loads(str(payload.pop("validation_warnings_json")))
    return payload


def validate_run_manifest(
    config: SectorScoutConfig,
    run_manifest_id: str,
) -> RunManifestValidationResult:
    manifest = _load_manifest(config, run_manifest_id)
    errors: list[str] = []
    warnings: list[str] = list(manifest["validation_warnings"])
    current_payload, current_errors, current_warnings = _build_manifest_payload(
        config,
        manifest["lifecycle_run_id"],
    )
    errors.extend(current_errors)
    warnings.extend(current_warnings)
    if current_payload["execution_run_id"] != manifest["execution_run_id"]:
        errors.append(f"EXECUTION_RUN_ID_CHANGED: {run_manifest_id}")
    if current_payload["source_signal_snapshot_id"] != manifest["source_signal_snapshot_id"]:
        errors.append(f"SOURCE_SIGNAL_SNAPSHOT_ID_CHANGED: {run_manifest_id}")
    if current_payload["source_signal_rows_hash"] != manifest["source_signal_rows_hash"]:
        errors.append(f"SOURCE_SIGNAL_SNAPSHOT_MANIFEST_HASH_MISMATCH: {run_manifest_id}")
    if current_payload["price_snapshot_id"] != manifest["price_snapshot_id"]:
        errors.append(f"PRICE_SNAPSHOT_ID_CHANGED: {run_manifest_id}")
    if (
        current_payload["lifecycle_input_snapshot_id"]
        != manifest["lifecycle_input_snapshot_id"]
    ):
        errors.append(f"LIFECYCLE_INPUT_SNAPSHOT_ID_CHANGED: {run_manifest_id}")
    if int(manifest["schema_version"]) != SCHEMA_VERSION:
        warnings.append(
            f"MANIFEST_SCHEMA_VERSION_DRIFT: stored={manifest['schema_version']} current={SCHEMA_VERSION}"
        )
    for field_name in ("config_hash", "git_commit", "data_snapshot_id"):
        if manifest.get(field_name) != current_payload.get(field_name):
            warnings.append(
                f"MANIFEST_{field_name.upper()}_DRIFT: "
                f"stored={manifest.get(field_name)} current={current_payload.get(field_name)}"
            )
    _row_count, current_execution_hash = execution_decision_rows_hash(
        config,
        manifest["execution_run_id"],
    )
    if current_execution_hash != manifest["execution_decision_rows_hash"]:
        errors.append(f"EXECUTION_DECISION_ROWSET_HASH_MISMATCH: {run_manifest_id}")

    current_price_hash: str | None = None
    if not manifest["price_snapshot_id"]:
        errors.append("MISSING_PRICE_SNAPSHOT_ID")
    else:
        try:
            stored_price_hash, calculated_price_hash, hash_warnings = _price_snapshot_hash(
                config,
                manifest["price_snapshot_id"],
            )
            warnings.extend(hash_warnings)
            current_price_hash = stored_price_hash or calculated_price_hash
            if current_price_hash != manifest["price_snapshot_rows_hash"]:
                errors.append(f"PRICE_SNAPSHOT_MANIFEST_HASH_MISMATCH: {run_manifest_id}")
        except ValueError as exc:
            errors.append(str(exc))

    current_input_hash: str | None = None
    if not manifest["lifecycle_input_snapshot_id"]:
        errors.append("MISSING_LIFECYCLE_INPUT_SNAPSHOT_ID")
    else:
        try:
            stored_input_hash, calculated_input_hash = _lifecycle_input_snapshot_hash(
                config,
                manifest["lifecycle_input_snapshot_id"],
            )
            current_input_hash = stored_input_hash or calculated_input_hash
            if current_input_hash != manifest["lifecycle_input_snapshot_rows_hash"]:
                errors.append(
                    f"LIFECYCLE_INPUT_SNAPSHOT_MANIFEST_HASH_MISMATCH: {run_manifest_id}"
                )
        except ValueError as exc:
            errors.append(str(exc))

    return RunManifestValidationResult(
        run_manifest_id=run_manifest_id,
        lifecycle_run_id=manifest["lifecycle_run_id"],
        execution_run_id=manifest["execution_run_id"],
        validation_status=_validation_status(errors),
        validation_errors=_dedupe(errors),
        validation_warnings=_dedupe(warnings),
        execution_decision_rows_hash=current_execution_hash,
        source_signal_rows_hash=current_payload["source_signal_rows_hash"],
        price_snapshot_rows_hash=current_price_hash,
        lifecycle_input_snapshot_rows_hash=current_input_hash,
        warning=_manifest_warning(),
    )
