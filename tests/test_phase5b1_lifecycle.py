from __future__ import annotations

import inspect
import json
from datetime import date
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from sectorscout.cli import app
from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database, initialize_database
from sectorscout.lifecycle import generate_position_lifecycle
import sectorscout.lifecycle as lifecycle_module
from sectorscout.lifecycle_inputs import (
    LifecycleInputSnapshotValidationError,
    create_frozen_lifecycle_input_snapshot,
)


ENTRY = date(2024, 12, 2)
RUN_ID = "execution-run"


def _config(tmp_path: Path, **overrides) -> SectorScoutConfig:
    raw = {"database": {"path": tmp_path / "test.duckdb"}}
    raw.update(overrides)
    config = SectorScoutConfig.model_validate(raw)
    initialize_database(config)
    return config


def _insert_accepted_execution(
    config: SectorScoutConfig,
    *,
    symbol: str = "TEST",
    theme_id: str = "ai-memory",
    entry_date: date = ENTRY,
    entry_price: float = 100.0,
    stop_loss: float = 90.0,
    risk_per_share: float = 10.0,
    execution_run_id: str = RUN_ID,
    decision: str = "SIMULATED_NEXT_OPEN_ACCEPTED",
    execution_data_quality_pass: bool = True,
    execution_rule_pass: bool = True,
    risk_rule_pass: bool = True,
) -> None:
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            INSERT INTO execution_decisions (
                execution_run_id, asof_date, symbol, theme_id, setup_type, execution_model,
                decision, reject_reason, signal_entry_trigger, signal_stop_loss,
                next_session_date, chosen_provider, actual_entry_price,
                actual_stop_loss, risk_per_share, target_2r, target_3r,
                reward_risk, max_entry_extension_pct, max_initial_stop_pct,
                require_next_open_above_trigger, max_entry_drop_below_trigger_pct,
                execution_price_available, execution_data_quality_pass,
                execution_rule_pass, risk_rule_pass,
                source_signal_generated_at_utc, source_signal_config_hash,
                source_signal_git_commit, source_signal_data_snapshot_id,
                source_universe_version, source_theme_version,
                execution_generated_at_utc, execution_config_hash,
                execution_git_commit, execution_data_snapshot_id
            ) VALUES (
                ?, DATE '2024-11-29', ?, ?, 'VCP', 'next_open',
                ?, NULL, ?, ?,
                ?, 'FMP', ?, ?, ?, ?, ?,
                2.0, 0.05, 0.12,
                false, 0.02,
                true, ?,
                ?, ?,
                '2024-11-29T18:00:00+00:00', 'signal_hash',
                'signal_commit', 'signal_snapshot',
                'signal_universe', 'signal_theme',
                '2024-11-29T18:01:00+00:00', 'execution_hash',
                'execution_commit', 'execution_snapshot'
            )
            """,
            [
                execution_run_id,
                symbol,
                theme_id,
                decision,
                entry_price,
                stop_loss,
                entry_date,
                entry_price,
                stop_loss,
                risk_per_share,
                entry_price + 2 * risk_per_share,
                entry_price + 3 * risk_per_share,
                execution_data_quality_pass,
                execution_rule_pass,
                risk_rule_pass,
            ],
        )


def _insert_execution_run_context(
    config: SectorScoutConfig,
    execution_run_id: str = RUN_ID,
    *,
    mixed_source_signal_metadata: bool = False,
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
                created_at_utc
            ) VALUES (
                ?, DATE '2024-11-29', 'next_open',
                '2024-11-29T18:01:00+00:00', 'hash',
                'commit', 'snapshot', 'snapshot', 'hash',
                'commit', 'universe', 'theme', ?,
                '2024-11-29T18:01:00+00:00'
            )
            """,
            [execution_run_id, mixed_source_signal_metadata],
        )


def _insert_prices(
    config: SectorScoutConfig,
    symbol: str,
    rows: list[tuple[date, float, float, float, float]],
    *,
    provider: str = "FMP",
) -> None:
    with connect_database(config.database.path) as connection:
        for row_date, open_, high, low, close in rows:
            connection.execute(
                """
                INSERT INTO daily_prices (
                    symbol, price_date, open, high, low, close, volume,
                    adj_open, adj_high, adj_low, adj_close, adj_volume,
                    provider, is_adjusted, adjustment_warning, ingested_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, 1000000, ?, ?, ?, ?, 1000000, ?, true, false, now())
                """,
                [
                    symbol,
                    row_date,
                    open_,
                    high,
                    low,
                    close,
                    open_,
                    high,
                    low,
                    close,
                    provider,
                ],
            )


def _insert_market_regime(
    config: SectorScoutConfig,
    row_date: date,
    *,
    config_hash: str = "hash",
    git_commit: str = "commit",
    data_snapshot_id: str = "snapshot",
    universe_version: str = "universe",
    theme_version: str = "theme",
    risk_state: str = "RISK_ON",
) -> None:
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            INSERT INTO market_regime (
                asof_date, spy_stage, qqq_stage, spy_above_50dma, spy_above_200dma,
                qqq_above_50dma, qqq_above_200dma, pct_universe_above_50dma,
                pct_universe_above_200dma, pct_universe_stage2, risk_state,
                signal_generated_at_utc, config_hash, git_commit, data_snapshot_id,
                universe_version, theme_version
            ) VALUES (
                ?, 'Stage 2', 'Stage 2', true, true,
                true, true, 0.8, 0.8, 0.5, ?,
                '2024-12-03T21:00:00+00:00', ?, ?, ?,
                ?, ?
            )
            """,
            [
                row_date,
                risk_state,
                config_hash,
                git_commit,
                data_snapshot_id,
                universe_version,
                theme_version,
            ],
        )


def _insert_theme_score(
    config: SectorScoutConfig,
    row_date: date,
    *,
    theme_score: float = 60.0,
    config_hash: str = "hash",
    git_commit: str = "commit",
    data_snapshot_id: str = "snapshot",
    universe_version: str = "universe",
    theme_version: str = "theme",
) -> None:
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            INSERT INTO theme_scores (
                asof_date, theme_id, theme_score, technical_relative_strength,
                breadth, fundamental_acceleration, catalyst_score,
                risk_valuation_penalty, component_coverage_pct, members_count,
                raw_members_count, eligible_members_count, excluded_members_count,
                technical_coverage_pct, theme_fundamental_coverage_pct,
                members_with_valid_fundamentals, signal_generated_at_utc,
                config_hash, git_commit, data_snapshot_id, universe_version,
                theme_version
            ) VALUES (
                ?, 'ai-memory', ?, 40, 40, 40, 0, 0, 1, 1,
                1, 1, 0, 1, 1, 1, '2024-12-02T21:00:00+00:00',
                ?, ?, ?, ?, ?
            )
            """,
            [
                row_date,
                theme_score,
                config_hash,
                git_commit,
                data_snapshot_id,
                universe_version,
                theme_version,
            ],
        )


def _position(config: SectorScoutConfig, through: date, *, run_id: str = RUN_ID) -> dict:
    return generate_position_lifecycle(config, run_id, through).to_dict()["positions"][0]


def test_phase5b1_skips_when_through_date_is_before_entry_date(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_accepted_execution(config)

    result = generate_position_lifecycle(config, RUN_ID, date(2024, 11, 29)).to_dict()

    assert result["positions"] == []
    assert result["qa_summary"]["skipped_count"] == 1
    assert result["skipped_executions"][0]["skip_reason"] == "THROUGH_DATE_BEFORE_ENTRY_DATE"


def test_phase5b1_skips_missing_price_path(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_accepted_execution(config)

    result = generate_position_lifecycle(config, RUN_ID, date(2024, 12, 3)).to_dict()

    assert result["positions"] == []
    assert result["qa_summary"]["missing_price_path_count"] == 1
    assert result["skipped_executions"][0]["skip_reason"] == "MISSING_PRICE_PATH"
    with connect_database(config.database.path) as connection:
        persisted = connection.execute("SELECT skip_reason FROM lifecycle_skips").fetchone()
    assert persisted == ("MISSING_PRICE_PATH",)


def test_phase5b1_skips_missing_entry_session_price(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_accepted_execution(config)
    _insert_prices(config, "TEST", [(date(2024, 12, 3), 101.0, 103.0, 100.0, 102.0)])

    result = generate_position_lifecycle(config, RUN_ID, date(2024, 12, 3)).to_dict()

    assert result["positions"] == []
    assert result["qa_summary"]["missing_entry_session_price_count"] == 1
    assert result["skipped_executions"][0]["skip_reason"] == "MISSING_ENTRY_SESSION_PRICE"


def test_phase5b5_lifecycle_input_qa_records_matching_non_price_metadata(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _insert_execution_run_context(config)
    _insert_accepted_execution(config)
    _insert_prices(
        config,
        "TEST",
        [
            (ENTRY, 100.0, 102.0, 99.0, 101.0),
            (date(2024, 12, 3), 101.0, 102.0, 100.0, 101.0),
        ],
    )
    _insert_market_regime(config, ENTRY)
    _insert_market_regime(config, date(2024, 12, 3))
    _insert_theme_score(config, ENTRY)
    _insert_theme_score(config, date(2024, 12, 3))

    result = generate_position_lifecycle(config, RUN_ID, date(2024, 12, 3)).to_dict()

    assert result["input_qa"]["theme_score_rows"] == 2
    assert result["input_qa"]["market_regime_rows"] == 2
    assert result["input_qa"]["missing_market_regime_sessions"] == []
    assert result["input_qa"]["missing_theme_score_keys"] == []
    assert result["input_qa"]["non_price_input_snapshot_warning"] is False
    with connect_database(config.database.path) as connection:
        row = connection.execute(
            """
            SELECT non_price_input_snapshot_warning,
                   market_regime_data_snapshot_ids_json,
                   theme_score_theme_versions_json
            FROM lifecycle_input_qa
            """
        ).fetchone()
    assert row == (False, '["snapshot"]', '["theme"]')


def test_phase5b5_lifecycle_input_qa_flags_market_regime_source_mismatch(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _insert_execution_run_context(config)
    _insert_accepted_execution(config)
    _insert_prices(config, "TEST", [(ENTRY, 100.0, 102.0, 99.0, 101.0)])
    _insert_market_regime(config, ENTRY, data_snapshot_id="other_snapshot")

    result = generate_position_lifecycle(config, RUN_ID, ENTRY).to_dict()

    assert result["input_qa"]["market_regime_source_mismatch_warning"] is True
    assert result["input_qa"]["non_price_input_snapshot_warning"] is True


def test_phase5b5_lifecycle_input_qa_flags_theme_score_source_mismatch(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _insert_execution_run_context(config)
    _insert_accepted_execution(config)
    _insert_prices(config, "TEST", [(ENTRY, 100.0, 102.0, 99.0, 101.0)])
    _insert_theme_score(config, ENTRY, theme_version="other_theme")

    result = generate_position_lifecycle(config, RUN_ID, ENTRY).to_dict()

    assert result["input_qa"]["theme_score_source_mismatch_warning"] is True
    assert result["input_qa"]["theme_score_theme_versions"] == ["other_theme"]


def test_phase5b6_lifecycle_input_qa_flags_missing_market_regime_coverage(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _insert_execution_run_context(config)
    _insert_accepted_execution(config)
    _insert_prices(config, "TEST", [(ENTRY, 100.0, 102.0, 99.0, 101.0)])
    _insert_theme_score(config, ENTRY)

    result = generate_position_lifecycle(config, RUN_ID, ENTRY).to_dict()

    assert result["input_qa"]["market_regime_rows"] == 0
    assert result["input_qa"]["missing_market_regime_coverage_warning"] is True
    assert result["input_qa"]["missing_market_regime_sessions"] == ["2024-12-02"]
    assert result["input_qa"]["non_price_input_snapshot_warning"] is True


def test_phase5b6_lifecycle_input_qa_flags_missing_theme_score_coverage(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _insert_execution_run_context(config)
    _insert_accepted_execution(config)
    _insert_prices(config, "TEST", [(ENTRY, 100.0, 102.0, 99.0, 101.0)])
    _insert_market_regime(config, ENTRY)

    result = generate_position_lifecycle(config, RUN_ID, ENTRY).to_dict()

    assert result["input_qa"]["theme_score_rows"] == 0
    assert result["input_qa"]["missing_theme_score_coverage_warning"] is True
    assert result["input_qa"]["missing_theme_score_keys"] == ["ai-memory:2024-12-02"]
    assert result["input_qa"]["non_price_input_snapshot_warning"] is True


def test_phase5b6_lifecycle_input_qa_flags_mixed_source_metadata(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _insert_execution_run_context(config, mixed_source_signal_metadata=True)
    _insert_accepted_execution(config)
    _insert_prices(config, "TEST", [(ENTRY, 100.0, 102.0, 99.0, 101.0)])
    _insert_market_regime(config, ENTRY)
    _insert_theme_score(config, ENTRY)

    result = generate_position_lifecycle(config, RUN_ID, ENTRY).to_dict()

    assert result["input_qa"]["mixed_source_signal_metadata_warning"] is True
    assert result["input_qa"]["non_price_input_snapshot_warning"] is True


def test_phase5b7_lifecycle_uses_frozen_non_price_snapshot_for_risk_off(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _insert_execution_run_context(config)
    _insert_accepted_execution(config)
    for day in [ENTRY, date(2024, 12, 3), date(2024, 12, 4)]:
        _insert_theme_score(config, day)
    _insert_market_regime(config, ENTRY)
    _insert_market_regime(config, date(2024, 12, 3), risk_state="RISK_OFF")
    _insert_market_regime(config, date(2024, 12, 4))
    _insert_prices(
        config,
        "TEST",
        [
            (ENTRY, 100.0, 102.0, 99.0, 101.0),
            (date(2024, 12, 3), 101.0, 103.0, 100.0, 102.0),
            (date(2024, 12, 4), 102.0, 104.0, 101.0, 103.0),
        ],
    )
    snapshot = create_frozen_lifecycle_input_snapshot(
        config,
        RUN_ID,
        date(2024, 12, 4),
    )
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            UPDATE market_regime
            SET risk_state = 'RISK_ON'
            WHERE asof_date = DATE '2024-12-03'
            """
        )

    result = generate_position_lifecycle(
        config,
        RUN_ID,
        date(2024, 12, 4),
        lifecycle_input_snapshot_id=snapshot.lifecycle_input_snapshot_id,
    ).to_dict()

    assert result["positions"][0]["exit_reason"] == "RISK_OFF_POLICY_PLACEHOLDER"
    assert result["input_qa"]["non_price_input_mode"] == "persisted_lifecycle_input_snapshot"
    assert result["input_qa"]["lifecycle_input_snapshot_id"] == snapshot.lifecycle_input_snapshot_id
    assert result["input_qa"]["lifecycle_input_snapshot_rows_hash"] == snapshot.snapshot_rows_hash
    with connect_database(config.database.path) as connection:
        persisted = connection.execute(
            """
            SELECT lifecycle_input_snapshot_id
            FROM lifecycle_runs
            """
        ).fetchone()
    assert persisted == (snapshot.lifecycle_input_snapshot_id,)


def test_phase5b7_lifecycle_input_snapshot_fails_on_missing_market_session(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _insert_execution_run_context(config)
    _insert_accepted_execution(config)
    _insert_prices(config, "TEST", [(ENTRY, 100.0, 102.0, 99.0, 101.0)])
    _insert_theme_score(config, ENTRY)
    snapshot = create_frozen_lifecycle_input_snapshot(config, RUN_ID, ENTRY)

    try:
        generate_position_lifecycle(
            config,
            RUN_ID,
            ENTRY,
            lifecycle_input_snapshot_id=snapshot.lifecycle_input_snapshot_id,
        )
    except LifecycleInputSnapshotValidationError as exc:
        assert "LIFECYCLE_INPUT_SNAPSHOT_MISSING_MARKET_REGIME_SESSIONS" in str(exc)
    else:
        raise AssertionError("Expected missing market-regime snapshot validation failure")


def test_phase5b7_lifecycle_input_snapshot_fails_on_missing_theme_key(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _insert_execution_run_context(config)
    _insert_accepted_execution(config)
    _insert_prices(config, "TEST", [(ENTRY, 100.0, 102.0, 99.0, 101.0)])
    _insert_market_regime(config, ENTRY)
    snapshot = create_frozen_lifecycle_input_snapshot(config, RUN_ID, ENTRY)

    try:
        generate_position_lifecycle(
            config,
            RUN_ID,
            ENTRY,
            lifecycle_input_snapshot_id=snapshot.lifecycle_input_snapshot_id,
        )
    except LifecycleInputSnapshotValidationError as exc:
        assert "LIFECYCLE_INPUT_SNAPSHOT_MISSING_THEME_SCORE_KEYS" in str(exc)
    else:
        raise AssertionError("Expected missing theme-score snapshot validation failure")


def test_phase5b7_lifecycle_input_snapshot_unknown_id_fails_fast(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _insert_execution_run_context(config)
    _insert_accepted_execution(config)

    try:
        generate_position_lifecycle(
            config,
            RUN_ID,
            ENTRY,
            lifecycle_input_snapshot_id="missing-snapshot",
        )
    except LifecycleInputSnapshotValidationError as exc:
        assert "UNKNOWN_LIFECYCLE_INPUT_SNAPSHOT_ID" in str(exc)
    else:
        raise AssertionError("Expected unknown lifecycle input snapshot validation failure")


def test_phase5b7_lifecycle_input_snapshot_hash_mismatch_fails_fast(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _insert_execution_run_context(config)
    _insert_accepted_execution(config)
    _insert_prices(config, "TEST", [(ENTRY, 100.0, 102.0, 99.0, 101.0)])
    _insert_market_regime(config, ENTRY)
    _insert_theme_score(config, ENTRY)
    snapshot = create_frozen_lifecycle_input_snapshot(config, RUN_ID, ENTRY)
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            UPDATE lifecycle_market_regime_snapshot_rows
            SET risk_state = 'RISK_OFF'
            WHERE lifecycle_input_snapshot_id = ?
            """,
            [snapshot.lifecycle_input_snapshot_id],
        )

    try:
        generate_position_lifecycle(
            config,
            RUN_ID,
            ENTRY,
            lifecycle_input_snapshot_id=snapshot.lifecycle_input_snapshot_id,
        )
    except LifecycleInputSnapshotValidationError as exc:
        assert "LIFECYCLE_INPUT_SNAPSHOT_HASH_MISMATCH" in str(exc)
    else:
        raise AssertionError("Expected lifecycle input snapshot hash mismatch")


def test_phase5b7_lifecycle_input_snapshot_source_mismatch_fails_fast(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _insert_execution_run_context(config)
    _insert_accepted_execution(config)
    _insert_prices(config, "TEST", [(ENTRY, 100.0, 102.0, 99.0, 101.0)])
    _insert_market_regime(config, ENTRY, data_snapshot_id="other_snapshot")
    _insert_theme_score(config, ENTRY)
    snapshot = create_frozen_lifecycle_input_snapshot(config, RUN_ID, ENTRY)

    try:
        generate_position_lifecycle(
            config,
            RUN_ID,
            ENTRY,
            lifecycle_input_snapshot_id=snapshot.lifecycle_input_snapshot_id,
        )
    except LifecycleInputSnapshotValidationError as exc:
        assert "LIFECYCLE_INPUT_SNAPSHOT_SOURCE_MISMATCH" in str(exc)
    else:
        raise AssertionError("Expected lifecycle input source mismatch")


def test_phase5b7_lifecycle_input_snapshot_cli_output_has_no_formal_metric_terms(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _insert_execution_run_context(config)
    _insert_accepted_execution(config)
    _insert_market_regime(config, ENTRY)
    _insert_theme_score(config, ENTRY)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"database:\n  path: {config.database.path}\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "lifecycle-input-snapshot",
            "--execution-run-id",
            RUN_ID,
            "--through",
            ENTRY.isoformat(),
            "--no-persist",
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert "lifecycle_input_snapshot_id" in payload
    output = result.stdout.lower()
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


def test_phase5b1_gap_down_stop_at_open_exit(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_accepted_execution(config)
    _insert_prices(
        config,
        "TEST",
        [
            (ENTRY, 100.0, 102.0, 99.0, 101.0),
            (date(2024, 12, 3), 89.0, 90.0, 88.0, 89.0),
        ],
    )

    position = _position(config, date(2024, 12, 3))

    assert position["status"] == "CLOSED"
    assert position["exit_reason"] == "GAP_DOWN_STOP_AT_OPEN"
    assert position["exit_price"] == 89.0


def test_phase5b1_hard_stop_intraday_exit(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_accepted_execution(config)
    _insert_prices(
        config,
        "TEST",
        [
            (ENTRY, 100.0, 102.0, 99.0, 101.0),
            (date(2024, 12, 3), 95.0, 96.0, 89.0, 94.0),
        ],
    )

    position = _position(config, date(2024, 12, 3))

    assert position["exit_reason"] == "HARD_STOP_INTRADAY"
    assert position["exit_price"] == 90.0


def test_phase5b1_time_stop_no_1r_within_20_days_exit(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_accepted_execution(config)
    dates = [stamp.date() for stamp in pd.bdate_range(start=ENTRY, periods=21)]
    _insert_prices(config, "TEST", [(day, 100.0, 105.0, 95.0, 101.0) for day in dates])

    position = _position(config, dates[-1])

    assert position["exit_reason"] == "TIME_STOP_NO_1R_WITHIN_20D"
    assert position["exit_date"] == dates[-1].isoformat()
    assert position["exit_price"] == 100.0


def test_phase5b1_trend_failure_close_below_50sma_exit(tmp_path: Path) -> None:
    config = _config(tmp_path)
    dates = [stamp.date() for stamp in pd.bdate_range(end=date(2024, 12, 3), periods=52)]
    entry_date = dates[50]
    _insert_accepted_execution(
        config,
        entry_date=entry_date,
        stop_loss=70.0,
        risk_per_share=30.0,
    )
    rows = []
    for day in dates[:50]:
        rows.append((day, 100.0, 102.0, 98.0, 100.0))
    rows.append((entry_date, 100.0, 102.0, 79.0, 80.0))
    rows.append((dates[51], 81.0, 82.0, 80.0, 81.0))
    _insert_prices(config, "TEST", rows)

    position = _position(config, dates[-1])

    assert position["exit_reason"] == "TREND_FAILURE_CLOSE_BELOW_50SMA"
    assert position["exit_date"] == dates[51].isoformat()
    assert position["exit_price"] == 81.0


def test_phase5b1_same_day_hard_stop_wins_over_trend_failure(tmp_path: Path) -> None:
    config = _config(tmp_path)
    dates = [stamp.date() for stamp in pd.bdate_range(end=date(2024, 12, 3), periods=52)]
    entry_date = dates[50]
    _insert_accepted_execution(
        config,
        entry_date=entry_date,
        stop_loss=70.0,
        risk_per_share=30.0,
    )
    rows = [(day, 100.0, 102.0, 98.0, 100.0) for day in dates[:50]]
    rows.append((entry_date, 100.0, 102.0, 95.0, 80.0))
    rows.append((dates[51], 95.0, 96.0, 69.0, 94.0))
    _insert_prices(config, "TEST", rows)

    position = _position(config, dates[-1])

    assert position["exit_reason"] == "HARD_STOP_INTRADAY"
    assert position["exit_date"] == dates[51].isoformat()
    assert position["exit_price"] == 70.0


def test_phase5b1_gap_down_stop_wins_over_intraday_stop(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_accepted_execution(config)
    _insert_prices(
        config,
        "TEST",
        [(ENTRY, 89.0, 90.0, 80.0, 85.0)],
    )

    position = _position(config, ENTRY)

    assert position["exit_reason"] == "GAP_DOWN_STOP_AT_OPEN"
    assert position["exit_price"] == 89.0


def test_phase5b1_risk_off_policy_placeholder_exit(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_accepted_execution(config)
    _insert_prices(
        config,
        "TEST",
        [
            (ENTRY, 100.0, 102.0, 99.0, 101.0),
            (date(2024, 12, 3), 101.0, 102.0, 100.0, 101.0),
            (date(2024, 12, 4), 100.0, 101.0, 99.0, 100.0),
        ],
    )
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            INSERT INTO market_regime (
                asof_date, spy_stage, qqq_stage, spy_above_50dma, spy_above_200dma,
                qqq_above_50dma, qqq_above_200dma, pct_universe_above_50dma,
                pct_universe_above_200dma, pct_universe_stage2, risk_state,
                signal_generated_at_utc, config_hash, git_commit, data_snapshot_id,
                universe_version, theme_version
            ) VALUES (
                DATE '2024-12-03', 'Stage 4', 'Stage 4', false, false,
                false, false, 0, 0, 0, 'RISK_OFF',
                '2024-12-03T21:00:00+00:00', 'hash', 'commit', 'snapshot',
                'universe', 'theme'
            )
            """
        )

    position = _position(config, date(2024, 12, 4))

    assert position["exit_reason"] == "RISK_OFF_POLICY_PLACEHOLDER"
    assert position["exit_date"] == "2024-12-04"
    assert position["exit_price"] == 100.0


def test_phase5b1_theme_score_deterioration_placeholder_exit(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_accepted_execution(config)
    dates = [stamp.date() for stamp in pd.bdate_range(start=ENTRY, periods=6)]
    _insert_prices(config, "TEST", [(day, 100.0, 102.0, 99.0, 101.0) for day in dates])
    with connect_database(config.database.path) as connection:
        for day in dates[:5]:
            connection.execute(
                """
                INSERT INTO theme_scores (
                    asof_date, theme_id, theme_score, technical_relative_strength,
                    breadth, fundamental_acceleration, catalyst_score,
                    risk_valuation_penalty, component_coverage_pct, members_count,
                    raw_members_count, eligible_members_count, excluded_members_count,
                    technical_coverage_pct, theme_fundamental_coverage_pct,
                    members_with_valid_fundamentals, signal_generated_at_utc,
                    config_hash, git_commit, data_snapshot_id, universe_version,
                    theme_version
                ) VALUES (
                    ?, 'ai-memory', 40, 40, 40, 40, 0, 0, 1, 1,
                    1, 1, 0, 1, 1, 1, '2024-12-02T21:00:00+00:00',
                    'hash', 'commit', 'snapshot', 'universe', 'theme'
                )
                """,
                [day],
            )

    position = _position(config, dates[-1])

    assert position["exit_reason"] == "THEME_SCORE_DETERIORATION_PLACEHOLDER"
    assert position["exit_date"] == dates[-1].isoformat()


def test_phase5b1_theme_score_deterioration_requires_consecutive_sessions(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_accepted_execution(config)
    dates = [stamp.date() for stamp in pd.bdate_range(start=ENTRY, periods=6)]
    _insert_prices(config, "TEST", [(day, 100.0, 102.0, 99.0, 101.0) for day in dates])
    with connect_database(config.database.path) as connection:
        for day in [dates[0], dates[1], dates[2], dates[4], dates[5]]:
            connection.execute(
                """
                INSERT INTO theme_scores (
                    asof_date, theme_id, theme_score, technical_relative_strength,
                    breadth, fundamental_acceleration, catalyst_score,
                    risk_valuation_penalty, component_coverage_pct, members_count,
                    raw_members_count, eligible_members_count, excluded_members_count,
                    technical_coverage_pct, theme_fundamental_coverage_pct,
                    members_with_valid_fundamentals, signal_generated_at_utc,
                    config_hash, git_commit, data_snapshot_id, universe_version,
                    theme_version
                ) VALUES (
                    ?, 'ai-memory', 40, 40, 40, 40, 0, 0, 1, 1,
                    1, 1, 0, 1, 1, 1, '2024-12-02T21:00:00+00:00',
                    'hash', 'commit', 'snapshot', 'universe', 'theme'
                )
                """,
                [day],
            )

    position = _position(config, dates[-1])

    assert position["status"] == "OPEN"
    assert position["exit_reason"] is None


def test_phase5b1_persists_exit_decision_for_closed_position(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_accepted_execution(config)
    _insert_prices(
        config,
        "TEST",
        [
            (ENTRY, 100.0, 102.0, 99.0, 101.0),
            (date(2024, 12, 3), 95.0, 96.0, 89.0, 94.0),
        ],
    )

    result = generate_position_lifecycle(config, RUN_ID, date(2024, 12, 3)).to_dict()

    assert len(result["exit_decisions"]) == 1
    with connect_database(config.database.path) as connection:
        row = connection.execute(
            "SELECT status, exit_reason FROM simulated_positions"
        ).fetchone()
        exit_row = connection.execute(
            "SELECT exit_reason FROM exit_decisions"
        ).fetchone()
    assert row == ("CLOSED", "HARD_STOP_INTRADAY")
    assert exit_row == ("HARD_STOP_INTRADAY",)


def test_phase5b1_rejected_execution_decisions_do_not_enter_lifecycle(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_accepted_execution(config, decision="SIMULATED_NEXT_OPEN_REJECTED")
    _insert_prices(config, "TEST", [(ENTRY, 100.0, 102.0, 99.0, 101.0)])

    result = generate_position_lifecycle(config, RUN_ID, ENTRY).to_dict()

    assert result["qa_summary"]["accepted_execution_count"] == 0
    assert result["positions"] == []


def test_phase5b1_failed_execution_pass_flags_do_not_enter_lifecycle(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_accepted_execution(config, symbol="DATA", execution_data_quality_pass=False)
    _insert_accepted_execution(config, symbol="RULE", execution_rule_pass=False)
    _insert_accepted_execution(config, symbol="RISK", risk_rule_pass=False)
    for symbol in ("DATA", "RULE", "RISK"):
        _insert_prices(config, symbol, [(ENTRY, 100.0, 102.0, 99.0, 101.0)])

    result = generate_position_lifecycle(config, RUN_ID, ENTRY).to_dict()

    assert result["qa_summary"]["accepted_execution_count"] == 0
    assert result["positions"] == []


def test_phase5b1_lifecycle_uses_provider_priority_prices(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        providers={"prices_primary": "FMP", "prices_fallback": "yfinance"},
    )
    _insert_accepted_execution(config)
    _insert_prices(
        config,
        "TEST",
        [(ENTRY, 100.0, 102.0, 95.0, 101.0)],
        provider="FMP",
    )
    _insert_prices(
        config,
        "TEST",
        [(ENTRY, 100.0, 102.0, 80.0, 81.0)],
        provider="yfinance",
    )

    position = _position(config, ENTRY)

    assert position["status"] == "OPEN"
    assert position["exit_reason"] is None


def test_phase5b1_baseline_series_uses_provider_priority_prices(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        providers={"prices_primary": "FMP", "prices_fallback": "yfinance"},
    )
    _insert_accepted_execution(config)
    _insert_prices(config, "TEST", [(ENTRY, 100.0, 102.0, 99.0, 101.0)])
    _insert_prices(config, "SPY", [(ENTRY, 100.0, 101.0, 99.0, 100.0)], provider="FMP")
    _insert_prices(
        config,
        "SPY",
        [(ENTRY, 999.0, 1000.0, 998.0, 999.0)],
        provider="yfinance",
    )

    result = generate_position_lifecycle(config, RUN_ID, ENTRY).to_dict()

    assert result["qa_summary"]["baseline_rows_count"] == 1
    with connect_database(config.database.path) as connection:
        row = connection.execute(
            "SELECT provider, adj_close FROM baseline_price_series WHERE symbol = 'SPY'"
        ).fetchone()
    assert row == ("FMP", 100.0)


def test_phase5b1_cli_output_has_no_performance_terms(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_accepted_execution(config)
    _insert_prices(
        config,
        "TEST",
        [
            (ENTRY, 100.0, 102.0, 99.0, 101.0),
            (date(2024, 12, 3), 95.0, 96.0, 89.0, 94.0),
        ],
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"database:\n  path: {config.database.path}\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "position-lifecycle",
            "--execution-run-id",
            RUN_ID,
            "--through",
            "2024-12-03",
            "--no-persist",
            "--config",
            str(config_path),
        ],
    )
    output = result.stdout.lower()
    payload = json.loads(result.stdout)

    assert result.exit_code == 0
    assert "positions" in payload
    for forbidden in ("cagr", "sharpe", "drawdown", "win_rate", "profit_factor", "edge"):
        assert forbidden not in output


def test_phase5b1_lifecycle_path_is_read_only_from_execution_decisions() -> None:
    source = inspect.getsource(lifecycle_module)

    assert "FROM signals" not in source
    assert "run_scoring" not in source
    assert "detect_setups" not in source
