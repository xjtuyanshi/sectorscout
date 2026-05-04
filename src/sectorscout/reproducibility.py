from __future__ import annotations

from dataclasses import asdict, dataclass

from sectorscout.audit import generate_provenance_audit_report, validate_audit_report
from sectorscout.config import SectorScoutConfig
from sectorscout.run_manifest import validate_run_manifest


@dataclass(frozen=True)
class ReproducibilityCheckResult:
    run_manifest_id: str
    audit_report_id: str | None
    validation_status: str
    failure_reasons: list[str]
    validation_warnings: list[str]
    lifecycle_run_id: str | None
    execution_run_id: str | None
    source_signal_snapshot_id: str | None
    price_snapshot_id: str | None
    lifecycle_input_snapshot_id: str | None
    execution_decision_rows_hash: str | None
    source_signal_rows_hash: str | None
    price_snapshot_rows_hash: str | None
    lifecycle_input_snapshot_rows_hash: str | None
    audit_report_hash: str | None
    manifest_validation_status: str
    audit_exported: bool
    audit_completeness_status: str
    audit_completeness_warnings: list[str]
    audit_report_validation_status: str | None
    audit_report_validation_errors: list[str]
    warning: str

    def to_dict(self) -> dict:
        return asdict(self)


def _failure_reasons(
    *,
    manifest_status: str,
    audit_exported: bool,
    audit_completeness_status: str,
    audit_validation_status: str | None,
    audit_manifest_mismatch: bool,
    audit_hash_mismatch: bool,
) -> list[str]:
    reasons: list[str] = []
    if manifest_status != "PASS":
        reasons.append("MANIFEST_VALIDATION_FAIL")
    if not audit_exported:
        reasons.append("AUDIT_EXPORT_BLOCKED")
    if audit_completeness_status == "FAIL":
        reasons.append("AUDIT_COMPLETENESS_FAIL")
    if audit_validation_status is not None and audit_validation_status != "PASS":
        reasons.append("AUDIT_REPORT_VALIDATION_FAIL")
    if audit_manifest_mismatch:
        reasons.append("AUDIT_REPORT_MANIFEST_MISMATCH")
    if audit_hash_mismatch:
        reasons.append("AUDIT_REPORT_HASH_DOES_NOT_MATCH_CURRENT_MANIFEST_AUDIT")
    return reasons


def run_reproducibility_check(
    config: SectorScoutConfig,
    run_manifest_id: str,
    *,
    audit_report_id: str | None = None,
) -> ReproducibilityCheckResult:
    manifest = validate_run_manifest(config, run_manifest_id).to_dict()
    audit_report = generate_provenance_audit_report(
        config,
        run_manifest_id,
        strict=True,
        persist=audit_report_id is None,
    ).to_dict()
    effective_audit_report_id = audit_report_id or audit_report["audit_report_id"]

    audit_validation: dict | None = None
    if effective_audit_report_id:
        audit_validation = validate_audit_report(config, effective_audit_report_id).to_dict()

    audit_validation_status = (
        audit_validation["validation_status"] if audit_validation is not None else None
    )
    audit_manifest_mismatch = (
        audit_validation is not None
        and str(audit_validation["run_manifest_id"]) != run_manifest_id
    )
    audit_hash_mismatch = (
        audit_validation is not None
        and audit_report["audit_report_hash"] is not None
        and audit_validation["stored_audit_report_hash"] != audit_report["audit_report_hash"]
    )
    reasons = _failure_reasons(
        manifest_status=manifest["validation_status"],
        audit_exported=bool(audit_report["audit_exported"]),
        audit_completeness_status=str(audit_report["audit_completeness_status"]),
        audit_validation_status=audit_validation_status,
        audit_manifest_mismatch=audit_manifest_mismatch,
        audit_hash_mismatch=audit_hash_mismatch,
    )
    return ReproducibilityCheckResult(
        run_manifest_id=run_manifest_id,
        audit_report_id=effective_audit_report_id,
        validation_status="PASS" if not reasons else "FAIL",
        failure_reasons=reasons,
        validation_warnings=[
            *manifest["validation_warnings"],
            *audit_report["validation_warnings"],
            *(audit_validation["validation_warnings"] if audit_validation else []),
        ],
        lifecycle_run_id=audit_report["run_ids"].get("lifecycle_run_id"),
        execution_run_id=audit_report["run_ids"].get("execution_run_id"),
        source_signal_snapshot_id=audit_report["source_signal_snapshot"].get(
            "source_signal_snapshot_id"
        ),
        price_snapshot_id=audit_report["price_snapshot"].get("price_snapshot_id"),
        lifecycle_input_snapshot_id=audit_report["lifecycle_input_snapshot"].get(
            "lifecycle_input_snapshot_id"
        ),
        execution_decision_rows_hash=audit_report["execution_decision_rowset"].get(
            "rowset_hash"
        ),
        source_signal_rows_hash=audit_report["source_signal_snapshot"].get(
            "validated_rows_hash"
        ),
        price_snapshot_rows_hash=audit_report["price_snapshot"].get("validated_rows_hash"),
        lifecycle_input_snapshot_rows_hash=audit_report["lifecycle_input_snapshot"].get(
            "validated_rows_hash"
        ),
        audit_report_hash=(
            audit_validation["stored_audit_report_hash"]
            if audit_validation
            else audit_report["audit_report_hash"]
        ),
        manifest_validation_status=manifest["validation_status"],
        audit_exported=bool(audit_report["audit_exported"]),
        audit_completeness_status=str(audit_report["audit_completeness_status"]),
        audit_completeness_warnings=audit_report["audit_completeness_warnings"],
        audit_report_validation_status=audit_validation_status,
        audit_report_validation_errors=(
            audit_validation["validation_errors"] if audit_validation else []
        ),
        warning=(
            "Phase 5B+ only: reproducibility checks orchestrate existing "
            "manifest, audit-completeness, and audit-artifact validation; they "
            "are not result reports or strategy conclusions."
        ),
    )
