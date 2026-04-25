from datetime import date
from pathlib import Path

from sectorscout.config import SectorScoutConfig
from sectorscout.db import initialize_database
from sectorscout.ingest import (
    ingest_fundamental_facts_csv,
    ingest_theme_members_csv,
    ingest_themes_csv,
    ingest_universe_csv,
)
from sectorscout.pit import available_fundamental_facts, theme_members_asof, universe_asof


def test_fundamentals_are_point_in_time_and_unknown_availability_is_excluded(tmp_path: Path) -> None:
    config = SectorScoutConfig.model_validate(
        {"database": {"path": tmp_path / "test.duckdb"}}
    )
    initialize_database(config)
    ingest_fundamental_facts_csv(
        config,
        "data/fixtures/fundamental_facts_sample.csv",
        source="fixture",
    )
    facts_before_aapl_filing = available_fundamental_facts(config, date(2024, 10, 31))
    assert {fact["symbol"] for fact in facts_before_aapl_filing} == {"MU"}
    facts_after_aapl_filing = available_fundamental_facts(config, date(2024, 11, 29))
    assert {fact["symbol"] for fact in facts_after_aapl_filing} == {"AAPL", "MU"}


def test_theme_membership_is_point_in_time_and_ex_post_is_excluded_by_default(tmp_path: Path) -> None:
    config = SectorScoutConfig.model_validate(
        {"database": {"path": tmp_path / "test.duckdb"}}
    )
    initialize_database(config)
    ingest_themes_csv(config, "data/fixtures/themes_sample.csv", source="fixture")
    ingest_theme_members_csv(config, "data/fixtures/theme_members_sample.csv", source="fixture")
    before_dynamic_discovery = theme_members_asof(config, date(2024, 11, 27))
    assert {(row["theme_id"], row["symbol"]) for row in before_dynamic_discovery} == {
        ("semiconductors", "MU"),
        ("semiconductors", "SMH"),
    }
    after_dynamic_discovery = theme_members_asof(config, date(2024, 11, 29))
    assert ("dynamic-power", "AAPL") in {
        (row["theme_id"], row["symbol"]) for row in after_dynamic_discovery
    }
    with_ex_post = theme_members_asof(
        config,
        date(2024, 11, 29),
        allow_historical_ex_post=True,
    )
    assert ("ai-memory-hbm", "MU") in {
        (row["theme_id"], row["symbol"]) for row in with_ex_post
    }


def test_universe_asof_respects_lifecycle_dates(tmp_path: Path) -> None:
    config = SectorScoutConfig.model_validate(
        {"database": {"path": tmp_path / "test.duckdb"}}
    )
    initialize_database(config)
    universe_csv = tmp_path / "universe.csv"
    universe_csv.write_text(
        "symbol,name,exchange,security_type,is_etf,is_active,ipo_date,delist_date,first_seen_at,last_seen_at\n"
        "OLD,Old Co,NASDAQ,common_stock,false,true,2010-01-01,2024-01-15,2020-01-01,2024-12-31\n"
        "NEW,New Co,NASDAQ,common_stock,false,true,2024-11-29,,2024-11-29,2024-12-31\n"
        "LIVE,Live Co,NASDAQ,common_stock,false,true,2020-01-01,,2020-01-01,2024-12-31\n",
        encoding="utf-8",
    )
    ingest_universe_csv(config, universe_csv, provider="fixture")
    assert universe_asof(config, date(2024, 11, 28)) == ["LIVE"]
    assert universe_asof(config, date(2024, 11, 29)) == ["LIVE", "NEW"]
