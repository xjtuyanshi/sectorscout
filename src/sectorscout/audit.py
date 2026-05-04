from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timezone
from hashlib import sha256
from uuid import uuid4

from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database
from sectorscout.run_manifest import validate_run_manifest


@dataclass(frozen=True)
class ProvenanceAuditReport:
    audit_report_id: str | None
    run_manifest_id: str
    strict_mode: bool
    audit_report_hash: str | None
    audit_created_at_utc: str | None
    audit_exported: bool
    validation_status: str
    validation_errors: list[str]
    validation_warnings: list[str]
    audit_completeness_status: str
    audit_completeness_warnings: list[str]
    run_ids: dict
    manifest_metadata: dict
    source_signal_provenance: dict
    source_signal_snapshot: dict
    execution_decision_rowset: dict
    price_snapshot: dict
    lifecycle_input_snapshot: dict
    lifecycle_qa: dict
    lifecycle_input_qa: dict
    warning: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class AuditReportValidation:
    audit_report_id: str
    run_manifest_id: str
    validation_status: str
    validation_errors: list[str]
    validation_warnings: list[str]
    stored_audit_report_hash: str
    stored_payload_hash: str
    recomputed_audit_report_hash: str | None
    warning: str

    def to_dict(self) -> dict:
        return asdict(self)


AUDIT_HASH_EXCLUDED_KEYS = {
    "audit_report_id",
    "audit_report_hash",
    "audit_created_at_utc",
}


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


def _json_dict(value: object) -> dict:
    if not value:
        return {}
    payload = json.loads(str(value))
    return dict(payload)


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _hashable_audit_payload(payload: dict) -> dict:
    return {key: value for key, value in payload.items() if key not in AUDIT_HASH_EXCLUDED_KEYS}


def audit_report_hash(payload: dict) -> str:
    return sha256(_canonical_json(_hashable_audit_payload(payload)).encode("utf-8")).hexdigest()


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def _load_manifest_payload(config: SectorScoutConfig, run_manifest_id: str) -> dict:
    with connect_database(config.database.path) as connection:
        row = connection.execute(
            """
            SELECT
                run_manifest_id,
                lifecycle_run_id,
                execution_run_id,
                source_signal_snapshot_id,
                source_signal_rows_hash,
                source_signal_config_hash,
                source_signal_git_commit,
                source_universe_version,
                source_theme_version,
                execution_model,
                execution_decision_rows,
                execution_decision_rows_hash,
                price_snapshot_id,
                price_snapshot_rows_hash,
                lifecycle_input_snapshot_id,
                lifecycle_input_snapshot_rows_hash,
                schema_version,
                config_hash,
                git_commit,
                data_snapshot_id,
                validation_status,
                validation_errors_json,
                validation_warnings_json,
                created_at_utc
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
        "source_signal_snapshot_id",
        "source_signal_rows_hash",
        "source_signal_config_hash",
        "source_signal_git_commit",
        "source_universe_version",
        "source_theme_version",
        "execution_model",
        "execution_decision_rows",
        "execution_decision_rows_hash",
        "price_snapshot_id",
        "price_snapshot_rows_hash",
        "lifecycle_input_snapshot_id",
        "lifecycle_input_snapshot_rows_hash",
        "schema_version",
        "config_hash",
        "git_commit",
        "data_snapshot_id",
        "validation_status",
        "validation_errors_json",
        "validation_warnings_json",
        "created_at_utc",
    ]
    payload = dict(zip(columns, row, strict=True))
    payload["validation_errors"] = _json_list(payload.pop("validation_errors_json"))
    payload["validation_warnings"] = _json_list(payload.pop("validation_warnings_json"))
    payload["created_at_utc"] = _iso_value(payload["created_at_utc"])
    return payload


def _load_lifecycle_qa(config: SectorScoutConfig, lifecycle_run_id: str) -> dict:
    with connect_database(config.database.path) as connection:
        row = connection.execute(
            """
            SELECT
                accepted_execution_count,
                simulated_position_count,
                closed_position_count,
                open_position_count,
                skipped_count,
                missing_price_path_count,
                missing_entry_session_price_count,
                baseline_rows_count,
                baseline_symbols_expected_json,
                baseline_symbols_present_json,
                baseline_symbols_missing_json,
                baseline_provider_mix_json,
                baseline_symbol_qa_json,
                config_mismatch_warning,
                snapshot_mismatch_warning,
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
                non_price_input_mode,
                lifecycle_input_snapshot_id,
                lifecycle_input_snapshot_rows_hash,
                price_snapshot_mode
            FROM lifecycle_qa
            WHERE lifecycle_run_id = ?
            """,
            [lifecycle_run_id],
        ).fetchone()
    if row is None:
        return {"present": False}
    columns = [
        "accepted_execution_count",
        "simulated_position_count",
        "closed_position_count",
        "open_position_count",
        "skipped_count",
        "missing_price_path_count",
        "missing_entry_session_price_count",
        "baseline_rows_count",
        "baseline_symbols_expected",
        "baseline_symbols_present",
        "baseline_symbols_missing",
        "baseline_provider_mix",
        "baseline_symbol_qa",
        "config_mismatch_warning",
        "snapshot_mismatch_warning",
        "missing_baseline_coverage_warning",
        "missing_entry_session_price_warning",
        "price_snapshot_mismatch_warning",
        "non_price_input_snapshot_warning",
        "market_regime_source_mismatch_warning",
        "theme_score_source_mismatch_warning",
        "missing_market_regime_coverage_warning",
        "missing_theme_score_coverage_warning",
        "missing_lifecycle_input_qa_warning",
        "mixed_source_signal_metadata_warning",
        "non_price_input_mode",
        "lifecycle_input_snapshot_id",
        "lifecycle_input_snapshot_rows_hash",
        "price_snapshot_mode",
    ]
    payload = dict(zip(columns, row, strict=True))
    for key in (
        "baseline_symbols_expected",
        "baseline_symbols_present",
        "baseline_symbols_missing",
    ):
        payload[key] = _json_list(payload[key])
    payload["baseline_provider_mix"] = _json_dict(payload["baseline_provider_mix"])
    payload["baseline_symbol_qa"] = _json_dict(payload["baseline_symbol_qa"])
    payload["present"] = True
    return payload


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
        return {"present": False}
    columns = [
        "market_regime_rows",
        "theme_score_rows",
        "expected_market_regime_sessions",
        "missing_market_regime_sessions",
        "expected_theme_score_keys",
        "missing_theme_score_keys",
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
        "expected_market_regime_sessions",
        "missing_market_regime_sessions",
        "expected_theme_score_keys",
        "missing_theme_score_keys",
    ):
        payload[key] = _json_list(payload[key])
    payload["present"] = True
    return payload


def _audit_completeness(
    lifecycle_qa: dict,
    lifecycle_input_qa: dict,
) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if not lifecycle_qa.get("present"):
        warnings.append("MISSING_LIFECYCLE_QA")
    if not lifecycle_input_qa.get("present"):
        warnings.append("MISSING_LIFECYCLE_INPUT_QA")
    return ("PASS" if not warnings else "FAIL", warnings)


def _component_status(
    *,
    component_id: str | None,
    validated_hash: str | None,
    validation_errors: list[str],
    error_token: str,
) -> str:
    if not component_id:
        return "MISSING"
    if any(error_token in error for error in validation_errors):
        return "FAIL"
    if validated_hash:
        return "PASS"
    return "MISSING"


def _blocked_report(
    manifest: dict,
    validation: dict,
    *,
    strict: bool,
    audit_completeness_status: str = "NOT_EVALUATED",
    audit_completeness_warnings: list[str] | None = None,
) -> ProvenanceAuditReport:
    return ProvenanceAuditReport(
        audit_report_id=None,
        run_manifest_id=manifest["run_manifest_id"],
        strict_mode=strict,
        audit_report_hash=None,
        audit_created_at_utc=None,
        audit_exported=False,
        validation_status=validation["validation_status"],
        validation_errors=validation["validation_errors"],
        validation_warnings=validation["validation_warnings"],
        audit_completeness_status=audit_completeness_status,
        audit_completeness_warnings=audit_completeness_warnings or [],
        run_ids={
            "lifecycle_run_id": manifest["lifecycle_run_id"],
            "execution_run_id": manifest["execution_run_id"],
        },
        manifest_metadata={},
        source_signal_provenance={},
        source_signal_snapshot={},
        execution_decision_rowset={},
        price_snapshot={},
        lifecycle_input_snapshot={},
        lifecycle_qa={},
        lifecycle_input_qa={},
        warning=(
            "Phase 5B+ only: audit export was blocked because manifest "
            "validation or audit-completeness validation failed. This is not a "
            "result report or strategy conclusion."
        ),
    )


def _finalize_report(report: ProvenanceAuditReport) -> ProvenanceAuditReport:
    if not report.audit_exported:
        return report
    payload = report.to_dict()
    return replace(report, audit_report_hash=audit_report_hash(payload))


def persist_audit_report(config: SectorScoutConfig, report: ProvenanceAuditReport) -> None:
    if not report.audit_exported:
        return
    if report.audit_report_hash is None:
        raise ValueError("Cannot persist audit report without audit_report_hash")
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            INSERT INTO audit_reports (
                audit_report_id,
                run_manifest_id,
                strict_mode,
                audit_report_hash,
                audit_payload_json,
                audit_exported,
                validation_status,
                audit_completeness_status,
                validation_errors_json,
                validation_warnings_json,
                audit_completeness_warnings_json,
                created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                report.audit_report_id,
                report.run_manifest_id,
                report.strict_mode,
                report.audit_report_hash,
                _canonical_json(report.to_dict()),
                report.audit_exported,
                report.validation_status,
                report.audit_completeness_status,
                json.dumps(report.validation_errors, sort_keys=True),
                json.dumps(report.validation_warnings, sort_keys=True),
                json.dumps(report.audit_completeness_warnings, sort_keys=True),
                report.audit_created_at_utc,
            ],
        )


def generate_provenance_audit_report(
    config: SectorScoutConfig,
    run_manifest_id: str,
    *,
    strict: bool = True,
    persist: bool = True,
) -> ProvenanceAuditReport:
    manifest = _load_manifest_payload(config, run_manifest_id)
    validation = validate_run_manifest(config, run_manifest_id).to_dict()
    if strict and validation["validation_status"] != "PASS":
        return _blocked_report(manifest, validation, strict=strict)

    lifecycle_qa = _load_lifecycle_qa(config, manifest["lifecycle_run_id"])
    lifecycle_input_qa = _load_lifecycle_input_qa(config, manifest["lifecycle_run_id"])
    completeness_status, completeness_warnings = _audit_completeness(
        lifecycle_qa,
        lifecycle_input_qa,
    )
    if strict and completeness_status != "PASS":
        return _blocked_report(
            manifest,
            validation,
            strict=strict,
            audit_completeness_status=completeness_status,
            audit_completeness_warnings=completeness_warnings,
        )

    report = ProvenanceAuditReport(
        audit_report_id=str(uuid4()),
        run_manifest_id=run_manifest_id,
        strict_mode=strict,
        audit_report_hash=None,
        audit_created_at_utc=datetime.now(timezone.utc).isoformat(),
        audit_exported=True,
        validation_status=validation["validation_status"],
        validation_errors=validation["validation_errors"],
        validation_warnings=validation["validation_warnings"],
        audit_completeness_status=completeness_status,
        audit_completeness_warnings=completeness_warnings,
        run_ids={
            "lifecycle_run_id": manifest["lifecycle_run_id"],
            "execution_run_id": manifest["execution_run_id"],
            "execution_model": manifest["execution_model"],
        },
        manifest_metadata={
            "schema_version": manifest["schema_version"],
            "config_hash": manifest["config_hash"],
            "git_commit": manifest["git_commit"],
            "data_snapshot_id": manifest["data_snapshot_id"],
            "created_at_utc": manifest["created_at_utc"],
        },
        source_signal_provenance={
            "source_signal_snapshot_id": manifest["source_signal_snapshot_id"],
            "source_signal_config_hash": manifest["source_signal_config_hash"],
            "source_signal_git_commit": manifest["source_signal_git_commit"],
            "source_universe_version": manifest["source_universe_version"],
            "source_theme_version": manifest["source_theme_version"],
        },
        source_signal_snapshot={
            "source_signal_snapshot_id": manifest["source_signal_snapshot_id"],
            "manifest_rows_hash": manifest["source_signal_rows_hash"],
            "validated_rows_hash": validation["source_signal_rows_hash"],
            "validation_status": _component_status(
                component_id=manifest["source_signal_snapshot_id"],
                validated_hash=validation["source_signal_rows_hash"],
                validation_errors=validation["validation_errors"],
                error_token="SOURCE_SIGNAL_SNAPSHOT",
            ),
            "validation_errors": [
                error
                for error in validation["validation_errors"]
                if "SOURCE_SIGNAL_SNAPSHOT" in error
            ],
        },
        execution_decision_rowset={
            "row_count": manifest["execution_decision_rows"],
            "rowset_hash": validation["execution_decision_rows_hash"],
        },
        price_snapshot={
            "price_snapshot_id": manifest["price_snapshot_id"],
            "manifest_rows_hash": manifest["price_snapshot_rows_hash"],
            "validated_rows_hash": validation["price_snapshot_rows_hash"],
            "validation_status": _component_status(
                component_id=manifest["price_snapshot_id"],
                validated_hash=validation["price_snapshot_rows_hash"],
                validation_errors=validation["validation_errors"],
                error_token="PRICE_SNAPSHOT",
            ),
            "validation_errors": [
                error
                for error in validation["validation_errors"]
                if "PRICE_SNAPSHOT" in error
            ],
        },
        lifecycle_input_snapshot={
            "lifecycle_input_snapshot_id": manifest["lifecycle_input_snapshot_id"],
            "manifest_rows_hash": manifest["lifecycle_input_snapshot_rows_hash"],
            "validated_rows_hash": validation["lifecycle_input_snapshot_rows_hash"],
            "validation_status": _component_status(
                component_id=manifest["lifecycle_input_snapshot_id"],
                validated_hash=validation["lifecycle_input_snapshot_rows_hash"],
                validation_errors=validation["validation_errors"],
                error_token="LIFECYCLE_INPUT_SNAPSHOT",
            ),
            "validation_errors": [
                error
                for error in validation["validation_errors"]
                if "LIFECYCLE_INPUT_SNAPSHOT" in error
            ],
        },
        lifecycle_qa=lifecycle_qa,
        lifecycle_input_qa=lifecycle_input_qa,
        warning=(
            "Phase 5B+ only: provenance audit reports expose reproducibility, "
            "coverage, and warning diagnostics only; they are not result reports "
            "or strategy conclusions."
        ),
    )
    report = _finalize_report(report)
    if persist:
        persist_audit_report(config, report)
    return report


def _load_audit_report(config: SectorScoutConfig, audit_report_id: str) -> dict:
    with connect_database(config.database.path) as connection:
        row = connection.execute(
            """
            SELECT
                audit_report_id,
                run_manifest_id,
                strict_mode,
                audit_report_hash,
                audit_payload_json,
                audit_exported,
                validation_status,
                audit_completeness_status,
                validation_errors_json,
                validation_warnings_json,
                audit_completeness_warnings_json,
                created_at_utc
            FROM audit_reports
            WHERE audit_report_id = ?
            """,
            [audit_report_id],
        ).fetchone()
    if row is None:
        raise ValueError(f"Unknown audit_report_id: {audit_report_id}")
    columns = [
        "audit_report_id",
        "run_manifest_id",
        "strict_mode",
        "audit_report_hash",
        "audit_payload_json",
        "audit_exported",
        "validation_status",
        "audit_completeness_status",
        "validation_errors_json",
        "validation_warnings_json",
        "audit_completeness_warnings_json",
        "created_at_utc",
    ]
    payload = dict(zip(columns, row, strict=True))
    payload["validation_errors"] = _json_list(payload.pop("validation_errors_json"))
    payload["validation_warnings"] = _json_list(payload.pop("validation_warnings_json"))
    payload["audit_completeness_warnings"] = _json_list(
        payload.pop("audit_completeness_warnings_json")
    )
    payload["audit_payload"] = json.loads(str(payload.pop("audit_payload_json")))
    payload["created_at_utc"] = _iso_value(payload["created_at_utc"])
    return payload


def validate_audit_report(
    config: SectorScoutConfig,
    audit_report_id: str,
) -> AuditReportValidation:
    stored = _load_audit_report(config, audit_report_id)
    errors: list[str] = []
    warnings: list[str] = []
    stored_payload = stored["audit_payload"]
    stored_hash = str(stored["audit_report_hash"])
    stored_payload_hash = audit_report_hash(stored_payload)
    if stored_payload.get("audit_report_hash") != stored_hash:
        errors.append(f"AUDIT_REPORT_PAYLOAD_HASH_FIELD_MISMATCH: {audit_report_id}")
    if stored_payload_hash != stored_hash:
        errors.append(f"AUDIT_REPORT_PAYLOAD_HASH_MISMATCH: {audit_report_id}")

    recomputed_hash: str | None = None
    try:
        recomputed = generate_provenance_audit_report(
            config,
            str(stored["run_manifest_id"]),
            strict=bool(stored["strict_mode"]),
            persist=False,
        ).to_dict()
        recomputed_hash = audit_report_hash(recomputed)
        if recomputed_hash != stored_hash:
            errors.append(f"AUDIT_REPORT_SOURCE_HASH_MISMATCH: {audit_report_id}")
    except Exception as exc:  # pragma: no cover - defensive validation path
        errors.append(f"AUDIT_REPORT_RECOMPUTE_FAILED: {audit_report_id}: {exc}")

    status = "PASS" if not errors else "FAIL"
    return AuditReportValidation(
        audit_report_id=audit_report_id,
        run_manifest_id=str(stored["run_manifest_id"]),
        validation_status=status,
        validation_errors=_dedupe(errors),
        validation_warnings=_dedupe(warnings),
        stored_audit_report_hash=stored_hash,
        stored_payload_hash=stored_payload_hash,
        recomputed_audit_report_hash=recomputed_hash,
        warning=(
            "Phase 5B+ only: audit report validation checks persisted audit "
            "artifact integrity and source drift; it is not a result report or "
            "strategy conclusion."
        ),
    )
