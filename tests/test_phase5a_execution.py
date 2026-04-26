from __future__ import annotations

import inspect
import json
from datetime import date
from pathlib import Path

from typer.testing import CliRunner

from sectorscout.cli import app
from sectorscout.config import SectorScoutConfig, config_hash
from sectorscout.db import connect_database, initialize_database
from sectorscout.execution import generate_execution_decisions
import sectorscout.execution as execution_module


ASOF = date(2024, 11, 29)
NEXT_SESSION = date(2024, 12, 2)


def _config(tmp_path: Path, **overrides) -> SectorScoutConfig:
    raw = {"database": {"path": tmp_path / "test.duckdb"}}
    raw.update(overrides)
    config = SectorScoutConfig.model_validate(raw)
    initialize_database(config)
    return config


def _insert_signal(
    config: SectorScoutConfig,
    *,
    symbol: str = "MU",
    entry_trigger: float | None = 100.0,
    stop_loss: float | None = 95.0,
    state: str = "TRIGGERED",
    actionable: bool = False,
    setup_data_present: bool = True,
    execution_data_quality_pass: bool = False,
    data_quality_pass: bool = False,
    market_gate_pass: bool = True,
    portfolio_risk_pass: bool = True,
    source_signal_generated_at: str = "2024-11-29T18:00:00+00:00",
    source_config_hash: str = "signal_hash",
    source_git_commit: str = "signal_commit",
    source_data_snapshot_id: str = "signal_snapshot",
    source_universe_version: str = "signal_universe",
    source_theme_version: str = "signal_theme",
) -> None:
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            INSERT INTO signals (
                asof_date, symbol, theme_id, setup_type, state,
                action_category, actionable, reason, execution_model,
                entry_trigger, stop_loss, reward_risk, setup_data_present,
                execution_data_quality_pass, price_snapshot_quality_pass,
                data_quality_pass,
                data_quality_reason, market_gate_pass, market_gate_reason,
                market_regime_risk_state, portfolio_risk_pass,
                portfolio_risk_reason,
                signal_generated_at_utc, config_hash, git_commit,
                data_snapshot_id, universe_version, theme_version
            ) VALUES (
                ?, ?, 'ai-memory', 'VCP', ?,
                'Triggered setup candidate', ?, 'Phase 4 candidate', 'next_open',
                ?, ?, 2.0, ?,
                ?, true, ?,
                'Phase 5 execution validation required.', ?, 'Market gate pass.',
                'RISK_ON', ?, 'Portfolio gate pass.',
                ?, ?, ?,
                ?, ?, ?
            )
            """,
            [
                ASOF,
                symbol,
                state,
                actionable,
                entry_trigger,
                stop_loss,
                setup_data_present,
                execution_data_quality_pass,
                data_quality_pass,
                market_gate_pass,
                portfolio_risk_pass,
                source_signal_generated_at,
                source_config_hash,
                source_git_commit,
                source_data_snapshot_id,
                source_universe_version,
                source_theme_version,
            ],
        )


def _insert_next_open(
    config: SectorScoutConfig,
    *,
    symbol: str = "MU",
    open_price: float = 101.0,
    provider: str = "FMP",
) -> None:
    with connect_database(config.database.path) as connection:
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
                NEXT_SESSION,
                open_price,
                open_price + 1,
                open_price - 1,
                open_price,
                open_price,
                open_price + 1,
                open_price - 1,
                open_price,
                provider,
            ],
        )


def test_phase5a_next_open_execution_records_normal_fill(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_signal(config)
    _insert_next_open(config, open_price=101.0)

    result = generate_execution_decisions(config, ASOF).to_dict()
    decision = result["decisions"][0]

    assert result["performance_metrics"] == {}
    assert "not broker fills" in result["warning"]
    assert decision["decision"] == "SIMULATED_NEXT_OPEN_ACCEPTED"
    assert decision["next_session_date"] == "2024-12-02"
    assert decision["chosen_provider"] == "FMP"
    assert decision["actual_entry_price"] == 101.0
    assert decision["actual_stop_loss"] == 95.0
    assert decision["risk_per_share"] == 6.0
    assert decision["target_2r"] == 113.0
    assert decision["target_3r"] == 119.0
    assert decision["reward_risk"] == 2.0
    assert decision["execution_price_available"] is True
    assert decision["execution_data_quality_pass"] is True
    assert decision["execution_rule_pass"] is True
    assert decision["risk_rule_pass"] is True
    with connect_database(config.database.path) as connection:
        persisted = connection.execute(
            "SELECT decision, actual_entry_price, source_signal_config_hash FROM execution_decisions"
        ).fetchone()
    assert persisted == ("SIMULATED_NEXT_OPEN_ACCEPTED", 101.0, "signal_hash")


def test_phase5a_rejects_gap_too_extended(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_signal(config)
    _insert_next_open(config, open_price=106.0)

    decision = generate_execution_decisions(config, ASOF).to_dict()["decisions"][0]

    assert decision["decision"] == "SIMULATED_NEXT_OPEN_REJECTED"
    assert decision["reject_reason"] == "ENTRY_EXTENSION_TOO_HIGH"
    assert decision["execution_price_available"] is True
    assert decision["execution_data_quality_pass"] is True
    assert decision["execution_rule_pass"] is False
    assert decision["risk_rule_pass"] is True


def test_phase5a_rejects_missing_next_open_price(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_signal(config)

    decision = generate_execution_decisions(config, ASOF).to_dict()["decisions"][0]

    assert decision["decision"] == "SIMULATED_NEXT_OPEN_REJECTED"
    assert decision["reject_reason"] == "MISSING_NEXT_OPEN"
    assert decision["actual_entry_price"] is None
    assert decision["execution_price_available"] is False
    assert decision["execution_data_quality_pass"] is False


def test_phase5a_rejects_stop_at_or_above_entry(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_signal(config, stop_loss=102.0)
    _insert_next_open(config, open_price=101.0)

    decision = generate_execution_decisions(config, ASOF).to_dict()["decisions"][0]

    assert decision["decision"] == "SIMULATED_NEXT_OPEN_REJECTED"
    assert decision["reject_reason"] == "INVALID_STOP_LOSS"
    assert decision["execution_data_quality_pass"] is True
    assert decision["execution_rule_pass"] is True
    assert decision["risk_rule_pass"] is False


def test_phase5a_rejects_initial_stop_too_wide(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_signal(config, stop_loss=80.0)
    _insert_next_open(config, open_price=101.0)

    decision = generate_execution_decisions(config, ASOF).to_dict()["decisions"][0]

    assert decision["decision"] == "SIMULATED_NEXT_OPEN_REJECTED"
    assert decision["reject_reason"] == "INITIAL_STOP_TOO_WIDE"
    assert decision["execution_data_quality_pass"] is True
    assert decision["execution_rule_pass"] is True
    assert decision["risk_rule_pass"] is False


def test_phase5a_preserves_source_signal_metadata_separately_from_execution_metadata(
    tmp_path: Path,
) -> None:
    config = _config(
        tmp_path,
        reproducibility={"data_snapshot_id": "execution_snapshot"},
    )
    _insert_signal(config)
    _insert_next_open(config, open_price=101.0)

    decision = generate_execution_decisions(config, ASOF).to_dict()["decisions"][0]

    assert decision["source_signal_config_hash"] == "signal_hash"
    assert decision["source_signal_git_commit"] == "signal_commit"
    assert decision["source_signal_data_snapshot_id"] == "signal_snapshot"
    assert decision["source_universe_version"] == "signal_universe"
    assert decision["source_theme_version"] == "signal_theme"
    assert decision["execution_config_hash"] == config_hash(config)
    assert decision["execution_data_snapshot_id"] == "execution_snapshot"
    assert decision["execution_config_hash"] != decision["source_signal_config_hash"]


def test_phase5a_multiple_execution_runs_do_not_overwrite_same_date_decisions(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _insert_signal(config)
    _insert_next_open(config, open_price=101.0)

    first = generate_execution_decisions(config, ASOF).to_dict()
    second = generate_execution_decisions(config, ASOF).to_dict()

    assert first["execution_run_id"] != second["execution_run_id"]
    with connect_database(config.database.path) as connection:
        run_count = connection.execute("SELECT COUNT(*) FROM execution_runs").fetchone()[0]
        decision_count = connection.execute(
            "SELECT COUNT(*) FROM execution_decisions"
        ).fetchone()[0]
    assert run_count == 2
    assert decision_count == 2


def test_phase5a_ignores_non_triggered_or_actionable_rows(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_signal(config, symbol="SETUP", state="SETUP")
    _insert_signal(config, symbol="ACTION", actionable=True)
    _insert_next_open(config, symbol="SETUP", open_price=101.0)
    _insert_next_open(config, symbol="ACTION", open_price=101.0)

    result = generate_execution_decisions(config, ASOF).to_dict()

    assert result["decisions"] == []


def test_phase5a_ignores_market_or_portfolio_blocked_rows(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_signal(config, symbol="MARKET", market_gate_pass=False)
    _insert_signal(config, symbol="PORT", portfolio_risk_pass=False)
    _insert_next_open(config, symbol="MARKET", open_price=101.0)
    _insert_next_open(config, symbol="PORT", open_price=101.0)

    result = generate_execution_decisions(config, ASOF).to_dict()

    assert result["decisions"] == []


def test_phase5a_default_policy_accepts_next_open_below_trigger(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_signal(config, entry_trigger=100.0, stop_loss=97.0)
    _insert_next_open(config, open_price=99.0)

    decision = generate_execution_decisions(config, ASOF).to_dict()["decisions"][0]

    assert decision["decision"] == "SIMULATED_NEXT_OPEN_ACCEPTED"
    assert decision["require_next_open_above_trigger"] is False


def test_phase5a_can_require_next_open_to_hold_trigger(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        execution={
            "require_next_open_above_trigger": True,
            "max_entry_drop_below_trigger_pct": 0.02,
        },
    )
    _insert_signal(config, entry_trigger=100.0, stop_loss=97.0)
    _insert_next_open(config, open_price=97.0)

    decision = generate_execution_decisions(config, ASOF).to_dict()["decisions"][0]

    assert decision["decision"] == "SIMULATED_NEXT_OPEN_REJECTED"
    assert decision["reject_reason"] == "ENTRY_BELOW_TRIGGER"
    assert decision["execution_rule_pass"] is False


def test_phase5a_execution_uses_provider_priority_next_open(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        providers={"prices_primary": "FMP", "prices_fallback": "yfinance"},
    )
    _insert_signal(config)
    _insert_next_open(config, open_price=999.0, provider="yfinance")
    _insert_next_open(config, open_price=101.0, provider="FMP")

    decision = generate_execution_decisions(config, ASOF).to_dict()["decisions"][0]

    assert decision["chosen_provider"] == "FMP"
    assert decision["actual_entry_price"] == 101.0


def test_phase5a_cli_output_omits_performance_metric_terms(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_signal(config)
    _insert_next_open(config, open_price=101.0)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
database:
  path: {config.database.path}
reproducibility:
  data_snapshot_id: cli_snapshot
""",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "execution-decisions",
            "--asof",
            ASOF.isoformat(),
            "--no-persist",
            "--config",
            str(config_path),
        ],
    )
    payload = json.loads(result.stdout)
    output = result.stdout.lower()

    assert result.exit_code == 0
    assert payload["performance_metrics"] == {}
    for forbidden in ("cagr", "sharpe", "drawdown", "annual_return", "win_rate", "profit_factor"):
        assert forbidden not in output


def test_phase5a_execution_path_does_not_recompute_scoring_or_setups() -> None:
    source = inspect.getsource(execution_module)

    assert "run_scoring" not in source
    assert "detect_setups" not in source
