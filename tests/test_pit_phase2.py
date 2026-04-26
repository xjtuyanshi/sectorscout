from datetime import date
from pathlib import Path

from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database, initialize_database
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


def test_theme_members_valid_to_is_inclusive(tmp_path: Path) -> None:
    config = SectorScoutConfig.model_validate(
        {"database": {"path": tmp_path / "test.duckdb"}}
    )
    initialize_database(config)
    themes = tmp_path / "themes.csv"
    members = tmp_path / "members.csv"
    themes.write_text(
        "theme_id,name,theme_type,discovery_date,confidence,evidence\n"
        "memory,Memory,live_research_theme,2024-01-01,0.9,fixture\n",
        encoding="utf-8",
    )
    members.write_text(
        "theme_id,symbol,valid_from,valid_to,confidence,evidence\n"
        "memory,MU,2024-01-01,2024-11-29,0.9,fixture\n",
        encoding="utf-8",
    )
    ingest_themes_csv(config, themes, source="fixture")
    ingest_theme_members_csv(config, members, source="fixture")

    assert [row["symbol"] for row in theme_members_asof(config, date(2024, 11, 29))] == ["MU"]
    assert theme_members_asof(config, date(2024, 11, 30)) == []


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
        "DEAD,Dead Co,NASDAQ,common_stock,false,false,2010-01-01,2025-01-01,2020-01-01,2024-12-31\n"
        "LIVE,Live Co,NASDAQ,common_stock,false,true,2020-01-01,,2020-01-01,2024-12-31\n",
        encoding="utf-8",
    )
    ingest_universe_csv(config, universe_csv, provider="fixture")
    assert universe_asof(config, date(2024, 11, 28)) == ["DEAD", "LIVE"]
    assert universe_asof(config, date(2024, 11, 28), mode="live") == ["LIVE"]
    assert universe_asof(config, date(2024, 11, 29)) == ["DEAD", "LIVE", "NEW"]


def test_revised_fundamental_facts_do_not_overwrite_prior_pit_rows(tmp_path: Path) -> None:
    config = SectorScoutConfig.model_validate(
        {"database": {"path": tmp_path / "test.duckdb"}}
    )
    initialize_database(config)
    facts_csv = tmp_path / "facts.csv"
    facts_csv.write_text(
        "symbol,cik,fiscal_period,fiscal_year,fiscal_quarter,form_type,metric_name,metric_value,period_end_date,filing_date,accepted_at,earnings_release_datetime,available_at,provider_updated_at,revision_number,is_restatement\n"
        "MU,0000723125,2024Q4,2024,4,10-K,revenue,25000000000,2024-08-29,2024-10-03,2024-10-03T20:15:00+00:00,2024-09-25T20:05:00+00:00,2024-10-03T20:15:00+00:00,2024-10-03T20:16:00+00:00,0,false\n"
        "MU,0000723125,2024Q4,2024,4,10-K,revenue,25100000000,2024-08-29,2024-10-03,2024-10-03T20:15:00+00:00,2024-09-25T20:05:00+00:00,2024-10-20T20:15:00+00:00,2024-10-20T20:16:00+00:00,1,true\n",
        encoding="utf-8",
    )
    ingest_fundamental_facts_csv(config, facts_csv, source="fixture")

    with connect_database(config.database.path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM fundamental_facts").fetchone()[0]
    assert count == 2
    assert [
        fact["metric_value"] for fact in available_fundamental_facts(config, date(2024, 10, 10))
    ] == [25000000000.0]
    assert [
        fact["metric_value"] for fact in available_fundamental_facts(config, date(2024, 10, 21))
    ] == [25000000000.0, 25100000000.0]


def test_same_day_post_close_fundamental_availability_is_excluded(tmp_path: Path) -> None:
    config = SectorScoutConfig.model_validate(
        {"database": {"path": tmp_path / "test.duckdb"}}
    )
    initialize_database(config)
    facts_csv = tmp_path / "facts.csv"
    facts_csv.write_text(
        "symbol,cik,fiscal_period,fiscal_year,fiscal_quarter,form_type,metric_name,metric_value,period_end_date,filing_date,accepted_at,earnings_release_datetime,available_at,provider_updated_at\n"
        "A,0001,2024Q3,2024,3,10-Q,revenue,100,2024-09-30,2024-11-29,2024-11-29T17:59:00+00:00,2024-11-29T17:50:00+00:00,2024-11-29T17:59:00+00:00,2024-11-29T17:59:00+00:00\n"
        "B,0002,2024Q3,2024,3,10-Q,revenue,100,2024-09-30,2024-11-29,2024-11-29T18:01:00+00:00,2024-11-29T17:50:00+00:00,2024-11-29T18:01:00+00:00,2024-11-29T18:01:00+00:00\n",
        encoding="utf-8",
    )
    ingest_fundamental_facts_csv(config, facts_csv, source="fixture")

    assert {fact["symbol"] for fact in available_fundamental_facts(config, date(2024, 11, 29))} == {"A"}
