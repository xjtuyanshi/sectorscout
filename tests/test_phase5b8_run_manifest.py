from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from sectorscout.cli import app
from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database, initialize_database
from sectorscout.lifecycle_inputs import (
    MARKET_REGIME_COLUMNS,
    THEME_SCORE_COLUMNS,
    lifecycle_input_snapshot_rows_hash,
)
from sectorscout.prices import PRICE_SNAPSHOT_COLUMNS, snapshot_rows_hash
from sectorscout.run_manifest import generate_run_manifest, validate_run_manifest


EXECUTION_RUN_ID = "execution-run"
LIFECYCLE_RUN_ID = "lifecycle-run"
PRICE_SNAPSHOT_ID = "price-snapshot"
INPUT_SNAPSHOT_ID = "input-snapshot"


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
            }
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
                '{"FMP": 1}', 0, DATE '2024-12-02', DATE '2024-12-02',
                1, 1, ?, 'price_hash', 'price_commit',
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
                    row["adj_volume"],
                    row["provider"],
                    row["adjustment_warning"],
                ],
            )
    return rows_hash


def _insert_execution_and_lifecycle(
    config: SectorScoutConfig,
    *,
    price_snapshot_id: str | None = PRICE_SNAPSHOT_ID,
    lifecycle_input_snapshot_id: str | None = INPUT_SNAPSHOT_ID,
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


def _complete_manifest_fixture(config: SectorScoutConfig) -> tuple[str, str]:
    price_hash = _insert_price_snapshot(config)
    input_hash = _insert_lifecycle_input_snapshot(config)
    _insert_execution_and_lifecycle(config)
    return price_hash, input_hash


def test_phase5b8_run_manifest_persists_complete_provenance(tmp_path: Path) -> None:
    config = _config(tmp_path)
    price_hash, input_hash = _complete_manifest_fixture(config)

    result = generate_run_manifest(config, LIFECYCLE_RUN_ID).to_dict()

    assert result["validation_status"] == "PASS"
    assert result["source_signal_config_hash"] == "signal_hash"
    assert result["source_signal_snapshot_id"] == "signal_snapshot"
    assert result["price_snapshot_id"] == PRICE_SNAPSHOT_ID
    assert result["price_snapshot_rows_hash"] == price_hash
    assert result["lifecycle_input_snapshot_id"] == INPUT_SNAPSHOT_ID
    assert result["lifecycle_input_snapshot_rows_hash"] == input_hash
    assert len(result["execution_decision_rows_hash"]) == 64
    with connect_database(config.database.path) as connection:
        persisted = connection.execute(
            """
            SELECT validation_status, source_signal_config_hash,
                   price_snapshot_rows_hash, lifecycle_input_snapshot_rows_hash
            FROM run_manifests
            """
        ).fetchone()
    assert persisted == ("PASS", "signal_hash", price_hash, input_hash)


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
