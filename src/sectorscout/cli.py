from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import typer

from sectorscout import __version__
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
from sectorscout.intel.capture_inbox import capture_image_file, capture_markdown_file, capture_text
from sectorscout.intel.chandler_seed import seed_chandler_fixture
from sectorscout.intel.public_sources import DEFAULT_PUBLIC_SOURCES_PATH, collect_public_sources, load_public_sources
from sectorscout.intel.public_web import collect_public_url
from sectorscout.intel.report import generate_intel_daily_report
from sectorscout.intel.storage import ensure_intel_tables
from sectorscout.ledger import generate_trade_ledger_qa
from sectorscout.lifecycle import generate_position_lifecycle
from sectorscout.market_regime import compute_market_regime
from sectorscout.market_calendar import asof_market_close, to_market_time
from sectorscout.metadata import build_run_metadata
from sectorscout.pit import available_fundamental_facts, theme_members_asof, universe_asof
from sectorscout.prices import create_frozen_price_snapshot
from sectorscout.reports import generate_daily_report
from sectorscout.scoring import run_scoring
from sectorscout.setups import detect_setups

app = typer.Typer(help="SectorScout research system CLI.")
intel_capture_app = typer.Typer(help="Human-in-the-loop external intel capture.")
intel_report_app = typer.Typer(help="External intel report commands.")
intel_sources_app = typer.Typer(help="Configured public intel sources.")
app.add_typer(intel_capture_app, name="intel-capture")
app.add_typer(intel_report_app, name="intel-report")
app.add_typer(intel_sources_app, name="intel-sources")


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


@app.command()
def validate() -> None:
    _phase0_not_implemented("validate")


def _launch_streamlit(config: Path, port: int) -> None:
    app_path = Path(__file__).with_name("ui") / "app.py"
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
        "--",
        "--config",
        str(config),
    ]
    try:
        completed = subprocess.run(command, check=False)
    except ModuleNotFoundError:
        typer.echo("Streamlit is not installed. Run: uv pip install -e '.[dev]'", err=True)
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
    loaded = _load(config)
    ensure_intel_tables(loaded)
    result = collect_public_url(loaded, url, source_id=source_id)
    typer.echo(json.dumps(result.__dict__, indent=2))


@intel_sources_app.command("list")
def intel_sources_list(
    sources_file: Path = typer.Option(DEFAULT_PUBLIC_SOURCES_PATH, "--sources-file"),
) -> None:
    """List configured public intel sources."""
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
    loaded = _load(config)
    ensure_intel_tables(loaded)
    results = collect_public_sources(loaded, sources_path=sources_file, source_id=source_id)
    typer.echo(json.dumps([result.__dict__ for result in results], indent=2))


@app.command("intel-extract")
def intel_extract(
    date_: str = typer.Option(..., "--date"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """Seed and extract MVP external intel for a date."""
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
