from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import typer

from sectorscout import __version__
from sectorscout.audit import generate_provenance_audit_report, validate_audit_report
from sectorscout.config import config_hash, load_config
from sectorscout.data_quality import (
    compute_data_quality,
    compute_historical_data_quality,
    persist_data_quality,
)
from sectorscout.db import initialize_database, persist_run_metadata
from sectorscout.execution import generate_execution_decisions
from sectorscout.ingest import (
    ingest_corporate_actions_csv,
    ingest_fundamental_facts_csv,
    ingest_prices_csv,
    ingest_theme_members_csv,
    ingest_themes_csv,
    ingest_universe_csv,
)
from sectorscout.indicators import compute_technical_indicators
from sectorscout.ledger import generate_trade_ledger_qa
from sectorscout.lifecycle import generate_position_lifecycle
from sectorscout.lifecycle_inputs import create_frozen_lifecycle_input_snapshot
from sectorscout.market_regime import compute_market_regime
from sectorscout.market_calendar import asof_market_close, to_market_time
from sectorscout.metadata import build_run_metadata
from sectorscout.pit import available_fundamental_facts, theme_members_asof, universe_asof
from sectorscout.prices import create_frozen_price_snapshot
from sectorscout.reports import generate_daily_report
from sectorscout.run_manifest import generate_run_manifest, validate_run_manifest
from sectorscout.scoring import run_scoring
from sectorscout.setups import detect_setups

app = typer.Typer(help="SectorScout research system CLI.")


def _load(config: Path):
    return load_config(config)


def _parse_iso_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter("Expected date in YYYY-MM-DD format") from exc


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
    loaded = _load(config)
    db_path = initialize_database(loaded)
    typer.echo(f"Initialized DuckDB schema at {db_path}")


@app.command("metadata")
def metadata(
    command: str = typer.Option("metadata", "--command"),
    asof: str | None = typer.Option(None, "--asof"),
    persist: bool = typer.Option(True, "--persist/--no-persist"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """Generate Phase 0 reproducibility metadata."""
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
    loaded = _load(config)
    parsed_date = _parse_iso_date(date_)
    assert parsed_date is not None
    typer.echo(generate_daily_report(loaded, parsed_date).to_json())


@app.command()
def backtest() -> None:
    _phase0_not_implemented("backtest")


@app.command("execution-decisions")
def execution_decisions(
    asof: str = typer.Option(..., "--asof"),
    price_snapshot_id: str | None = typer.Option(None, "--price-snapshot-id"),
    persist: bool = typer.Option(True, "--persist/--no-persist"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
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
    lifecycle_input_snapshot_id: str | None = typer.Option(
        None,
        "--lifecycle-input-snapshot-id",
    ),
    persist: bool = typer.Option(True, "--persist/--no-persist"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    loaded = _load(config)
    through_date = _parse_iso_date(through)
    assert through_date is not None
    result = generate_position_lifecycle(
        loaded,
        execution_run_id,
        through_date,
        persist=persist,
        price_snapshot_id=price_snapshot_id,
        lifecycle_input_snapshot_id=lifecycle_input_snapshot_id,
    )
    typer.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True))


@app.command("trade-ledger-qa")
def trade_ledger_qa(
    lifecycle_run_id: str = typer.Option(..., "--lifecycle-run-id"),
    persist: bool = typer.Option(True, "--persist/--no-persist"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
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
    loaded = _load(config)
    parsed_asof = _parse_iso_date(asof)
    assert parsed_asof is not None
    result = create_frozen_price_snapshot(loaded, parsed_asof, persist=persist)
    typer.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True))


@app.command("lifecycle-input-snapshot")
def lifecycle_input_snapshot(
    execution_run_id: str = typer.Option(..., "--execution-run-id"),
    through: str = typer.Option(..., "--through"),
    persist: bool = typer.Option(True, "--persist/--no-persist"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    loaded = _load(config)
    through_date = _parse_iso_date(through)
    assert through_date is not None
    result = create_frozen_lifecycle_input_snapshot(
        loaded,
        execution_run_id,
        through_date,
        persist=persist,
    )
    typer.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True))


@app.command("run-manifest")
def run_manifest(
    lifecycle_run_id: str = typer.Option(..., "--lifecycle-run-id"),
    persist: bool = typer.Option(True, "--persist/--no-persist"),
    strict: bool = typer.Option(True, "--strict/--no-strict"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    loaded = _load(config)
    result = generate_run_manifest(
        loaded,
        lifecycle_run_id,
        persist=persist,
    )
    typer.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    if strict and result.validation_status != "PASS":
        raise typer.Exit(1)


@app.command("provenance-validate")
def provenance_validate(
    run_manifest_id: str = typer.Option(..., "--run-manifest-id"),
    strict: bool = typer.Option(True, "--strict/--no-strict"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    loaded = _load(config)
    result = validate_run_manifest(loaded, run_manifest_id)
    typer.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    if strict and result.validation_status != "PASS":
        raise typer.Exit(1)


@app.command("provenance-report")
def provenance_report(
    run_manifest_id: str = typer.Option(..., "--run-manifest-id"),
    strict: bool = typer.Option(True, "--strict/--no-strict"),
    persist: bool = typer.Option(True, "--persist/--no-persist"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    loaded = _load(config)
    result = generate_provenance_audit_report(
        loaded,
        run_manifest_id,
        strict=strict,
        persist=persist,
    )
    typer.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    if strict and (
        result.validation_status != "PASS"
        or result.audit_completeness_status != "PASS"
        or not result.audit_exported
    ):
        raise typer.Exit(1)


@app.command("audit-report-validate")
def audit_report_validate(
    audit_report_id: str = typer.Option(..., "--audit-report-id"),
    strict: bool = typer.Option(True, "--strict/--no-strict"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    loaded = _load(config)
    result = validate_audit_report(loaded, audit_report_id)
    typer.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    if strict and result.validation_status != "PASS":
        raise typer.Exit(1)


@app.command()
def validate() -> None:
    _phase0_not_implemented("validate")


@app.command()
def dashboard() -> None:
    _phase0_not_implemented("dashboard")


@app.command("data-quality")
def data_quality(
    asof: str = typer.Option(..., "--asof"),
    mode: str = typer.Option("live", "--mode"),
    persist: bool = typer.Option(True, "--persist/--no-persist"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
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
    loaded = _load(config)
    parsed_asof = _parse_iso_date(asof)
    assert parsed_asof is not None
    rows = available_fundamental_facts(loaded, parsed_asof)
    typer.echo(json.dumps({"asof_date": parsed_asof.isoformat(), "facts": rows}, default=str, indent=2))


if __name__ == "__main__":
    app()
