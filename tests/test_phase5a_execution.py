from __future__ import annotations

import inspect
from datetime import date
from pathlib import Path

from sectorscout.config import SectorScoutConfig
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
                ?, ?, 'ai-memory', 'VCP', 'TRIGGERED',
                'Triggered setup candidate', false, 'Phase 4 candidate', 'next_open',
                ?, ?, 2.0, true,
                false, true, false,
                'Phase 5 execution validation required.', true, 'Market gate pass.',
                'RISK_ON', true, 'Portfolio gate pass.',
                '2024-11-29T18:00:00+00:00', 'hash', 'commit',
                'snapshot', 'universe', 'theme'
            )
            """,
            [ASOF, symbol, entry_trigger, stop_loss],
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
    assert "not a performance backtest" in result["warning"]
    assert decision["decision"] == "FILLED"
    assert decision["next_session_date"] == "2024-12-02"
    assert decision["chosen_provider"] == "FMP"
    assert decision["actual_entry_price"] == 101.0
    assert decision["actual_stop_loss"] == 95.0
    assert decision["risk_per_share"] == 6.0
    assert decision["target_2r"] == 113.0
    assert decision["target_3r"] == 119.0
    assert decision["reward_risk"] == 2.0
    assert decision["execution_data_quality_pass"] is True
    with connect_database(config.database.path) as connection:
        persisted = connection.execute(
            "SELECT decision, actual_entry_price FROM execution_decisions"
        ).fetchone()
    assert persisted == ("FILLED", 101.0)


def test_phase5a_rejects_gap_too_extended(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_signal(config)
    _insert_next_open(config, open_price=106.0)

    decision = generate_execution_decisions(config, ASOF).to_dict()["decisions"][0]

    assert decision["decision"] == "REJECTED"
    assert decision["reject_reason"] == "ENTRY_EXTENSION_TOO_HIGH"
    assert decision["execution_data_quality_pass"] is False


def test_phase5a_rejects_missing_next_open_price(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_signal(config)

    decision = generate_execution_decisions(config, ASOF).to_dict()["decisions"][0]

    assert decision["decision"] == "REJECTED"
    assert decision["reject_reason"] == "MISSING_NEXT_OPEN"
    assert decision["actual_entry_price"] is None


def test_phase5a_rejects_stop_at_or_above_entry(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_signal(config, stop_loss=102.0)
    _insert_next_open(config, open_price=101.0)

    decision = generate_execution_decisions(config, ASOF).to_dict()["decisions"][0]

    assert decision["decision"] == "REJECTED"
    assert decision["reject_reason"] == "INVALID_STOP_LOSS"


def test_phase5a_rejects_initial_stop_too_wide(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_signal(config, stop_loss=80.0)
    _insert_next_open(config, open_price=101.0)

    decision = generate_execution_decisions(config, ASOF).to_dict()["decisions"][0]

    assert decision["decision"] == "REJECTED"
    assert decision["reject_reason"] == "INITIAL_STOP_TOO_WIDE"


def test_phase5a_execution_path_does_not_recompute_scoring_or_setups() -> None:
    source = inspect.getsource(execution_module)

    assert "run_scoring" not in source
    assert "detect_setups" not in source
