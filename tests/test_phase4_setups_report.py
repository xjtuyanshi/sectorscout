from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database, initialize_database
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


def test_phase4_setup_detection_uses_provider_priority_price_snapshot(tmp_path: Path) -> None:
    config = _seed_vcp_database(tmp_path)
    with connect_database(config.database.path) as connection:
        for provider, close, volume in (("FMP", 142.0, 350_000), ("yfinance", 146.0, 1_500_000)):
            connection.execute(
                """
                INSERT INTO daily_prices (
                    symbol, price_date, open, high, low, close, volume,
                    adj_open, adj_high, adj_low, adj_close, adj_volume,
                    provider, is_adjusted, adjustment_warning, ingested_at_utc
                ) VALUES (
                    'MU', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, true, false, now()
                )
                """,
                [
                    ASOF,
                    close * 0.997,
                    close * 1.012,
                    close * 0.988,
                    close,
                    volume,
                    close * 0.997,
                    close * 1.012,
                    close * 0.988,
                    close,
                    volume,
                    provider,
                ],
            )

    result = detect_setups(config, ASOF).to_dict()
    vcp_setups = [row for row in result["setups"] if row["setup_type"] == "VCP"]

    assert len(vcp_setups) == 1
    assert vcp_setups[0]["state"] == "SETUP"
    assert vcp_setups[0]["trigger_price"] is None


def test_phase4_daily_report_uses_required_action_categories(tmp_path: Path) -> None:
    config = _seed_vcp_database(tmp_path)
    report = generate_daily_report(config, ASOF)
    payload = report.to_dict()

    assert "Triggered setup candidate" in payload["categories"]
    assert "Triggered actionable setup" in payload["categories"]
    assert payload["categories"]["Triggered setup candidate"][0]["symbol"] == "MU"
    assert payload["categories"]["Triggered setup candidate"][0]["setup_data_present"] is True
    assert payload["categories"]["Triggered setup candidate"][0]["price_snapshot_quality_pass"] is True
    assert payload["categories"]["Triggered setup candidate"][0]["execution_data_quality_pass"] is False
    assert payload["categories"]["Triggered setup candidate"][0]["data_quality_pass"] is False
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


def test_phase4_portfolio_selection_uses_quality_sorted_order(tmp_path: Path) -> None:
    config = SectorScoutConfig.model_validate(
        {
            "database": {"path": tmp_path / "test.duckdb"},
            "portfolio": {"max_new_positions_per_day": 1},
        }
    )
    low = _setup_row("LOW", "ai-memory", 60)
    high = _setup_row("HIGH", "ai-memory", 95)
    signals = build_signals(config, [low, high], "RISK_ON")
    by_symbol = {signal.symbol: signal for signal in signals}

    assert by_symbol["HIGH"].action_category == "Triggered setup candidate"
    assert by_symbol["HIGH"].portfolio_risk_pass is True
    assert by_symbol["LOW"].portfolio_risk_pass is False
    assert by_symbol["LOW"].portfolio_risk_reason == "Max new positions per day reached."


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


def test_phase4_earnings_gap_maps_premarket_to_same_session_and_after_close_to_next_session(
    tmp_path: Path,
) -> None:
    config = SectorScoutConfig.model_validate(
        {"database": {"path": tmp_path / "test.duckdb"}}
    )
    initialize_database(config)
    facts = tmp_path / "facts.csv"
    facts.write_text(
        "symbol,cik,fiscal_period,fiscal_year,fiscal_quarter,form_type,metric_name,metric_value,period_end_date,filing_date,accepted_at,earnings_release_datetime,available_at,provider_updated_at\n"
        "PRE,0001,2024Q3,2024,3,10-Q,revenue,100,2024-09-30,2024-11-20,2024-11-20T12:00:00+00:00,2024-11-20T12:00:00+00:00,2024-11-20T12:00:00+00:00,2024-11-20T12:01:00+00:00\n"
        "POST,0002,2024Q3,2024,3,10-Q,revenue,100,2024-09-30,2024-11-20,2024-11-20T21:30:00+00:00,2024-11-20T21:30:00+00:00,2024-11-20T21:30:00+00:00,2024-11-20T21:31:00+00:00\n",
        encoding="utf-8",
    )
    ingest_fundamental_facts_csv(config, facts, source="fixture")
    prices = pd.DataFrame(
        {
            "date": [date(2024, 11, 20), date(2024, 11, 21)],
            "open": [100.0, 100.0],
            "high": [101.0, 101.0],
            "low": [99.0, 99.0],
            "close": [100.0, 100.0],
            "volume": [1_000_000, 1_000_000],
        }
    )
    from sectorscout.setups import _known_earnings_gap_dates

    assert _known_earnings_gap_dates(config, "PRE", ASOF, prices) == {date(2024, 11, 20)}
    assert _known_earnings_gap_dates(config, "POST", ASOF, prices) == {date(2024, 11, 21)}


def test_phase4_earnings_gap_filters_known_gap_dates_before_selecting_latest_gap(
    tmp_path: Path,
) -> None:
    config = SectorScoutConfig.model_validate(
        {"database": {"path": tmp_path / "test.duckdb"}}
    )
    initialize_database(config)
    facts = tmp_path / "facts.csv"
    facts.write_text(
        "symbol,cik,fiscal_period,fiscal_year,fiscal_quarter,form_type,metric_name,metric_value,period_end_date,filing_date,accepted_at,earnings_release_datetime,available_at,provider_updated_at\n"
        "GAP,0001,2024Q3,2024,3,10-Q,revenue,100,2024-09-30,2024-11-15,2024-11-15T21:30:00+00:00,2024-11-15T21:30:00+00:00,2024-11-15T21:30:00+00:00,2024-11-15T21:31:00+00:00\n",
        encoding="utf-8",
    )
    ingest_fundamental_facts_csv(config, facts, source="fixture")
    rows = []
    dates = pd.bdate_range(end=ASOF, periods=90)
    post_gap_mode = False
    for index, session in enumerate(dates):
        session_date = session.date()
        close = 100.0 + index * 0.02
        open_ = close
        high = close * 1.005
        low = close * 0.995
        volume = 500_000
        if session_date == date(2024, 11, 18):
            post_gap_mode = True
            open_ = 112.0
            close = 113.0
            high = 114.0
            low = 111.0
            volume = 2_000_000
        elif session_date == date(2024, 11, 26):
            open_ = 128.0
            close = 129.0
            high = 131.0
            low = 126.0
            volume = 2_000_000
        elif post_gap_mode:
            close = 113.0 + index * 0.05
            open_ = close
            high = min(close * 1.005, 130.0)
            low = 111.5
            volume = 600_000
        if session_date == ASOF:
            open_ = 132.0
            close = 132.5
            high = 133.0
            low = 131.5
            volume = 700_000
        rows.append(
            {
                "date": session_date,
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

    setup = _detect_earnings_gap_base(config, candidate, prices, metadata)

    assert setup is not None
    assert setup.consolidation_start == "2024-11-18"
