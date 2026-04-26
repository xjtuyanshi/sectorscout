from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from sectorscout.config import SectorScoutConfig
from sectorscout.db import initialize_database
from sectorscout.ingest import (
    ingest_fundamental_facts_csv,
    ingest_prices_csv,
    ingest_theme_members_csv,
    ingest_themes_csv,
    ingest_universe_csv,
)
from sectorscout.reports import generate_daily_report
from sectorscout.setups import SetupRow, _detect_earnings_gap_base, build_signals, detect_setups


ASOF = date(2024, 11, 29)


def _write_universe(path: Path) -> None:
    path.write_text(
        "symbol,name,exchange,security_type,is_etf,is_active,ipo_date,delist_date,first_seen_at,last_seen_at\n"
        "SPY,SPDR S&P 500 ETF,NYSE,etf,true,true,1993-01-29,,2020-01-01,2024-12-31\n"
        "QQQ,Invesco QQQ,NASDAQ,etf,true,true,1999-03-10,,2020-01-01,2024-12-31\n"
        "MU,Micron Technology,NASDAQ,common_stock,false,true,1984-01-01,,2020-01-01,2024-12-31\n"
        "WEAK,Weak Co,NASDAQ,common_stock,false,true,2020-01-01,,2020-01-01,2024-12-31\n",
        encoding="utf-8",
    )


def _mu_vcp_close(index: int) -> float:
    if index < 220:
        return 45.0 + index * 0.42
    if index < 240:
        return 136.0 + ((index % 2) * 6.0)
    if index < 253:
        return 139.0 + ((index % 2) * 2.0)
    if index < 259:
        return 141.0 + (index - 253) * 0.45
    return 146.0


def _write_prices(path: Path, *, breakout: bool = True) -> None:
    dates = pd.bdate_range(end=ASOF, periods=260)
    lines = [
        "symbol,date,open,high,low,close,volume,adj_open,adj_high,adj_low,adj_close,adj_volume,adjustment_warning"
    ]
    for symbol in ("SPY", "QQQ", "MU", "WEAK"):
        for index, session in enumerate(dates):
            if symbol == "SPY":
                close = 100.0 + index * 0.05
                volume = 5_000_000
                spread = 0.01
            elif symbol == "QQQ":
                close = 100.0 + index * 0.06
                volume = 4_000_000
                spread = 0.01
            elif symbol == "MU":
                close = _mu_vcp_close(index)
                if not breakout and index == 259:
                    close = 142.0
                volume = 2_000_000 if index < 210 else 1_000_000
                if 240 <= index < 259:
                    volume = 350_000
                if index == 259:
                    volume = 1_500_000 if breakout else 350_000
                spread = 0.012 if index >= 240 else 0.025
            else:
                close = 100.0 - index * 0.12
                volume = 300_000
                spread = 0.01
            open_ = close * 0.997
            high = close * (1 + spread)
            low = close * (1 - spread)
            values = [
                symbol,
                session.date().isoformat(),
                f"{open_:.2f}",
                f"{high:.2f}",
                f"{low:.2f}",
                f"{close:.2f}",
                str(volume),
                f"{open_:.2f}",
                f"{high:.2f}",
                f"{low:.2f}",
                f"{close:.2f}",
                str(volume),
                "false",
            ]
            lines.append(",".join(values))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_themes(path: Path) -> None:
    path.write_text(
        "theme_id,name,theme_type,discovery_date,confidence,evidence\n"
        "ai-memory,AI Memory,live_research_theme,2024-01-01,0.90,Fixture leadership theme\n",
        encoding="utf-8",
    )


def _write_theme_members(path: Path) -> None:
    path.write_text(
        "theme_id,symbol,valid_from,valid_to,confidence,evidence\n"
        "ai-memory,MU,2024-01-01,,0.90,Fixture PIT membership\n",
        encoding="utf-8",
    )


def _write_fundamentals(path: Path) -> None:
    path.write_text(
        "symbol,cik,fiscal_period,fiscal_year,fiscal_quarter,form_type,metric_name,metric_value,period_end_date,filing_date,accepted_at,earnings_release_datetime,available_at,provider_updated_at\n"
        "MU,0000723125,2023Q4,2023,4,10-K,revenue,20000000000,2023-08-31,2023-10-05,2023-10-05T20:15:00+00:00,2023-09-27T20:05:00+00:00,2023-10-05T20:15:00+00:00,2023-10-05T20:16:00+00:00\n"
        "MU,0000723125,2024Q4,2024,4,10-K,revenue,26000000000,2024-08-29,2024-10-03,2024-10-03T20:15:00+00:00,2024-09-25T20:05:00+00:00,2024-10-03T20:15:00+00:00,2024-10-03T20:16:00+00:00\n",
        encoding="utf-8",
    )


def _seed_vcp_database(tmp_path: Path, *, breakout: bool = True) -> SectorScoutConfig:
    config = SectorScoutConfig.model_validate(
        {"database": {"path": tmp_path / "test.duckdb"}}
    )
    initialize_database(config)
    paths = {
        "universe": tmp_path / "universe.csv",
        "prices": tmp_path / "prices.csv",
        "themes": tmp_path / "themes.csv",
        "members": tmp_path / "members.csv",
        "fundamentals": tmp_path / "fundamentals.csv",
    }
    _write_universe(paths["universe"])
    _write_prices(paths["prices"], breakout=breakout)
    _write_themes(paths["themes"])
    _write_theme_members(paths["members"])
    _write_fundamentals(paths["fundamentals"])
    ingest_universe_csv(config, paths["universe"], provider="fixture")
    ingest_prices_csv(config, paths["prices"], provider="fixture")
    ingest_themes_csv(config, paths["themes"], source="fixture")
    ingest_theme_members_csv(config, paths["members"], source="fixture")
    ingest_fundamental_facts_csv(config, paths["fundamentals"], source="fixture")
    return config


def test_phase4_vcp_trigger_creates_triggered_candidate_not_actionable_label(tmp_path: Path) -> None:
    config = _seed_vcp_database(tmp_path)
    result = detect_setups(config, ASOF).to_dict()

    vcp_setups = [row for row in result["setups"] if row["setup_type"] == "VCP"]
    assert len(vcp_setups) == 1
    assert vcp_setups[0]["symbol"] == "MU"
    assert vcp_setups[0]["state"] == "TRIGGERED"
    assert vcp_setups[0]["reward_risk"] == 2.0

    vcp_signal = result["signals"][0]
    assert vcp_signal["action_category"] == "Triggered setup candidate"
    assert vcp_signal["actionable"] is False
    assert vcp_signal["market_gate_pass"] is True
    assert vcp_signal["portfolio_risk_pass"] is True
    assert "Phase 5" in vcp_signal["reason"]
    assert "buy" not in str(vcp_signal).lower()


def test_phase4_vcp_without_breakout_is_setup_not_triggered(tmp_path: Path) -> None:
    config = _seed_vcp_database(tmp_path, breakout=False)
    result = detect_setups(config, ASOF).to_dict()

    vcp_setups = [row for row in result["setups"] if row["setup_type"] == "VCP"]
    assert len(vcp_setups) == 1
    assert vcp_setups[0]["state"] == "SETUP"
    assert vcp_setups[0]["trigger_price"] is None
    assert result["signals"][0]["action_category"] == "Setup forming"


def test_phase4_daily_report_uses_required_action_categories(tmp_path: Path) -> None:
    config = _seed_vcp_database(tmp_path)
    report = generate_daily_report(config, ASOF)
    payload = report.to_dict()

    assert "Triggered setup candidate" in payload["categories"]
    assert "Triggered actionable setup" in payload["categories"]
    assert payload["categories"]["Triggered setup candidate"][0]["symbol"] == "MU"
    assert payload["categories"]["Triggered setup candidate"][0]["data_quality_pass"] is True
    report_text = report.to_json().lower()
    for forbidden in ("buy", "sell", "order", "trade now", "execute"):
        assert forbidden not in report_text


def _setup_row(symbol: str, theme_id: str, quality: float = 90.0) -> SetupRow:
    return SetupRow(
        asof_date=ASOF.isoformat(),
        symbol=symbol,
        theme_id=theme_id,
        setup_type="VCP",
        state="TRIGGERED",
        setup_quality=quality,
        trigger_price=100.0,
        stop_loss=90.0,
        reward_risk=2.0,
        consolidation_start="2024-11-01",
        consolidation_end=ASOF.isoformat(),
        pivot=100.0,
        depth=0.10,
        atr_contraction=True,
        volume_contraction=True,
        evidence={},
        signal_generated_at="2024-11-29T21:00:00+00:00",
        config_hash="hash",
        git_commit="commit",
        data_snapshot_id="snapshot",
        universe_version="universe",
        theme_version="theme",
    )


def test_phase4_signal_gate_reasons_separate_regime_from_portfolio(tmp_path: Path) -> None:
    config = SectorScoutConfig.model_validate(
        {
            "database": {"path": tmp_path / "test.duckdb"},
            "portfolio": {"max_new_positions_per_day": 1},
        }
    )
    regime_blocked = build_signals(config, [_setup_row("MU", "ai-memory")], "RISK_OFF")[0]
    assert regime_blocked.market_gate_pass is False
    assert regime_blocked.portfolio_risk_pass is True
    assert regime_blocked.action_category == "Blocked by market regime"

    portfolio_blocked = build_signals(
        config,
        [_setup_row("MU", "ai-memory", 90), _setup_row("NVDA", "ai-memory", 80)],
        "RISK_ON",
    )[1]
    assert portfolio_blocked.market_gate_pass is True
    assert portfolio_blocked.portfolio_risk_pass is False
    assert portfolio_blocked.portfolio_risk_reason == "Max new positions per day reached."


def test_phase4_earnings_gap_requires_known_public_earnings_timestamp(tmp_path: Path) -> None:
    config = SectorScoutConfig.model_validate(
        {"database": {"path": tmp_path / "test.duckdb"}}
    )
    initialize_database(config)
    dates = pd.bdate_range(end=ASOF, periods=90)
    rows = []
    for index, session in enumerate(dates):
        close = 100.0 + index * 0.05
        open_ = close
        high = close * 1.01
        low = close * 0.99
        volume = 500_000
        if index == 80:
            open_ = 120.0
            close = 121.0
            high = 123.0
            low = 119.0
            volume = 2_000_000
        if index > 80:
            close = 121.0 + (index - 80) * 0.4
            open_ = close
            high = close * 1.005
            low = 119.5
        rows.append(
            {
                "date": session.date(),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )
    prices = pd.DataFrame(rows)
    candidate = {"symbol": "GAP", "theme_id": "ai-memory"}
    metadata = _setup_row("GAP", "ai-memory")

    assert _detect_earnings_gap_base(config, candidate, prices, metadata) is None
