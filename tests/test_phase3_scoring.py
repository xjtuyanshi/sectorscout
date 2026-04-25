from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database, initialize_database
from sectorscout.indicators import compute_technical_indicators
from sectorscout.ingest import (
    ingest_fundamental_facts_csv,
    ingest_prices_csv,
    ingest_theme_members_csv,
    ingest_themes_csv,
    ingest_universe_csv,
)
from sectorscout.market_regime import compute_market_regime
from sectorscout.scoring import run_scoring


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


def _write_prices(path: Path) -> None:
    dates = pd.bdate_range(end=ASOF, periods=260)
    profiles = {
        "SPY": (100.0, 0.45, 5_000_000),
        "QQQ": (100.0, 0.60, 4_000_000),
        "MU": (45.0, 0.80, 2_000_000),
        "WEAK": (100.0, -0.12, 300_000),
    }
    lines = [
        "symbol,date,open,high,low,close,volume,adj_open,adj_high,adj_low,adj_close,adj_volume,adjustment_warning"
    ]
    for symbol, (base, slope, volume) in profiles.items():
        for index, session in enumerate(dates):
            close = base + slope * index
            open_ = close * 0.995
            high = close * 1.01
            low = close * 0.99
            row_volume = volume + (500_000 if symbol == "MU" and index >= 250 else 0)
            values = [
                symbol,
                session.date().isoformat(),
                f"{open_:.2f}",
                f"{high:.2f}",
                f"{low:.2f}",
                f"{close:.2f}",
                str(row_volume),
                f"{open_:.2f}",
                f"{high:.2f}",
                f"{low:.2f}",
                f"{close:.2f}",
                str(row_volume),
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


def _seed_phase3_database(tmp_path: Path) -> SectorScoutConfig:
    config = SectorScoutConfig.model_validate(
        {"database": {"path": tmp_path / "test.duckdb"}}
    )
    initialize_database(config)

    universe_path = tmp_path / "universe.csv"
    prices_path = tmp_path / "prices.csv"
    themes_path = tmp_path / "themes.csv"
    members_path = tmp_path / "theme_members.csv"
    fundamentals_path = tmp_path / "fundamentals.csv"

    _write_universe(universe_path)
    _write_prices(prices_path)
    _write_themes(themes_path)
    _write_theme_members(members_path)
    _write_fundamentals(fundamentals_path)

    ingest_universe_csv(config, universe_path, provider="fixture")
    ingest_prices_csv(config, prices_path, provider="fixture")
    ingest_themes_csv(config, themes_path, source="fixture")
    ingest_theme_members_csv(config, members_path, source="fixture")
    ingest_fundamental_facts_csv(config, fundamentals_path, source="fixture")
    return config


def test_phase3_indicators_are_deterministic_and_stage_aware(tmp_path: Path) -> None:
    config = _seed_phase3_database(tmp_path)
    rows = compute_technical_indicators(config, ASOF)
    by_symbol = {row.symbol: row for row in rows}

    assert by_symbol["SPY"].trend_stage == "Stage 2"
    assert by_symbol["QQQ"].trend_stage == "Stage 2"
    assert by_symbol["MU"].trend_stage == "Stage 2"
    assert by_symbol["WEAK"].trend_stage != "Stage 2"
    assert by_symbol["MU"].rs_percentile > by_symbol["WEAK"].rs_percentile

    with connect_database(config.database.path) as connection:
        persisted = connection.execute(
            "SELECT COUNT(*) FROM technical_indicators WHERE asof_date = ?",
            [ASOF],
        ).fetchone()[0]
    assert persisted == 4


def test_phase3_market_regime_uses_spy_qqq_and_breadth(tmp_path: Path) -> None:
    config = _seed_phase3_database(tmp_path)
    compute_technical_indicators(config, ASOF)
    regime = compute_market_regime(config, ASOF)

    assert regime.spy_stage == "Stage 2"
    assert regime.qqq_stage == "Stage 2"
    assert regime.risk_state == "RISK_ON"
    assert regime.pct_universe_stage2 == 0.75


def test_phase3_scores_rank_without_triggering_trades(tmp_path: Path) -> None:
    config = _seed_phase3_database(tmp_path)
    result = run_scoring(config, ASOF).to_dict()

    assert result["market_regime"]["risk_state"] == "RISK_ON"
    assert result["theme_scores"][0]["theme_id"] == "ai-memory"
    assert result["theme_scores"][0]["theme_fundamental_coverage_pct"] == 100.0
    assert result["stock_scores"][0]["symbol"] == "MU"
    assert result["stock_scores"][0]["state"] == "WATCH"
    assert result["stock_scores"][0]["setup_quality"] == 0.0
    assert result["stock_scores"][0]["risk_reward"] == 0.0
    assert result["stock_scores"][0]["state"] != "TRIGGERED"

    with connect_database(config.database.path) as connection:
        watchlist_state = connection.execute(
            "SELECT state FROM watchlist WHERE asof_date = ? AND symbol = 'MU'",
            [ASOF],
        ).fetchone()[0]
    assert watchlist_state == "WATCH"
