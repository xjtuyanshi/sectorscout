from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import typer

from sectorscout import __version__
from sectorscout.config import config_hash, load_config

DEFAULT_DEMO_ASOF_DATE = date(2024, 11, 29)
DEFAULT_HINDSIGHT_CASES_PATH = Path("data/hindsight/leader_cases.csv")
DEFAULT_PUBLIC_SOURCES_PATH = Path("data/intel/public_sources.yaml")
DEFAULT_X_SOURCES_PATH = Path("data/intel/x_sources.yaml")

app = typer.Typer(help="SectorScout research system CLI.")
intel_capture_app = typer.Typer(help="Human-in-the-loop external intel capture.")
intel_report_app = typer.Typer(help="External intel report commands.")
intel_sources_app = typer.Typer(help="Configured public intel sources.")
intel_x_app = typer.Typer(help="Official X API external intel collection.")
hindsight_app = typer.Typer(help="Historical hindsight case-study tools.")
app.add_typer(intel_capture_app, name="intel-capture")
app.add_typer(intel_report_app, name="intel-report")
app.add_typer(intel_sources_app, name="intel-sources")
app.add_typer(intel_x_app, name="intel-x")
app.add_typer(hindsight_app, name="hindsight")


def _load(config: Path):
    return load_config(config)


def _parse_iso_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter("Expected date in YYYY-MM-DD format") from exc


def _demo_init_command(
    *,
    date_: str,
    config: Path,
    reset: bool,
    force_reset: bool,
    launch_ui: bool,
    port: int,
) -> None:
    from sectorscout.demo import run_demo_init

    loaded = _load(config)
    parsed_date = _parse_iso_date(date_)
    assert parsed_date is not None
    try:
        result = run_demo_init(loaded, asof_date=parsed_date, reset=reset, force_reset=force_reset)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from None
    typer.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    if launch_ui:
        _launch_streamlit(config, port)


@app.command()
def version() -> None:
    """Show the SectorScout package version."""
    typer.echo(__version__)


@app.command("config-hash")
def config_hash_command(config: Path = typer.Option(Path("config.yaml"), "--config")) -> None:
    """Print the deterministic hash for a config file."""
    loaded = _load(config)
    typer.echo(config_hash(loaded))


@app.command("market-close")
def market_close(
    session_date: str = typer.Argument(...),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """Print the NYSE session close in UTC and market-local time."""
    from sectorscout.market_calendar import asof_market_close, to_market_time

    loaded = _load(config)
    parsed_date = _parse_iso_date(session_date)
    assert parsed_date is not None
    close_utc = asof_market_close(parsed_date, loaded)
    close_local = to_market_time(close_utc, loaded)
    typer.echo(
        json.dumps(
            {
                "session_date": parsed_date.isoformat(),
                "calendar": loaded.market.calendar,
                "market_timezone": loaded.market.timezone,
                "close_utc": close_utc.isoformat(),
                "close_market_time": close_local.isoformat(),
            },
            indent=2,
            sort_keys=True,
        )
    )


@app.command("init-db")
def init_db(config: Path = typer.Option(Path("config.yaml"), "--config")) -> None:
    """Initialize the Phase 0 DuckDB schema."""
    from sectorscout.db import initialize_database

    loaded = _load(config)
    db_path = initialize_database(loaded)
    typer.echo(f"Initialized DuckDB schema at {db_path}")


@app.command("demo-init")
def demo_init(
    date_: str = typer.Option(DEFAULT_DEMO_ASOF_DATE.isoformat(), "--date"),
    reset: bool = typer.Option(False, "--reset/--no-reset"),
    force_reset: bool = typer.Option(False, "--force-reset/--no-force-reset"),
    launch_ui: bool = typer.Option(False, "--launch-ui/--no-launch-ui"),
    port: int = typer.Option(8501, "--port"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """Initialize fixture data, seed sample intel, and generate the demo report."""
    _demo_init_command(date_=date_, config=config, reset=reset, force_reset=force_reset, launch_ui=launch_ui, port=port)


@app.command("quickstart")
def quickstart(
    date_: str = typer.Option(DEFAULT_DEMO_ASOF_DATE.isoformat(), "--date"),
    reset: bool = typer.Option(False, "--reset/--no-reset"),
    force_reset: bool = typer.Option(False, "--force-reset/--no-force-reset"),
    launch_ui: bool = typer.Option(False, "--launch-ui/--no-launch-ui"),
    port: int = typer.Option(8501, "--port"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """One-command local demo setup for a non-blank dashboard."""
    _demo_init_command(date_=date_, config=config, reset=reset, force_reset=force_reset, launch_ui=launch_ui, port=port)


@app.command("demo-status")
def demo_status(
    date_: str = typer.Option(DEFAULT_DEMO_ASOF_DATE.isoformat(), "--date"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """Show whether the local demo/dashboard is ready."""
    from sectorscout.demo import demo_readiness

    loaded = _load(config)
    parsed_date = _parse_iso_date(date_)
    assert parsed_date is not None
    typer.echo(json.dumps(demo_readiness(loaded, asof_date=parsed_date), indent=2, sort_keys=True))


@app.command("metadata")
def metadata(
    command: str = typer.Option("metadata", "--command"),
    asof: str | None = typer.Option(None, "--asof"),
    persist: bool = typer.Option(True, "--persist/--no-persist"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """Generate Phase 0 reproducibility metadata."""
    from sectorscout.db import persist_run_metadata
    from sectorscout.metadata import build_run_metadata

    loaded = _load(config)
    payload = build_run_metadata(loaded, command=command, asof_date=_parse_iso_date(asof))
    if persist:
        persist_run_metadata(loaded, payload)
    typer.echo(payload.to_json())


@app.command()
def init(config: Path = typer.Option(Path("config.yaml"), "--config")) -> None:
    """Phase 0 init alias for database initialization."""
    init_db(config=config)


def _phase0_not_implemented(name: str) -> None:
    typer.echo(f"{name} is intentionally not implemented in Phase 0.")


@app.command("refresh-universe")
def refresh_universe(
    from_csv: Path | None = typer.Option(None, "--from-csv"),
    provider: str = typer.Option("fixture", "--provider"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    from sectorscout.ingest import ingest_universe_csv

    loaded = _load(config)
    if from_csv is None:
        _phase0_not_implemented("live refresh-universe")
        return
    count = ingest_universe_csv(loaded, from_csv, provider=provider)
    typer.echo(f"Ingested {count} symbols from {from_csv}")


@app.command("ingest-prices")
def ingest_prices(
    from_csv: Path | None = typer.Option(None, "--from-csv"),
    provider: str = typer.Option("fixture", "--provider"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    from sectorscout.ingest import ingest_prices_csv

    loaded = _load(config)
    if from_csv is None:
        _phase0_not_implemented("live ingest-prices")
        return
    count = ingest_prices_csv(loaded, from_csv, provider=provider)
    typer.echo(f"Ingested {count} price rows from {from_csv}")


@app.command("ingest-corporate-actions")
def ingest_corporate_actions(
    from_csv: Path | None = typer.Option(None, "--from-csv"),
    source: str = typer.Option("fixture", "--source"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    from sectorscout.ingest import ingest_corporate_actions_csv

    loaded = _load(config)
    if from_csv is None:
        _phase0_not_implemented("live ingest-corporate-actions")
        return
    count = ingest_corporate_actions_csv(loaded, from_csv, source=source)
    typer.echo(f"Ingested {count} corporate action rows from {from_csv}")


@app.command("ingest-fundamentals")
def ingest_fundamentals(
    from_csv: Path | None = typer.Option(None, "--from-csv"),
    source: str = typer.Option("fixture", "--source"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    from sectorscout.ingest import ingest_fundamental_facts_csv

    loaded = _load(config)
    if from_csv is None:
        _phase0_not_implemented("live ingest-fundamentals")
        return
    count = ingest_fundamental_facts_csv(loaded, from_csv, source=source)
    typer.echo(f"Ingested {count} fundamental fact rows from {from_csv}")


@app.command("ingest-themes")
def ingest_themes(
    from_csv: Path | None = typer.Option(None, "--from-csv"),
    source: str = typer.Option("fixture", "--source"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    from sectorscout.ingest import ingest_themes_csv

    loaded = _load(config)
    if from_csv is None:
        _phase0_not_implemented("live ingest-themes")
        return
    count = ingest_themes_csv(loaded, from_csv, source=source)
    typer.echo(f"Ingested {count} theme rows from {from_csv}")


@app.command("ingest-theme-members")
def ingest_theme_members(
    from_csv: Path | None = typer.Option(None, "--from-csv"),
    source: str = typer.Option("fixture", "--source"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    from sectorscout.ingest import ingest_theme_members_csv

    loaded = _load(config)
    if from_csv is None:
        _phase0_not_implemented("live ingest-theme-members")
        return
    count = ingest_theme_members_csv(loaded, from_csv, source=source)
    typer.echo(f"Ingested {count} theme member rows from {from_csv}")


@app.command("compute-indicators")
def compute_indicators_command(
    asof: str = typer.Option(..., "--asof"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    from sectorscout.indicators import compute_technical_indicators

    loaded = _load(config)
    parsed_asof = _parse_iso_date(asof)
    assert parsed_asof is not None
    rows = compute_technical_indicators(loaded, parsed_asof, persist=True)
    typer.echo(
        json.dumps(
            {
                "asof_date": parsed_asof.isoformat(),
                "indicator_rows": len(rows),
                "symbols": [row.symbol for row in rows],
            },
            indent=2,
            sort_keys=True,
        )
    )


@app.command("market-regime")
def market_regime_command(
    asof: str = typer.Option(..., "--asof"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    from sectorscout.market_regime import compute_market_regime

    loaded = _load(config)
    parsed_asof = _parse_iso_date(asof)
    assert parsed_asof is not None
    regime = compute_market_regime(loaded, parsed_asof, persist=True)
    typer.echo(json.dumps(regime.to_dict(), indent=2, sort_keys=True))


@app.command()
def score(
    asof: str = typer.Option(..., "--asof"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    from sectorscout.scoring import run_scoring

    loaded = _load(config)
    parsed_asof = _parse_iso_date(asof)
    assert parsed_asof is not None
    result = run_scoring(loaded, parsed_asof)
    typer.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True))


@app.command("detect-setups")
def detect_setups_command(
    asof: str = typer.Option(..., "--asof"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    from sectorscout.setups import detect_setups

    loaded = _load(config)
    parsed_asof = _parse_iso_date(asof)
    assert parsed_asof is not None
    result = detect_setups(loaded, parsed_asof, persist=True)
    typer.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True))


@app.command()
def report(
    date_: str = typer.Option(..., "--date"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    from sectorscout.reports import generate_daily_report

    loaded = _load(config)
    parsed_date = _parse_iso_date(date_)
    assert parsed_date is not None
    typer.echo(generate_daily_report(loaded, parsed_date).to_json())


@app.command()
def backtest() -> None:
    _phase0_not_implemented("backtest")


@hindsight_app.command("write-default-cases")
def hindsight_write_default_cases(
    path: Path = typer.Option(DEFAULT_HINDSIGHT_CASES_PATH, "--path"),
) -> None:
    """Write the default hindsight leader case-study seed file."""
    from sectorscout.hindsight import write_default_hindsight_cases

    typer.echo(str(write_default_hindsight_cases(path)))


@hindsight_app.command("seed")
def hindsight_seed(
    path: Path = typer.Option(DEFAULT_HINDSIGHT_CASES_PATH, "--path"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """Persist the hindsight case-study seed list into DuckDB."""
    from sectorscout.hindsight import seed_hindsight_cases

    loaded = _load(config)
    count = seed_hindsight_cases(loaded, path)
    typer.echo(json.dumps({"seeded_cases": count, "path": str(path)}, indent=2, sort_keys=True))


@hindsight_app.command("scan")
def hindsight_scan(
    path: Path = typer.Option(DEFAULT_HINDSIGHT_CASES_PATH, "--path"),
    persist: bool = typer.Option(True, "--persist/--no-persist"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """Run case-study hindsight diagnostics for configured historical leader examples."""
    from sectorscout.hindsight import scan_hindsight_cases

    loaded = _load(config)
    results = scan_hindsight_cases(loaded, path=path, persist=persist)
    typer.echo(json.dumps([result.to_dict() for result in results], indent=2, sort_keys=True))


@hindsight_app.command("seed-events")
def hindsight_seed_events(
    path: Path = typer.Option(DEFAULT_HINDSIGHT_CASES_PATH, "--path"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """Persist the event ledger seed for historical pattern cases."""
    from sectorscout.hindsight import seed_hindsight_events

    loaded = _load(config)
    count = seed_hindsight_events(loaded, path)
    typer.echo(json.dumps({"seeded_events": count, "path": str(path)}, indent=2, sort_keys=True))


@hindsight_app.command("build-gates")
def hindsight_build_gates(
    path: Path = typer.Option(DEFAULT_HINDSIGHT_CASES_PATH, "--path"),
    persist: bool = typer.Option(True, "--persist/--no-persist"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """Build audit-friendly replay gate rows for historical pattern cases."""
    from sectorscout.hindsight import build_hindsight_replay_gates

    loaded = _load(config)
    gates = build_hindsight_replay_gates(loaded, path=path, persist=persist)
    typer.echo(json.dumps([gate.to_dict() for gate in gates], indent=2, sort_keys=True))


@hindsight_app.command("seed-evidence")
def hindsight_seed_evidence(
    path: Path = typer.Option(DEFAULT_HINDSIGHT_CASES_PATH, "--path"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """Persist point-in-time industry/fundamental evidence rows for historical cases."""
    from sectorscout.hindsight import seed_hindsight_evidence

    loaded = _load(config)
    count = seed_hindsight_evidence(loaded, path)
    typer.echo(json.dumps({"seeded_evidence": count, "path": str(path)}, indent=2, sort_keys=True))


@hindsight_app.command("build-observation-links")
def hindsight_build_observation_links(
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """Link pattern observations to official evidence and computed replay gates."""
    from sectorscout.hindsight import build_hindsight_observation_links

    loaded = _load(config)
    links = build_hindsight_observation_links(loaded)
    typer.echo(json.dumps([link.to_dict() for link in links], indent=2, sort_keys=True))


@hindsight_app.command("build-hypotheses")
def hindsight_build_hypotheses(
    path: Path = typer.Option(DEFAULT_HINDSIGHT_CASES_PATH, "--path"),
    persist: bool = typer.Option(True, "--persist/--no-persist"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """Build cross-case replay hypothesis registry rows."""
    from sectorscout.hindsight import build_hindsight_hypothesis_registry

    loaded = _load(config)
    hypotheses, case_results = build_hindsight_hypothesis_registry(loaded, path=path, persist=persist)
    typer.echo(
        json.dumps(
            {
                "hypotheses": [hypothesis.to_dict() for hypothesis in hypotheses],
                "case_results": [result.to_dict() for result in case_results],
            },
            indent=2,
            sort_keys=True,
        )
    )


@hindsight_app.command("fetch-prices")
def hindsight_fetch_prices(
    path: Path = typer.Option(DEFAULT_HINDSIGHT_CASES_PATH, "--path"),
    provider: str = typer.Option("yahoo_chart_public", "--provider"),
    lookback_days: int = typer.Option(320, "--lookback-days", min=0),
    include_benchmarks: bool = typer.Option(
        True,
        "--include-benchmarks/--no-include-benchmarks",
        help="Also fetch fixed benchmark symbols used by replay gates, such as SMH or QQQ.",
    ),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """Fetch public daily prices for historical cases and fixed replay benchmarks."""
    from sectorscout.hindsight import fetch_hindsight_prices

    loaded = _load(config)
    result = fetch_hindsight_prices(
        loaded,
        path=path,
        provider=provider,
        lookback_days=lookback_days,
        include_benchmarks=include_benchmarks,
    )
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@hindsight_app.command("playbook")
def hindsight_playbook(
    output_dir: Path = typer.Option(Path("data/hindsight/reports"), "--output-dir"),
    source_snapshot_dir: Path = typer.Option(Path("data/hindsight/sources"), "--source-snapshot-dir"),
    asof: str | None = typer.Option(None, "--asof"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """Generate a Markdown pattern playbook from current hindsight evidence."""
    from sectorscout.hindsight_playbook import generate_hindsight_pattern_playbook

    loaded = _load(config)
    path = generate_hindsight_pattern_playbook(
        loaded,
        output_dir=output_dir,
        asof_date=_parse_iso_date(asof),
        source_snapshot_dir=source_snapshot_dir,
    )
    typer.echo(str(path))


@hindsight_app.command("refresh")
def hindsight_refresh(
    path: Path = typer.Option(DEFAULT_HINDSIGHT_CASES_PATH, "--path"),
    output_dir: Path = typer.Option(Path("data/hindsight/reports"), "--output-dir"),
    asof: str | None = typer.Option(None, "--asof"),
    fetch_prices: bool = typer.Option(True, "--fetch-prices/--no-fetch-prices"),
    provider: str = typer.Option("yahoo_chart_public", "--provider"),
    lookback_days: int = typer.Option(320, "--lookback-days", min=0),
    include_benchmarks: bool = typer.Option(True, "--include-benchmarks/--no-include-benchmarks"),
    check_sources: bool = typer.Option(False, "--check-sources/--no-check-sources"),
    fetch_sec_metadata: bool = typer.Option(False, "--fetch-sec-metadata/--no-fetch-sec-metadata"),
    fetch_companyfacts: bool = typer.Option(False, "--fetch-companyfacts/--no-fetch-companyfacts"),
    snapshot_sources: bool = typer.Option(False, "--snapshot-sources/--no-snapshot-sources"),
    source_snapshot_dir: Path = typer.Option(Path("data/hindsight/sources"), "--source-snapshot-dir"),
    strict_price_fetch: bool = typer.Option(False, "--strict-price-fetch/--no-strict-price-fetch"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """Run the complete hindsight lab refresh and export the research playbook."""
    from sectorscout.hindsight_workflow import run_hindsight_refresh

    loaded = _load(config)
    result = run_hindsight_refresh(
        loaded,
        path=path,
        output_dir=output_dir,
        asof_date=_parse_iso_date(asof),
        fetch_prices=fetch_prices,
        provider=provider,
        lookback_days=lookback_days,
        include_benchmarks=include_benchmarks,
        continue_on_price_error=not strict_price_fetch,
        check_sources=check_sources,
        fetch_sec_metadata=fetch_sec_metadata,
        fetch_companyfacts=fetch_companyfacts,
        snapshot_sources=snapshot_sources,
        source_snapshot_dir=source_snapshot_dir,
    )
    typer.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True))


@hindsight_app.command("sec-metadata")
def hindsight_sec_metadata(
    fetch_remote: bool = typer.Option(False, "--fetch-remote/--no-fetch-remote"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """Build SEC filing metadata rows for hindsight event source URLs."""
    from sectorscout.hindsight_sec_metadata import build_hindsight_sec_filing_metadata

    loaded = _load(config)
    rows = build_hindsight_sec_filing_metadata(loaded, fetch_remote=fetch_remote)
    typer.echo(json.dumps([row.to_dict() for row in rows], indent=2, sort_keys=True))


@hindsight_app.command("companyfacts")
def hindsight_companyfacts(
    fetch_remote: bool = typer.Option(False, "--fetch-remote/--no-fetch-remote"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """Build SEC companyfacts candidate rows for hindsight review."""
    from sectorscout.hindsight_companyfacts import build_hindsight_companyfacts, companyfacts_summary

    loaded = _load(config)
    candidates, statuses = build_hindsight_companyfacts(loaded, fetch_remote=fetch_remote)
    payload = {
        "summary": companyfacts_summary(candidates, statuses),
        "status": [status.to_dict() for status in statuses],
        "candidates": [candidate.to_dict() for candidate in candidates],
    }
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


@hindsight_app.command("snapshot-sources")
def hindsight_snapshot_sources(
    output_dir: Path = typer.Option(Path("data/hindsight/sources"), "--output-dir"),
    include_review_required: bool = typer.Option(
        True,
        "--include-review-required/--no-include-review-required",
    ),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """Fetch public official-source snapshots for hindsight evidence review."""
    from sectorscout.hindsight_source_snapshot import fetch_hindsight_source_snapshots

    loaded = _load(config)
    snapshots = fetch_hindsight_source_snapshots(
        loaded,
        output_dir=output_dir,
        include_review_required=include_review_required,
    )
    typer.echo(json.dumps([snapshot.to_dict() for snapshot in snapshots], indent=2, sort_keys=True))


@app.command("execution-decisions")
def execution_decisions(
    asof: str = typer.Option(..., "--asof"),
    price_snapshot_id: str | None = typer.Option(None, "--price-snapshot-id"),
    persist: bool = typer.Option(True, "--persist/--no-persist"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    from sectorscout.execution import generate_execution_decisions

    loaded = _load(config)
    parsed_asof = _parse_iso_date(asof)
    assert parsed_asof is not None
    result = generate_execution_decisions(
        loaded,
        parsed_asof,
        persist=persist,
        price_snapshot_id=price_snapshot_id,
    )
    typer.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True))


@app.command("position-lifecycle")
def position_lifecycle(
    execution_run_id: str = typer.Option(..., "--execution-run-id"),
    through: str = typer.Option(..., "--through"),
    price_snapshot_id: str | None = typer.Option(None, "--price-snapshot-id"),
    persist: bool = typer.Option(True, "--persist/--no-persist"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    from sectorscout.lifecycle import generate_position_lifecycle

    loaded = _load(config)
    through_date = _parse_iso_date(through)
    assert through_date is not None
    result = generate_position_lifecycle(
        loaded,
        execution_run_id,
        through_date,
        persist=persist,
        price_snapshot_id=price_snapshot_id,
    )
    typer.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True))


@app.command("trade-ledger-qa")
def trade_ledger_qa(
    lifecycle_run_id: str = typer.Option(..., "--lifecycle-run-id"),
    persist: bool = typer.Option(True, "--persist/--no-persist"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    from sectorscout.ledger import generate_trade_ledger_qa

    loaded = _load(config)
    result = generate_trade_ledger_qa(
        loaded,
        lifecycle_run_id,
        persist=persist,
    )
    typer.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True))


@app.command("price-snapshot")
def price_snapshot(
    asof: str = typer.Option(..., "--asof"),
    persist: bool = typer.Option(True, "--persist/--no-persist"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    from sectorscout.prices import create_frozen_price_snapshot

    loaded = _load(config)
    parsed_asof = _parse_iso_date(asof)
    assert parsed_asof is not None
    result = create_frozen_price_snapshot(loaded, parsed_asof, persist=persist)
    typer.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True))


@app.command()
def validate() -> None:
    _phase0_not_implemented("validate")


def _launch_streamlit(config: Path, port: int) -> None:
    source_app_path = Path.cwd() / "src" / "sectorscout" / "ui" / "app.py"
    app_path = source_app_path if source_app_path.exists() else Path(__file__).with_name("ui") / "app.py"
    env = os.environ.copy()
    if source_app_path.exists():
        source_path = str(Path.cwd() / "src")
        env["PYTHONPATH"] = f"{source_path}{os.pathsep}{env['PYTHONPATH']}" if env.get("PYTHONPATH") else source_path
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_path),
        "--server.headless",
        "true",
        "--server.port",
        str(port),
        "--server.fileWatcherType",
        "none",
        "--browser.gatherUsageStats",
        "false",
        "--",
        "--config",
        str(config),
    ]
    try:
        completed = subprocess.run(command, check=False, env=env)
    except ModuleNotFoundError:
        typer.echo("Streamlit is not installed. Run: uv sync --extra dev --no-editable", err=True)
        raise typer.Exit(code=1) from None
    if completed.returncode != 0:
        raise typer.Exit(code=completed.returncode)


@app.command()
def dashboard(
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    port: int = typer.Option(8501, "--port"),
) -> None:
    """Launch the local Streamlit research dashboard."""
    _launch_streamlit(config, port)


@app.command()
def ui(
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    port: int = typer.Option(8501, "--port"),
) -> None:
    """Launch the local Streamlit research dashboard."""
    _launch_streamlit(config, port)


@app.command("intel-ui")
def intel_ui(
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    port: int = typer.Option(8501, "--port"),
) -> None:
    """Launch the local Streamlit intel dashboard."""
    _launch_streamlit(config, port)


@intel_capture_app.command("add-file")
def intel_capture_add_file(
    path: Path = typer.Argument(...),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """Capture a markdown/text/image file into the intel overlay."""
    from sectorscout.intel.capture_inbox import capture_image_file, capture_markdown_file, capture_text
    from sectorscout.intel.storage import ensure_intel_tables

    loaded = _load(config)
    ensure_intel_tables(loaded)
    suffix = path.suffix.lower()
    if suffix == ".md":
        raw_item_id, view_id = capture_markdown_file(loaded, path)
        typer.echo(json.dumps({"raw_item_id": raw_item_id, "intel_view_id": view_id}, indent=2))
        return
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        media_id = capture_image_file(loaded, path)
        typer.echo(json.dumps({"media_id": media_id}, indent=2))
        return
    raw_item_id, view_id = capture_text(
        loaded,
        path.read_text(encoding="utf-8"),
        source_id="manual_file",
        platform="other",
        title=path.name,
    )
    typer.echo(json.dumps({"raw_item_id": raw_item_id, "intel_view_id": view_id}, indent=2))


@intel_capture_app.command("list")
def intel_capture_list(config: Path = typer.Option(Path("config.yaml"), "--config")) -> None:
    """List captured raw intel rows."""
    from sectorscout.intel.storage import ensure_intel_tables

    loaded = _load(config)
    ensure_intel_tables(loaded)
    from sectorscout.db import connect_database

    with connect_database(loaded.database.path) as connection:
        rows = connection.execute(
            """
            SELECT raw_item_id, source_id, source_type, title, platform, url, collected_at
            FROM intel_raw_items
            ORDER BY collected_at DESC
            LIMIT 100
            """
        ).fetchall()
    typer.echo(
        json.dumps(
            [
                {
                    "raw_item_id": row[0],
                    "source_id": row[1],
                    "source_type": row[2],
                    "title": row[3],
                    "platform": row[4],
                    "url": row[5],
                    "collected_at": str(row[6]),
                }
                for row in rows
            ],
            indent=2,
        )
    )


@intel_capture_app.command("add-url")
def intel_capture_add_url(
    url: str = typer.Argument(...),
    source_id: str | None = typer.Option(None, "--source-id"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """Capture one public URL into the intel overlay."""
    from sectorscout.intel.public_web import collect_public_url
    from sectorscout.intel.storage import ensure_intel_tables

    loaded = _load(config)
    ensure_intel_tables(loaded)
    result = collect_public_url(loaded, url, source_id=source_id)
    typer.echo(json.dumps(result.__dict__, indent=2))


@intel_sources_app.command("list")
def intel_sources_list(
    sources_file: Path = typer.Option(DEFAULT_PUBLIC_SOURCES_PATH, "--sources-file"),
) -> None:
    """List configured public intel sources."""
    from sectorscout.intel.public_sources import load_public_sources

    typer.echo(
        json.dumps(
            [source.to_dict() for source in load_public_sources(sources_file)],
            indent=2,
            sort_keys=True,
        )
    )


@intel_sources_app.command("collect")
def intel_sources_collect(
    source_id: str = typer.Option("all", "--source"),
    sources_file: Path = typer.Option(DEFAULT_PUBLIC_SOURCES_PATH, "--sources-file"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """Collect configured public sources without login or private browsing."""
    from sectorscout.intel.public_sources import collect_public_sources
    from sectorscout.intel.storage import ensure_intel_tables

    loaded = _load(config)
    ensure_intel_tables(loaded)
    results = collect_public_sources(loaded, sources_path=sources_file, source_id=source_id)
    typer.echo(json.dumps([result.__dict__ for result in results], indent=2))


@intel_x_app.command("status")
def intel_x_status(
    sources_file: Path = typer.Option(DEFAULT_X_SOURCES_PATH, "--sources-file"),
) -> None:
    """Show X API configuration status without making network requests."""
    from sectorscout.intel.x_collector import load_x_sources, x_api_status

    typer.echo(
        json.dumps(
            {
                **x_api_status(),
                "sources": [source.to_dict() for source in load_x_sources(sources_file)],
                "compliance": [
                    "official X API only",
                    "public posts only",
                    "no browser-login scraping",
                    "no cookie/session scraping",
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )


@intel_x_app.command("collect")
def intel_x_collect(
    handles: str | None = typer.Option(None, "--handles", help="Comma-separated X handles. Defaults to data/intel/x_sources.yaml."),
    max_results: int = typer.Option(50, "--max-results", min=10, max=100),
    include_replies: bool = typer.Option(False, "--include-replies/--no-include-replies"),
    include_retweets: bool = typer.Option(False, "--include-retweets/--no-include-retweets"),
    sources_file: Path = typer.Option(DEFAULT_X_SOURCES_PATH, "--sources-file"),
    date_: str | None = typer.Option(None, "--date"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """Collect recent public X posts using the official X Recent Search API."""
    from sectorscout.intel.storage import ensure_intel_tables
    from sectorscout.intel.x_collector import collect_x_recent_search

    loaded = _load(config)
    ensure_intel_tables(loaded)
    parsed_date = _parse_iso_date(date_)
    selected_handles = [item.strip() for item in handles.split(",") if item.strip()] if handles else None
    result = collect_x_recent_search(
        loaded,
        handles=selected_handles,
        sources_path=sources_file,
        asof_date=parsed_date,
        max_results=max_results,
        include_replies=include_replies,
        include_retweets=include_retweets,
    )
    typer.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True))


@app.command("intel-extract")
def intel_extract(
    date_: str = typer.Option(..., "--date"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """Seed and extract MVP external intel for a date."""
    from sectorscout.intel.chandler_seed import seed_chandler_fixture

    loaded = _load(config)
    parsed_date = _parse_iso_date(date_)
    assert parsed_date is not None
    raw_item_id = seed_chandler_fixture(loaded, asof_date=parsed_date)
    typer.echo(json.dumps({"seeded_raw_item_id": raw_item_id}, indent=2))


@intel_report_app.command("daily")
def intel_report_daily(
    date_: str = typer.Option(..., "--date"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """Generate the daily external intel markdown report."""
    from sectorscout.intel.report import generate_intel_daily_report

    loaded = _load(config)
    parsed_date = _parse_iso_date(date_)
    assert parsed_date is not None
    path = generate_intel_daily_report(loaded, parsed_date)
    typer.echo(str(path))


@app.command("data-quality")
def data_quality(
    asof: str = typer.Option(..., "--asof"),
    mode: str = typer.Option("live", "--mode"),
    persist: bool = typer.Option(True, "--persist/--no-persist"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    from sectorscout.data_quality import compute_data_quality, compute_historical_data_quality, persist_data_quality

    loaded = _load(config)
    parsed_asof = _parse_iso_date(asof)
    assert parsed_asof is not None
    if mode == "historical":
        report = compute_historical_data_quality(loaded, parsed_asof)
    elif mode == "live":
        report = compute_data_quality(loaded, parsed_asof)
    else:
        raise typer.BadParameter("--mode must be live or historical")
    if persist:
        persist_data_quality(loaded, report)
    typer.echo(report.to_json())


@app.command("universe-asof")
def universe_asof_command(
    asof: str = typer.Option(..., "--asof"),
    mode: str = typer.Option("historical", "--mode"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    from sectorscout.pit import universe_asof

    loaded = _load(config)
    parsed_asof = _parse_iso_date(asof)
    assert parsed_asof is not None
    typer.echo(
        json.dumps(
            {
                "asof_date": parsed_asof.isoformat(),
                "mode": mode,
                "symbols": universe_asof(loaded, parsed_asof, mode=mode),
            },
            indent=2,
        )
    )


@app.command("theme-members-asof")
def theme_members_asof_command(
    asof: str = typer.Option(..., "--asof"),
    allow_historical_ex_post: bool = typer.Option(False, "--allow-historical-ex-post"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    from sectorscout.pit import theme_members_asof

    loaded = _load(config)
    parsed_asof = _parse_iso_date(asof)
    assert parsed_asof is not None
    rows = theme_members_asof(
        loaded,
        parsed_asof,
        allow_historical_ex_post=allow_historical_ex_post,
    )
    typer.echo(json.dumps({"asof_date": parsed_asof.isoformat(), "members": rows}, default=str, indent=2))


@app.command("fundamentals-asof")
def fundamentals_asof_command(
    asof: str = typer.Option(..., "--asof"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    from sectorscout.pit import available_fundamental_facts

    loaded = _load(config)
    parsed_asof = _parse_iso_date(asof)
    assert parsed_asof is not None
    rows = available_fundamental_facts(loaded, parsed_asof)
    typer.echo(json.dumps({"asof_date": parsed_asof.isoformat(), "facts": rows}, default=str, indent=2))


if __name__ == "__main__":
    app()
