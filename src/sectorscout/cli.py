from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import typer

from sectorscout import __version__
from sectorscout.config import config_hash, load_config
from sectorscout.db import initialize_database, persist_run_metadata
from sectorscout.market_calendar import asof_market_close, to_market_time
from sectorscout.metadata import build_run_metadata

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
def refresh_universe() -> None:
    _phase0_not_implemented("refresh-universe")


@app.command("ingest-prices")
def ingest_prices() -> None:
    _phase0_not_implemented("ingest-prices")


@app.command("ingest-fundamentals")
def ingest_fundamentals() -> None:
    _phase0_not_implemented("ingest-fundamentals")


@app.command()
def score(asof: str | None = typer.Option(None, "--asof")) -> None:
    _parse_iso_date(asof)
    _phase0_not_implemented("score")


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


if __name__ == "__main__":
    app()
