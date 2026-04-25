from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import typer

from sectorscout import __version__
from sectorscout.config import config_hash, load_config
from sectorscout.data_quality import compute_data_quality, persist_data_quality
from sectorscout.db import initialize_database, persist_run_metadata
from sectorscout.ingest import (
    ingest_corporate_actions_csv,
    ingest_fundamental_facts_csv,
    ingest_prices_csv,
    ingest_theme_members_csv,
    ingest_themes_csv,
    ingest_universe_csv,
)
from sectorscout.indicators import compute_technical_indicators
from sectorscout.market_regime import compute_market_regime
from sectorscout.market_calendar import asof_market_close, to_market_time
from sectorscout.metadata import build_run_metadata
from sectorscout.pit import available_fundamental_facts, theme_members_asof, universe_asof
from sectorscout.scoring import run_scoring

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


@app.command()
def report(date_: str | None = typer.Option(None, "--date")) -> None:
    _parse_iso_date(date_)
    _phase0_not_implemented("report")


@app.command()
def backtest() -> None:
    _phase0_not_implemented("backtest")


@app.command()
def validate() -> None:
    _phase0_not_implemented("validate")


@app.command()
def dashboard() -> None:
    _phase0_not_implemented("dashboard")


@app.command("data-quality")
def data_quality(
    asof: str = typer.Option(..., "--asof"),
    persist: bool = typer.Option(True, "--persist/--no-persist"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    loaded = _load(config)
    parsed_asof = _parse_iso_date(asof)
    assert parsed_asof is not None
    report = compute_data_quality(loaded, parsed_asof)
    if persist:
        persist_data_quality(loaded, report)
    typer.echo(report.to_json())


@app.command("universe-asof")
def universe_asof_command(
    asof: str = typer.Option(..., "--asof"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    loaded = _load(config)
    parsed_asof = _parse_iso_date(asof)
    assert parsed_asof is not None
    typer.echo(json.dumps({"asof_date": parsed_asof.isoformat(), "symbols": universe_asof(loaded, parsed_asof)}, indent=2))


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
