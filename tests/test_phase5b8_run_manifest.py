from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from sectorscout.cli import app
from sectorscout.audit import generate_provenance_audit_report, validate_audit_report
from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database, initialize_database
from sectorscout.lifecycle_inputs import (
    MARKET_REGIME_COLUMNS,
    THEME_SCORE_COLUMNS,
    lifecycle_input_snapshot_rows_hash,
)
from sectorscout.prices import PRICE_SNAPSHOT_COLUMNS, snapshot_rows_hash
from sectorscout.reproducibility import run_reproducibility_check
from sectorscout.run_manifest import generate_run_manifest, validate_run_manifest
from sectorscout.source_signals import SOURCE_SIGNAL_COLUMNS, source_signal_snapshot_rows_hash


EXECUTION_RUN_ID = "execution-run"
LIFECYCLE_RUN_ID = "lifecycle-run"
PRICE_SNAPSHOT_ID = "price-snapshot"
INPUT_SNAPSHOT_ID = "input-snapshot"
SOURCE_SIGNAL_SNAPSHOT_ID = "signal_snapshot"


def _config(tmp_path: Path) -> SectorScoutConfig:
    config = SectorScoutConfig.model_validate({"database": {"path": tmp_path / "test.duckdb"}})
    initialize_database(config)
    return config


def _price_rows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "MU",
                "price_date": date(2024, 12, 2),
                "adj_open": 101.0,
                "adj_high": 102.0,
                "adj_low": 100.0,
                "adj_close": 101.5,
                "adj_volume": 1000000,
                "provider": "FMP",
                "adjustment_warning": False,
            },
            {
                "symbol": "MU",
                "price_date": date(2024, 12, 3),
                "adj_open": 102.0,
                "adj_high": 103.0,
                "adj_low": 101.0,
                "adj_close": 102.5,
                "adj_volume": 1000000,
                "provider": "FMP",
                "adjustment_warning": False,
            },
            *[
                {
                    "symbol": symbol,
                    "price_date": date(2024, 12, 3),
                    "adj_open": 500.0,
                    "adj_high": 501.0,
                    "adj_low": 499.0,
                    "adj_close": 500.0,
                    "adj_volume": 1000000,
                    "provider": "FMP",
                    "adjustment_warning": False,
                }
                for symbol in ("QQQ", "SMH", "SPY")
            ],
        ],
        columns=PRICE_SNAPSHOT_COLUMNS,
    )


def _market_row(asof_date: date) -> dict:
    return {
        "asof_date": asof_date,
        "spy_stage": "Stage 2",
        "qqq_stage": "Stage 2",
        "spy_above_50dma": True,
        "spy_above_200dma": True,
        "qqq_above_50dma": True,
        "qqq_above_200dma": True,
        "pct_universe_above_50dma": 0.7,
        "pct_universe_above_200dma": 0.65,
        "pct_universe_stage2": 0.5,
        "risk_state": "RISK_ON",
        "signal_generated_at_utc": "2024-12-02T21:01:00+00:00",
        "config_hash": "signal_hash",
        "git_commit": "signal_commit",
        "data_snapshot_id": "signal_snapshot",
        "universe_version": "signal_universe",
        "theme_version": "signal_theme",
    }


def _theme_row(asof_date: date) -> dict:
    return {
        "asof_date": asof_date,
        "theme_id": "ai-memory",
        "theme_score": 75.0,
        "technical_relative_strength": 80.0,
        "breadth": 70.0,
        "fundamental_acceleration": 65.0,
        "catalyst_score": 0.0,
        "risk_valuation_penalty": 0.0,
        "component_coverage_pct": 90.0,
        "members_count": 1,
        "raw_members_count": 1,
        "eligible_members_count": 1,
        "excluded_members_count": 0,
        "technical_coverage_pct": 100.0,
        "theme_fundamental_coverage_pct": 80.0,
        "members_with_valid_fundamentals": 1,
        "signal_generated_at_utc": "2024-12-02T21:01:00+00:00",
        "config_hash": "signal_hash",
        "git_commit": "signal_commit",
        "data_snapshot_id": "signal_snapshot",
        "universe_version": "signal_universe",
        "theme_version": "signal_theme",
    }


def _insert_price_snapshot(config: SectorScoutConfig) -> str:
    rows = _price_rows()
    rows_hash = snapshot_rows_hash(rows)
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            INSERT INTO price_snapshot_runs (
                price_snapshot_id, asof_date, provider_priority_json,
                provider_mix_json, duplicate_provider_rows_dropped,
                min_price_date, max_price_date, raw_row_count,
                chosen_row_count, snapshot_rows_hash, config_hash,
                git_commit, created_at_utc
            ) VALUES (
                ?, DATE '2024-12-02', '["FMP", "yfinance", "fixture"]',
                '{"FMP": 5}', 0, DATE '2024-12-02', DATE '2024-12-03',
                5, 5, ?, 'price_hash', 'price_commit',
                '2024-12-02T21:01:00+00:00'
            )
            """,
            [PRICE_SNAPSHOT_ID, rows_hash],
        )
        for _, row in rows.iterrows():
            connection.execute(
                """
                INSERT INTO price_snapshot_rows (
                    price_snapshot_id, symbol, price_date, adj_open, adj_high,
                    adj_low, adj_close, adj_volume, provider, adjustment_warning
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    PRICE_SNAPSHOT_ID,
                    row["symbol"],
                    row["price_date"],
                    row["adj_open"],
                    row["adj_high"],
                    row["adj_low"],
                    row["adj_close"],
                    int(row["adj_volume"]),
                    row["provider"],
                    bool(row["adjustment_warning"]),
                ],
            )
    return rows_hash


def _source_signal_row() -> dict:
    return {
        "asof_date": date(2024, 11, 29),
        "symbol": "MU",
        "theme_id": "ai-memory",
        "setup_type": "VCP",
        "state": "TRIGGERED",
        "action_category": "Triggered setup candidate",
        "actionable": False,
        "reason": "Phase 4 candidate",
        "execution_model": "next_open",
        "entry_trigger": 100.0,
        "stop_loss": 95.0,
        "reward_risk": 2.0,
        "setup_data_present": True,
        "execution_data_quality_pass": False,
        "price_snapshot_quality_pass": True,
        "data_quality_pass": False,
        "data_quality_reason": "Phase 5 execution validation required.",
        "market_gate_pass": True,
        "market_gate_reason": "Market gate pass.",
        "market_regime_risk_state": "RISK_ON",
        "portfolio_risk_pass": True,
        "portfolio_risk_reason": "Portfolio gate pass.",
        "signal_generated_at_utc": datetime.fromisoformat("2024-11-29T21:00:00+00:00"),
        "config_hash": "signal_hash",
        "git_commit": "signal_commit",
        "data_snapshot_id": "signal_snapshot",
        "universe_version": "signal_universe",
        "theme_version": "signal_theme",
    }


def _insert_source_signal_snapshot(config: SectorScoutConfig) -> str:
    rows = pd.DataFrame([_source_signal_row()], columns=SOURCE_SIGNAL_COLUMNS)
    rows_hash = source_signal_snapshot_rows_hash(rows)
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
            ) VALUES (
                ?, DATE '2024-11-29', 'next_open', 1, ?,
                '["signal_hash"]', '["signal_commit"]', '["signal_snapshot"]',
                '["signal_universe"]', '["signal_theme"]',
                'snapshot_hash', 'snapshot_commit', 'snapshot_data',
                '2024-11-29T21:00:00+00:00'
            )
            """,
            [SOURCE_SIGNAL_SNAPSHOT_ID, rows_hash],
        )
        row = _source_signal_row()
        connection.execute(
            f"""
            INSERT INTO source_signal_snapshot_rows (
                source_signal_snapshot_id, {", ".join(SOURCE_SIGNAL_COLUMNS)}
            ) VALUES ({", ".join(["?"] * (len(SOURCE_SIGNAL_COLUMNS) + 1))})
            """,
            [SOURCE_SIGNAL_SNAPSHOT_ID] + [row[column] for column in SOURCE_SIGNAL_COLUMNS],
        )
    return rows_hash


def _insert_execution_and_lifecycle(
    config: SectorScoutConfig,
    *,
    price_snapshot_id: str | None = PRICE_SNAPSHOT_ID,
    lifecycle_input_snapshot_id: str | None = INPUT_SNAPSHOT_ID,
    insert_execution_decision: bool = True,
) -> None:
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            INSERT INTO execution_runs (
                execution_run_id, asof_date, execution_model,
                execution_generated_at_utc, execution_config_hash,
                execution_git_commit, execution_data_snapshot_id,
                source_signal_snapshot_id, source_signal_config_hash,
                source_signal_git_commit, source_universe_version,
                source_theme_version, mixed_source_signal_metadata,
                price_snapshot_id, created_at_utc
            ) VALUES (
                ?, DATE '2024-11-29', 'next_open',
                '2024-11-29T21:01:00+00:00', 'execution_hash',
                'execution_commit', 'execution_snapshot',
                'signal_snapshot', 'signal_hash', 'signal_commit',
                'signal_universe', 'signal_theme', false, ?,
                '2024-11-29T21:01:00+00:00'
            )
            """,
            [EXECUTION_RUN_ID, price_snapshot_id],
        )
        if insert_execution_decision:
            connection.execute(
                """
                INSERT INTO execution_decisions (
                execution_run_id, asof_date, symbol, theme_id, setup_type,
                execution_model, decision, reject_reason, signal_entry_trigger,
                signal_stop_loss, next_session_date, chosen_provider,
                actual_entry_price, actual_stop_loss, risk_per_share, target_2r,
                target_3r, reward_risk, max_entry_extension_pct,
                max_initial_stop_pct, require_next_open_above_trigger,
                max_entry_drop_below_trigger_pct, execution_price_available,
                execution_data_quality_pass, execution_rule_pass, risk_rule_pass,
                source_signal_generated_at_utc, source_signal_config_hash,
                source_signal_git_commit, source_signal_data_snapshot_id,
                source_universe_version, source_theme_version,
                execution_generated_at_utc, execution_config_hash,
                execution_git_commit, execution_data_snapshot_id,
                price_snapshot_id
            ) VALUES (
                ?, DATE '2024-11-29', 'MU', 'ai-memory', 'VCP',
                'next_open', 'SIMULATED_NEXT_OPEN_ACCEPTED', NULL,
                100, 95, DATE '2024-12-02', 'FMP',
                101, 95, 6, 113, 119, 2.0, 0.05, 0.12, false,
                0.02, true, true, true, true,
                '2024-11-29T21:00:00+00:00', 'signal_hash',
                'signal_commit', 'signal_snapshot', 'signal_universe',
                'signal_theme', '2024-11-29T21:01:00+00:00',
                'execution_hash', 'execution_commit', 'execution_snapshot',
                ?
            )
                """,
                [EXECUTION_RUN_ID, price_snapshot_id],
            )
        connection.execute(
            """
            INSERT INTO lifecycle_runs (
                lifecycle_run_id, execution_run_id, through_date,
                lifecycle_generated_at_utc, lifecycle_config_hash,
                lifecycle_git_commit, lifecycle_data_snapshot_id,
                price_snapshot_id, lifecycle_input_snapshot_id, created_at_utc
            ) VALUES (
                ?, ?, DATE '2024-12-03',
                '2024-12-03T21:01:00+00:00', 'lifecycle_hash',
                'lifecycle_commit', 'lifecycle_snapshot', ?, ?,
                '2024-12-03T21:01:00+00:00'
            )
            """,
            [
                LIFECYCLE_RUN_ID,
                EXECUTION_RUN_ID,
                price_snapshot_id,
                lifecycle_input_snapshot_id,
            ],
        )


def _insert_lifecycle_input_snapshot(config: SectorScoutConfig) -> str:
    market_rows = [_market_row(date(2024, 12, 2)), _market_row(date(2024, 12, 3))]
    theme_rows = [_theme_row(date(2024, 12, 2)), _theme_row(date(2024, 12, 3))]
    rows_hash = lifecycle_input_snapshot_rows_hash(market_rows, theme_rows)
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            INSERT INTO lifecycle_input_snapshot_runs (
                lifecycle_input_snapshot_id, execution_run_id, through_date,
                expected_market_regime_sessions_json,
                expected_theme_score_keys_json,
                market_regime_rows, theme_score_rows,
                market_regime_rows_hash, theme_score_rows_hash,
                snapshot_rows_hash, market_regime_config_hashes_json,
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
            ) VALUES (
                ?, ?, DATE '2024-12-03',
                '["2024-12-02", "2024-12-03"]',
                '["ai-memory:2024-12-02", "ai-memory:2024-12-03"]',
                2, 2, 'market_hash', 'theme_hash', ?,
                '["signal_hash"]', '["signal_commit"]',
                '["signal_snapshot"]', '["signal_universe"]',
                '["signal_theme"]', '["signal_hash"]',
                '["signal_commit"]', '["signal_snapshot"]',
                '["signal_universe"]', '["signal_theme"]',
                'input_hash', 'input_commit', 'input_snapshot',
                '2024-12-03T21:01:00+00:00'
            )
            """,
            [INPUT_SNAPSHOT_ID, EXECUTION_RUN_ID, rows_hash],
        )
        for row in market_rows:
            connection.execute(
                f"""
                INSERT INTO lifecycle_market_regime_snapshot_rows (
                    lifecycle_input_snapshot_id, {", ".join(MARKET_REGIME_COLUMNS)}
                ) VALUES ({", ".join(["?"] * (len(MARKET_REGIME_COLUMNS) + 1))})
                """,
                [INPUT_SNAPSHOT_ID] + [row[column] for column in MARKET_REGIME_COLUMNS],
            )
        for row in theme_rows:
            connection.execute(
                f"""
                INSERT INTO lifecycle_theme_score_snapshot_rows (
                    lifecycle_input_snapshot_id, {", ".join(THEME_SCORE_COLUMNS)}
                ) VALUES ({", ".join(["?"] * (len(THEME_SCORE_COLUMNS) + 1))})
                """,
                [INPUT_SNAPSHOT_ID] + [row[column] for column in THEME_SCORE_COLUMNS],
            )
    return rows_hash


def _insert_lifecycle_qa_rows(config: SectorScoutConfig, input_hash: str) -> None:
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            INSERT INTO lifecycle_input_qa (
                lifecycle_run_id, execution_run_id, market_regime_rows,
                theme_score_rows, expected_market_regime_sessions_json,
                missing_market_regime_sessions_json, expected_theme_score_keys_json,
                missing_theme_score_keys_json, market_regime_source_mismatch_warning,
                theme_score_source_mismatch_warning,
                missing_market_regime_coverage_warning,
                missing_theme_score_coverage_warning,
                mixed_source_signal_metadata_warning,
                non_price_input_snapshot_warning, non_price_input_mode,
                lifecycle_input_snapshot_id, lifecycle_input_snapshot_rows_hash,
                lifecycle_generated_at_utc, lifecycle_config_hash,
                lifecycle_git_commit, lifecycle_data_snapshot_id
            ) VALUES (
                ?, ?, 2, 2, '["2024-12-02", "2024-12-03"]', '[]',
                '["ai-memory:2024-12-02", "ai-memory:2024-12-03"]',
                '[]', false, false, false, false, false, false,
                'persisted_lifecycle_input_snapshot', ?, ?,
                '2024-12-03T21:01:00+00:00', 'lifecycle_hash',
                'lifecycle_commit', 'lifecycle_snapshot'
            )
            """,
            [LIFECYCLE_RUN_ID, EXECUTION_RUN_ID, INPUT_SNAPSHOT_ID, input_hash],
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
                missing_entry_session_price_warning, price_snapshot_mismatch_warning,
                non_price_input_snapshot_warning,
                market_regime_source_mismatch_warning,
                theme_score_source_mismatch_warning,
                missing_market_regime_coverage_warning,
                missing_theme_score_coverage_warning,
                missing_lifecycle_input_qa_warning,
                mixed_source_signal_metadata_warning,
                non_price_input_mode, lifecycle_input_snapshot_id,
                lifecycle_input_snapshot_rows_hash, non_price_input_qa_json,
                price_snapshot_mode, lifecycle_generated_at_utc,
                lifecycle_config_hash, lifecycle_git_commit,
                lifecycle_data_snapshot_id
            ) VALUES (
                ?, ?, 1, 1, 1, 0, 0, 0, 0, 3,
                '["QQQ", "SMH", "SPY"]',
                '["QQQ", "SMH", "SPY"]',
                '[]', '{"FMP": 3}', '{}',
                DATE '2024-12-03', DATE '2024-12-03',
                false, false, false, false, false, false,
                false, false, false, false, false, false,
                'persisted_lifecycle_input_snapshot', ?, ?, '{}',
                'persisted_price_snapshot',
                '2024-12-03T21:01:00+00:00', 'lifecycle_hash',
                'lifecycle_commit', 'lifecycle_snapshot'
            )
            """,
            [LIFECYCLE_RUN_ID, EXECUTION_RUN_ID, INPUT_SNAPSHOT_ID, input_hash],
        )


def _complete_manifest_fixture(config: SectorScoutConfig) -> tuple[str, str, str]:
    source_hash = _insert_source_signal_snapshot(config)
    price_hash = _insert_price_snapshot(config)
    input_hash = _insert_lifecycle_input_snapshot(config)
    _insert_execution_and_lifecycle(config)
    _insert_lifecycle_qa_rows(config, input_hash)
    return source_hash, price_hash, input_hash


def _clone_run_manifest(
    config: SectorScoutConfig,
    source_manifest_id: str,
    cloned_manifest_id: str,
) -> None:
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
            )
            SELECT
                ?, lifecycle_run_id, execution_run_id,
                source_signal_snapshot_id, source_signal_rows_hash,
                source_signal_config_hash,
                source_signal_git_commit, source_universe_version,
                source_theme_version, execution_model, execution_decision_rows,
                execution_decision_rows_hash, price_snapshot_id,
                price_snapshot_rows_hash, lifecycle_input_snapshot_id,
                lifecycle_input_snapshot_rows_hash, schema_version, config_hash,
                git_commit, data_snapshot_id, validation_status,
                validation_errors_json, validation_warnings_json, created_at_utc
            FROM run_manifests
            WHERE run_manifest_id = ?
            """,
            [cloned_manifest_id, source_manifest_id],
        )


def test_phase5b8_run_manifest_persists_complete_provenance(tmp_path: Path) -> None:
    config = _config(tmp_path)
    source_hash, price_hash, input_hash = _complete_manifest_fixture(config)

    result = generate_run_manifest(config, LIFECYCLE_RUN_ID).to_dict()

    assert result["validation_status"] == "PASS"
    assert result["source_signal_config_hash"] == "signal_hash"
    assert result["source_signal_snapshot_id"] == "signal_snapshot"
    assert result["source_signal_rows_hash"] == source_hash
    assert result["price_snapshot_id"] == PRICE_SNAPSHOT_ID
    assert result["price_snapshot_rows_hash"] == price_hash
    assert result["lifecycle_input_snapshot_id"] == INPUT_SNAPSHOT_ID
    assert result["lifecycle_input_snapshot_rows_hash"] == input_hash
    assert len(result["execution_decision_rows_hash"]) == 64
    with connect_database(config.database.path) as connection:
        persisted = connection.execute(
            """
            SELECT validation_status, source_signal_config_hash,
                   source_signal_rows_hash, price_snapshot_rows_hash,
                   lifecycle_input_snapshot_rows_hash
            FROM run_manifests
            """
        ).fetchone()
    assert persisted == ("PASS", "signal_hash", source_hash, price_hash, input_hash)


def test_phase5b8_manifest_fails_without_price_snapshot(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_lifecycle_input_snapshot(config)
    _insert_execution_and_lifecycle(config, price_snapshot_id=None)

    result = generate_run_manifest(config, LIFECYCLE_RUN_ID, persist=False).to_dict()

    assert result["validation_status"] == "FAIL"
    assert "MISSING_PRICE_SNAPSHOT_ID" in result["validation_errors"]


def test_phase5b8_manifest_fails_without_lifecycle_input_snapshot(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_price_snapshot(config)
    _insert_execution_and_lifecycle(config, lifecycle_input_snapshot_id=None)

    result = generate_run_manifest(config, LIFECYCLE_RUN_ID, persist=False).to_dict()

    assert result["validation_status"] == "FAIL"
    assert "MISSING_LIFECYCLE_INPUT_SNAPSHOT_ID" in result["validation_errors"]


def test_phase5b12_manifest_fails_unknown_source_signal_snapshot(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _insert_price_snapshot(config)
    _insert_lifecycle_input_snapshot(config)
    _insert_execution_and_lifecycle(config)

    result = generate_run_manifest(config, LIFECYCLE_RUN_ID, persist=False).to_dict()

    assert result["validation_status"] == "FAIL"
    assert any(
        "UNKNOWN_SOURCE_SIGNAL_SNAPSHOT_ID" in error
        for error in result["validation_errors"]
    )


def test_phase5b12_validate_manifest_catches_source_signal_snapshot_tamper(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _complete_manifest_fixture(config)
    manifest = generate_run_manifest(config, LIFECYCLE_RUN_ID).to_dict()
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            UPDATE source_signal_snapshot_rows
            SET stop_loss = 94
            WHERE source_signal_snapshot_id = ?
            """,
            [SOURCE_SIGNAL_SNAPSHOT_ID],
        )

    result = validate_run_manifest(config, manifest["run_manifest_id"]).to_dict()

    assert result["validation_status"] == "FAIL"
    assert any(
        "SOURCE_SIGNAL_SNAPSHOT_HASH_MISMATCH" in error
        or "SOURCE_SIGNAL_SNAPSHOT_MANIFEST_HASH_MISMATCH" in error
        for error in result["validation_errors"]
    )


def test_phase5b12_manifest_fails_missing_source_signal_key(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _complete_manifest_fixture(config)
    replacement = _source_signal_row()
    replacement["symbol"] = "AMD"
    rows_hash = source_signal_snapshot_rows_hash(
        pd.DataFrame([replacement], columns=SOURCE_SIGNAL_COLUMNS)
    )
    with connect_database(config.database.path) as connection:
        connection.execute(
            "DELETE FROM source_signal_snapshot_rows WHERE source_signal_snapshot_id = ?",
            [SOURCE_SIGNAL_SNAPSHOT_ID],
        )
        connection.execute(
            f"""
            INSERT INTO source_signal_snapshot_rows (
                source_signal_snapshot_id, {", ".join(SOURCE_SIGNAL_COLUMNS)}
            ) VALUES ({", ".join(["?"] * (len(SOURCE_SIGNAL_COLUMNS) + 1))})
            """,
            [SOURCE_SIGNAL_SNAPSHOT_ID] + [replacement[column] for column in SOURCE_SIGNAL_COLUMNS],
        )
        connection.execute(
            """
            UPDATE source_signal_snapshot_runs
            SET snapshot_rows_hash = ?
            WHERE source_signal_snapshot_id = ?
            """,
            [rows_hash, SOURCE_SIGNAL_SNAPSHOT_ID],
        )

    result = generate_run_manifest(config, LIFECYCLE_RUN_ID, persist=False).to_dict()

    assert result["validation_status"] == "FAIL"
    assert any(
        "SOURCE_SIGNAL_SNAPSHOT_MISSING_KEYS" in error
        for error in result["validation_errors"]
    )


def test_phase5b12_manifest_fails_source_signal_snapshot_asof_mismatch(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _complete_manifest_fixture(config)
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            UPDATE source_signal_snapshot_runs
            SET asof_date = DATE '2024-11-28'
            WHERE source_signal_snapshot_id = ?
            """,
            [SOURCE_SIGNAL_SNAPSHOT_ID],
        )

    result = generate_run_manifest(config, LIFECYCLE_RUN_ID, persist=False).to_dict()

    assert result["validation_status"] == "FAIL"
    assert any(
        "SOURCE_SIGNAL_SNAPSHOT_ASOF_MISMATCH" in error
        for error in result["validation_errors"]
    )


def test_phase5b8_validate_manifest_catches_execution_rowset_tamper(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _complete_manifest_fixture(config)
    manifest = generate_run_manifest(config, LIFECYCLE_RUN_ID).to_dict()
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            UPDATE execution_decisions
            SET actual_entry_price = 102
            WHERE execution_run_id = ?
            """,
            [EXECUTION_RUN_ID],
        )

    result = validate_run_manifest(config, manifest["run_manifest_id"]).to_dict()

    assert result["validation_status"] == "FAIL"
    assert any("EXECUTION_DECISION_ROWSET_HASH_MISMATCH" in error for error in result["validation_errors"])


def test_phase5b8_validate_manifest_hashes_all_execution_decision_columns(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _complete_manifest_fixture(config)
    manifest = generate_run_manifest(config, LIFECYCLE_RUN_ID).to_dict()
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            UPDATE execution_decisions
            SET max_entry_extension_pct = 0.99
            WHERE execution_run_id = ?
            """,
            [EXECUTION_RUN_ID],
        )

    result = validate_run_manifest(config, manifest["run_manifest_id"]).to_dict()

    assert result["validation_status"] == "FAIL"
    assert any(
        "EXECUTION_DECISION_ROWSET_HASH_MISMATCH" in error
        for error in result["validation_errors"]
    )


def test_phase5b8_manifest_fails_when_price_snapshot_undercovered(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    rows = pd.DataFrame([_price_rows().iloc[0].to_dict()], columns=PRICE_SNAPSHOT_COLUMNS)
    rows_hash = snapshot_rows_hash(rows)
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            INSERT INTO price_snapshot_runs (
                price_snapshot_id, asof_date, provider_priority_json,
                provider_mix_json, duplicate_provider_rows_dropped,
                min_price_date, max_price_date, raw_row_count,
                chosen_row_count, snapshot_rows_hash, config_hash,
                git_commit, created_at_utc
            ) VALUES (
                ?, DATE '2024-12-02', '["FMP", "yfinance", "fixture"]',
                '{"FMP": 1}', 0, DATE '2024-12-02', DATE '2024-12-02',
                1, 1, ?, 'price_hash', 'price_commit',
                '2024-12-02T21:01:00+00:00'
            )
            """,
            [PRICE_SNAPSHOT_ID, rows_hash],
        )
        row = rows.iloc[0]
        connection.execute(
            """
            INSERT INTO price_snapshot_rows (
                price_snapshot_id, symbol, price_date, adj_open, adj_high,
                adj_low, adj_close, adj_volume, provider, adjustment_warning
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                PRICE_SNAPSHOT_ID,
                row["symbol"],
                row["price_date"],
                row["adj_open"],
                row["adj_high"],
                row["adj_low"],
                row["adj_close"],
                int(row["adj_volume"]),
                row["provider"],
                bool(row["adjustment_warning"]),
            ],
        )
    _insert_lifecycle_input_snapshot(config)
    _insert_execution_and_lifecycle(config)

    result = generate_run_manifest(config, LIFECYCLE_RUN_ID, persist=False).to_dict()

    assert result["validation_status"] == "FAIL"
    assert any("PRICE_SNAPSHOT_UNDERCOVERED" in error for error in result["validation_errors"])


def test_phase5b8_manifest_fails_when_execution_parent_missing(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_price_snapshot(config)
    _insert_lifecycle_input_snapshot(config)
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            INSERT INTO lifecycle_runs (
                lifecycle_run_id, execution_run_id, through_date,
                lifecycle_generated_at_utc, lifecycle_config_hash,
                lifecycle_git_commit, lifecycle_data_snapshot_id,
                price_snapshot_id, lifecycle_input_snapshot_id, created_at_utc
            ) VALUES (
                ?, 'missing-execution-run', DATE '2024-12-03',
                '2024-12-03T21:01:00+00:00', 'lifecycle_hash',
                'lifecycle_commit', 'lifecycle_snapshot', ?, ?,
                '2024-12-03T21:01:00+00:00'
            )
            """,
            [LIFECYCLE_RUN_ID, PRICE_SNAPSHOT_ID, INPUT_SNAPSHOT_ID],
        )

    result = generate_run_manifest(config, LIFECYCLE_RUN_ID, persist=False).to_dict()

    assert result["validation_status"] == "FAIL"
    assert "MISSING_EXECUTION_RUN" in result["validation_errors"]


def test_phase5b8_manifest_fails_on_empty_execution_decision_rowset(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _insert_price_snapshot(config)
    _insert_lifecycle_input_snapshot(config)
    _insert_execution_and_lifecycle(config, insert_execution_decision=False)

    result = generate_run_manifest(config, LIFECYCLE_RUN_ID, persist=False).to_dict()

    assert result["validation_status"] == "FAIL"
    assert "MISSING_EXECUTION_DECISIONS" in result["validation_errors"]


def test_phase5b8_manifest_fails_on_price_snapshot_id_mismatch(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _complete_manifest_fixture(config)
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            UPDATE lifecycle_runs
            SET price_snapshot_id = 'different-price-snapshot'
            WHERE lifecycle_run_id = ?
            """,
            [LIFECYCLE_RUN_ID],
        )

    result = generate_run_manifest(config, LIFECYCLE_RUN_ID, persist=False).to_dict()

    assert result["validation_status"] == "FAIL"
    assert any(
        "PRICE_SNAPSHOT_ID_MISMATCH" in error for error in result["validation_errors"]
    )


def test_phase5b8_validate_manifest_warns_on_manifest_metadata_drift(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _complete_manifest_fixture(config)
    manifest = generate_run_manifest(config, LIFECYCLE_RUN_ID).to_dict()
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            UPDATE run_manifests
            SET config_hash = 'older-config-hash'
            WHERE run_manifest_id = ?
            """,
            [manifest["run_manifest_id"]],
        )

    result = validate_run_manifest(config, manifest["run_manifest_id"]).to_dict()

    assert result["validation_status"] == "PASS"
    assert any("MANIFEST_CONFIG_HASH_DRIFT" in warning for warning in result["validation_warnings"])


def test_phase5b8_validate_manifest_catches_price_snapshot_tamper(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _complete_manifest_fixture(config)
    manifest = generate_run_manifest(config, LIFECYCLE_RUN_ID).to_dict()
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            UPDATE price_snapshot_rows
            SET adj_close = 102
            WHERE price_snapshot_id = ?
              AND symbol = 'MU'
            """,
            [PRICE_SNAPSHOT_ID],
        )

    result = validate_run_manifest(config, manifest["run_manifest_id"]).to_dict()

    assert result["validation_status"] == "FAIL"
    assert any("PRICE_SNAPSHOT_HASH_MISMATCH" in error for error in result["validation_errors"])


def test_phase5b8_validate_manifest_catches_lifecycle_input_tamper(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _complete_manifest_fixture(config)
    manifest = generate_run_manifest(config, LIFECYCLE_RUN_ID).to_dict()
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            UPDATE lifecycle_theme_score_snapshot_rows
            SET theme_score = 10
            WHERE lifecycle_input_snapshot_id = ?
              AND theme_id = 'ai-memory'
              AND asof_date = DATE '2024-12-03'
            """,
            [INPUT_SNAPSHOT_ID],
        )

    result = validate_run_manifest(config, manifest["run_manifest_id"]).to_dict()

    assert result["validation_status"] == "FAIL"
    assert any("LIFECYCLE_INPUT_SNAPSHOT_HASH_MISMATCH" in error for error in result["validation_errors"])


def test_phase5b8_run_manifest_cli_has_no_formal_metric_terms(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _complete_manifest_fixture(config)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"database:\n  path: {config.database.path}\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "run-manifest",
            "--lifecycle-run-id",
            LIFECYCLE_RUN_ID,
            "--no-persist",
            "--config",
            str(config_path),
        ],
    )
    output = result.stdout.lower()
    payload = json.loads(result.stdout)

    assert result.exit_code == 0
    assert payload["validation_status"] == "PASS"
    for forbidden in (
        "cagr",
        "sharpe",
        "max drawdown",
        "annual return",
        "annual returns",
        "win_rate",
        "win rate",
        "profit_factor",
        "profit factor",
        "expectancy",
        "edge claim",
        "claim edge",
    ):
        assert forbidden not in output


def test_phase5b8_provenance_validate_cli_fails_on_rowset_drift(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _complete_manifest_fixture(config)
    manifest = generate_run_manifest(config, LIFECYCLE_RUN_ID).to_dict()
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            UPDATE execution_decisions
            SET chosen_provider = 'changed'
            WHERE execution_run_id = ?
            """,
            [EXECUTION_RUN_ID],
        )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"database:\n  path: {config.database.path}\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "provenance-validate",
            "--run-manifest-id",
            manifest["run_manifest_id"],
            "--config",
            str(config_path),
        ],
    )
    output = result.stdout.lower()
    payload = json.loads(result.stdout)

    assert result.exit_code == 1
    assert payload["validation_status"] == "FAIL"
    assert any(
        "EXECUTION_DECISION_ROWSET_HASH_MISMATCH" in error
        for error in payload["validation_errors"]
    )
    for forbidden in (
        "cagr",
        "sharpe",
        "max drawdown",
        "annual return",
        "annual returns",
        "win_rate",
        "win rate",
        "profit_factor",
        "profit factor",
        "expectancy",
        "edge claim",
        "claim edge",
    ):
        assert forbidden not in output


def test_phase5b9_provenance_report_exports_manifest_audit_bundle(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    source_hash, price_hash, input_hash = _complete_manifest_fixture(config)
    manifest = generate_run_manifest(config, LIFECYCLE_RUN_ID).to_dict()

    result = generate_provenance_audit_report(
        config,
        manifest["run_manifest_id"],
    ).to_dict()

    assert result["audit_exported"] is True
    assert result["validation_status"] == "PASS"
    assert result["audit_report_hash"]
    assert result["audit_completeness_status"] == "PASS"
    assert result["run_ids"]["execution_run_id"] == EXECUTION_RUN_ID
    assert result["source_signal_provenance"]["source_signal_config_hash"] == "signal_hash"
    assert result["source_signal_snapshot"]["validated_rows_hash"] == source_hash
    assert result["price_snapshot"]["validated_rows_hash"] == price_hash
    assert result["lifecycle_input_snapshot"]["validated_rows_hash"] == input_hash
    assert result["execution_decision_rowset"]["row_count"] == 1
    with connect_database(config.database.path) as connection:
        persisted = connection.execute(
            """
            SELECT run_manifest_id, audit_report_hash, audit_completeness_status
            FROM audit_reports
            WHERE audit_report_id = ?
            """,
            [result["audit_report_id"]],
        ).fetchone()
    assert persisted == (
        manifest["run_manifest_id"],
        result["audit_report_hash"],
        "PASS",
    )


def test_phase5b9_provenance_report_blocks_failed_manifest(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _complete_manifest_fixture(config)
    manifest = generate_run_manifest(config, LIFECYCLE_RUN_ID).to_dict()
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            UPDATE execution_decisions
            SET actual_entry_price = 999
            WHERE execution_run_id = ?
            """,
            [EXECUTION_RUN_ID],
        )

    result = generate_provenance_audit_report(
        config,
        manifest["run_manifest_id"],
    ).to_dict()

    assert result["audit_exported"] is False
    assert result["validation_status"] == "FAIL"
    assert result["execution_decision_rowset"] == {}


def test_phase5b10_provenance_report_blocks_missing_lifecycle_qa_in_strict_mode(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _complete_manifest_fixture(config)
    manifest = generate_run_manifest(config, LIFECYCLE_RUN_ID).to_dict()
    with connect_database(config.database.path) as connection:
        connection.execute("DELETE FROM lifecycle_qa WHERE lifecycle_run_id = ?", [LIFECYCLE_RUN_ID])

    result = generate_provenance_audit_report(
        config,
        manifest["run_manifest_id"],
    ).to_dict()

    assert result["audit_exported"] is False
    assert result["validation_status"] == "PASS"
    assert result["audit_completeness_status"] == "FAIL"
    assert "MISSING_LIFECYCLE_QA" in result["audit_completeness_warnings"]


def test_phase5b10_provenance_report_blocks_missing_lifecycle_input_qa_in_strict_mode(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _complete_manifest_fixture(config)
    manifest = generate_run_manifest(config, LIFECYCLE_RUN_ID).to_dict()
    with connect_database(config.database.path) as connection:
        connection.execute(
            "DELETE FROM lifecycle_input_qa WHERE lifecycle_run_id = ?",
            [LIFECYCLE_RUN_ID],
        )

    result = generate_provenance_audit_report(
        config,
        manifest["run_manifest_id"],
    ).to_dict()

    assert result["audit_exported"] is False
    assert result["validation_status"] == "PASS"
    assert result["audit_completeness_status"] == "FAIL"
    assert "MISSING_LIFECYCLE_INPUT_QA" in result["audit_completeness_warnings"]


def test_phase5b10_provenance_report_non_strict_surfaces_component_failure(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _complete_manifest_fixture(config)
    manifest = generate_run_manifest(config, LIFECYCLE_RUN_ID).to_dict()
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            UPDATE price_snapshot_rows
            SET adj_close = 888
            WHERE price_snapshot_id = ?
              AND symbol = 'MU'
              AND price_date = DATE '2024-12-03'
            """,
            [PRICE_SNAPSHOT_ID],
        )

    result = generate_provenance_audit_report(
        config,
        manifest["run_manifest_id"],
        strict=False,
        persist=False,
    ).to_dict()

    assert result["audit_exported"] is True
    assert result["validation_status"] == "FAIL"
    assert result["price_snapshot"]["validation_status"] == "FAIL"
    assert result["price_snapshot"]["validation_errors"]


def test_phase5b9_provenance_report_uses_frozen_snapshot_provenance_after_live_mutation(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    source_hash, price_hash, input_hash = _complete_manifest_fixture(config)
    manifest = generate_run_manifest(config, LIFECYCLE_RUN_ID).to_dict()
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            INSERT INTO daily_prices (
                symbol, price_date, open, high, low, close, volume,
                adj_open, adj_high, adj_low, adj_close, adj_volume,
                provider, is_adjusted, adjustment_warning, ingested_at_utc
            ) VALUES (
                'MU', DATE '2024-12-03', 999, 1000, 998, 999, 1000000,
                999, 1000, 998, 999, 1000000, 'yfinance', true, false, now()
            )
            """
        )

    result = generate_provenance_audit_report(
        config,
        manifest["run_manifest_id"],
    ).to_dict()

    assert result["audit_exported"] is True
    assert result["price_snapshot"]["validated_rows_hash"] == price_hash
    assert result["lifecycle_input_snapshot"]["validated_rows_hash"] == input_hash


def test_phase5b10_provenance_report_cli_fails_on_strict_manifest_error(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _complete_manifest_fixture(config)
    manifest = generate_run_manifest(config, LIFECYCLE_RUN_ID).to_dict()
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            UPDATE execution_decisions
            SET chosen_provider = 'changed'
            WHERE execution_run_id = ?
            """,
            [EXECUTION_RUN_ID],
        )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"database:\n  path: {config.database.path}\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "provenance-report",
            "--run-manifest-id",
            manifest["run_manifest_id"],
            "--config",
            str(config_path),
        ],
    )
    payload = json.loads(result.stdout)

    assert result.exit_code == 1
    assert payload["audit_exported"] is False
    assert payload["validation_status"] == "FAIL"


def test_phase5b9_provenance_report_cli_has_no_formal_metric_terms(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _complete_manifest_fixture(config)
    manifest = generate_run_manifest(config, LIFECYCLE_RUN_ID).to_dict()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"database:\n  path: {config.database.path}\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "provenance-report",
            "--run-manifest-id",
            manifest["run_manifest_id"],
            "--config",
            str(config_path),
        ],
    )
    output = result.stdout.lower()
    payload = json.loads(result.stdout)

    assert result.exit_code == 0
    assert payload["audit_exported"] is True
    for forbidden in (
        "cagr",
        "sharpe",
        "max drawdown",
        "annual return",
        "annual returns",
        "win_rate",
        "win rate",
        "profit_factor",
        "profit factor",
        "expectancy",
        "edge claim",
        "claim edge",
    ):
        assert forbidden not in output


def test_phase5b10_validate_audit_report_passes_persisted_bundle(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _complete_manifest_fixture(config)
    manifest = generate_run_manifest(config, LIFECYCLE_RUN_ID).to_dict()
    report = generate_provenance_audit_report(config, manifest["run_manifest_id"]).to_dict()

    result = validate_audit_report(config, report["audit_report_id"]).to_dict()

    assert result["validation_status"] == "PASS"
    assert result["stored_audit_report_hash"] == report["audit_report_hash"]
    assert result["stored_payload_hash"] == report["audit_report_hash"]
    assert result["recomputed_audit_report_hash"] == report["audit_report_hash"]


def test_phase5b10_validate_audit_report_catches_payload_hash_drift(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _complete_manifest_fixture(config)
    manifest = generate_run_manifest(config, LIFECYCLE_RUN_ID).to_dict()
    report = generate_provenance_audit_report(config, manifest["run_manifest_id"]).to_dict()
    with connect_database(config.database.path) as connection:
        payload = json.loads(
            connection.execute(
                "SELECT audit_payload_json FROM audit_reports WHERE audit_report_id = ?",
                [report["audit_report_id"]],
            ).fetchone()[0]
        )
        payload["source_signal_provenance"]["source_signal_config_hash"] = "tampered"
        connection.execute(
            """
            UPDATE audit_reports
            SET audit_payload_json = ?
            WHERE audit_report_id = ?
            """,
            [json.dumps(payload, sort_keys=True), report["audit_report_id"]],
        )

    result = validate_audit_report(config, report["audit_report_id"]).to_dict()

    assert result["validation_status"] == "FAIL"
    assert any(
        "AUDIT_REPORT_PAYLOAD_HASH_MISMATCH" in error
        for error in result["validation_errors"]
    )


def test_phase5b10_validate_audit_report_catches_source_hash_drift(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _complete_manifest_fixture(config)
    manifest = generate_run_manifest(config, LIFECYCLE_RUN_ID).to_dict()
    report = generate_provenance_audit_report(config, manifest["run_manifest_id"]).to_dict()
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            UPDATE lifecycle_qa
            SET baseline_rows_count = 99
            WHERE lifecycle_run_id = ?
            """,
            [LIFECYCLE_RUN_ID],
        )

    result = validate_audit_report(config, report["audit_report_id"]).to_dict()

    assert result["validation_status"] == "FAIL"
    assert any(
        "AUDIT_REPORT_SOURCE_HASH_MISMATCH" in error
        for error in result["validation_errors"]
    )


def test_phase5b10_audit_report_validate_cli_has_no_formal_metric_terms(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _complete_manifest_fixture(config)
    manifest = generate_run_manifest(config, LIFECYCLE_RUN_ID).to_dict()
    report = generate_provenance_audit_report(config, manifest["run_manifest_id"]).to_dict()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"database:\n  path: {config.database.path}\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "audit-report-validate",
            "--audit-report-id",
            report["audit_report_id"],
            "--config",
            str(config_path),
        ],
    )
    output = result.stdout.lower()

    assert result.exit_code == 0
    for forbidden in (
        "cagr",
        "sharpe",
        "max drawdown",
        "annual return",
        "annual returns",
        "win_rate",
        "win rate",
        "profit_factor",
        "profit factor",
        "expectancy",
        "edge claim",
        "claim edge",
    ):
        assert forbidden not in output


def test_phase5b11_reproducibility_check_passes_complete_chain(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    source_hash, price_hash, input_hash = _complete_manifest_fixture(config)
    manifest = generate_run_manifest(config, LIFECYCLE_RUN_ID).to_dict()

    result = run_reproducibility_check(config, manifest["run_manifest_id"]).to_dict()

    assert result["validation_status"] == "PASS"
    assert result["failure_reasons"] == []
    assert result["manifest_validation_status"] == "PASS"
    assert result["audit_exported"] is True
    assert result["audit_completeness_status"] == "PASS"
    assert result["audit_report_validation_status"] == "PASS"
    assert result["price_snapshot_rows_hash"] == price_hash
    assert result["lifecycle_input_snapshot_rows_hash"] == input_hash
    assert result["audit_report_hash"]


def test_phase5b11_reproducibility_check_fails_manifest_drift(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _complete_manifest_fixture(config)
    manifest = generate_run_manifest(config, LIFECYCLE_RUN_ID).to_dict()
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            UPDATE execution_decisions
            SET chosen_provider = 'changed'
            WHERE execution_run_id = ?
            """,
            [EXECUTION_RUN_ID],
        )

    result = run_reproducibility_check(config, manifest["run_manifest_id"]).to_dict()

    assert result["validation_status"] == "FAIL"
    assert "MANIFEST_VALIDATION_FAIL" in result["failure_reasons"]
    assert "AUDIT_EXPORT_BLOCKED" in result["failure_reasons"]


def test_phase5b11_reproducibility_check_fails_missing_lifecycle_qa(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _complete_manifest_fixture(config)
    manifest = generate_run_manifest(config, LIFECYCLE_RUN_ID).to_dict()
    with connect_database(config.database.path) as connection:
        connection.execute("DELETE FROM lifecycle_qa WHERE lifecycle_run_id = ?", [LIFECYCLE_RUN_ID])

    result = run_reproducibility_check(config, manifest["run_manifest_id"]).to_dict()

    assert result["validation_status"] == "FAIL"
    assert "AUDIT_COMPLETENESS_FAIL" in result["failure_reasons"]
    assert "MISSING_LIFECYCLE_QA" in result["audit_completeness_warnings"]


def test_phase5b11_reproducibility_check_fails_audit_hash_drift(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _complete_manifest_fixture(config)
    manifest = generate_run_manifest(config, LIFECYCLE_RUN_ID).to_dict()
    report = generate_provenance_audit_report(config, manifest["run_manifest_id"]).to_dict()
    with connect_database(config.database.path) as connection:
        payload = json.loads(
            connection.execute(
                "SELECT audit_payload_json FROM audit_reports WHERE audit_report_id = ?",
                [report["audit_report_id"]],
            ).fetchone()[0]
        )
        payload["manifest_metadata"]["config_hash"] = "tampered"
        connection.execute(
            """
            UPDATE audit_reports
            SET audit_payload_json = ?
            WHERE audit_report_id = ?
            """,
            [json.dumps(payload, sort_keys=True), report["audit_report_id"]],
        )

    result = run_reproducibility_check(
        config,
        manifest["run_manifest_id"],
        audit_report_id=report["audit_report_id"],
    ).to_dict()

    assert result["validation_status"] == "FAIL"
    assert "AUDIT_REPORT_VALIDATION_FAIL" in result["failure_reasons"]
    assert any(
        "AUDIT_REPORT_PAYLOAD_HASH_MISMATCH" in error
        for error in result["audit_report_validation_errors"]
    )


def test_phase5b11_reproducibility_check_rejects_mismatched_audit_report(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _complete_manifest_fixture(config)
    manifest_a = generate_run_manifest(config, LIFECYCLE_RUN_ID).to_dict()
    manifest_b_id = "cloned-manifest-b"
    _clone_run_manifest(config, manifest_a["run_manifest_id"], manifest_b_id)
    report_b = generate_provenance_audit_report(config, manifest_b_id).to_dict()

    result = run_reproducibility_check(
        config,
        manifest_a["run_manifest_id"],
        audit_report_id=report_b["audit_report_id"],
    ).to_dict()

    assert result["validation_status"] == "FAIL"
    assert "AUDIT_REPORT_MANIFEST_MISMATCH" in result["failure_reasons"]
    assert (
        "AUDIT_REPORT_HASH_DOES_NOT_MATCH_CURRENT_MANIFEST_AUDIT"
        in result["failure_reasons"]
    )


def test_phase5b11_reproducibility_check_cli_rejects_mismatched_audit_report(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _complete_manifest_fixture(config)
    manifest_a = generate_run_manifest(config, LIFECYCLE_RUN_ID).to_dict()
    manifest_b_id = "cloned-manifest-b"
    _clone_run_manifest(config, manifest_a["run_manifest_id"], manifest_b_id)
    report_b = generate_provenance_audit_report(config, manifest_b_id).to_dict()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"database:\n  path: {config.database.path}\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "reproducibility-check",
            "--run-manifest-id",
            manifest_a["run_manifest_id"],
            "--audit-report-id",
            report_b["audit_report_id"],
            "--config",
            str(config_path),
        ],
    )
    payload = json.loads(result.stdout)

    assert result.exit_code == 1
    assert payload["validation_status"] == "FAIL"
    assert "AUDIT_REPORT_MANIFEST_MISMATCH" in payload["failure_reasons"]


def test_phase5b11_reproducibility_check_cli_has_no_formal_metric_terms(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _complete_manifest_fixture(config)
    manifest = generate_run_manifest(config, LIFECYCLE_RUN_ID).to_dict()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"database:\n  path: {config.database.path}\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "reproducibility-check",
            "--run-manifest-id",
            manifest["run_manifest_id"],
            "--config",
            str(config_path),
        ],
    )
    output = result.stdout.lower()
    payload = json.loads(result.stdout)

    assert result.exit_code == 0
    assert payload["validation_status"] == "PASS"
    for forbidden in (
        "cagr",
        "sharpe",
        "max drawdown",
        "annual return",
        "annual returns",
        "win_rate",
        "win rate",
        "profit_factor",
        "profit factor",
        "expectancy",
        "edge claim",
        "claim edge",
    ):
        assert forbidden not in output
