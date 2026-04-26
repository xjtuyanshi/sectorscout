# SectorScout

SectorScout is a staged, point-in-time research system for finding strong market
themes, ranking strong stocks inside those themes, and waiting for technical
setups before labeling anything actionable.

Current status: Phase 5A foundation is implemented. It creates next-open
execution-decision records from frozen Phase 4 triggered setup candidates, but it
does not calculate strategy performance.

Implemented so far:

- Phase 0: project skeleton, config loading, config hash, git metadata, NYSE
  calendar handling, market-close timestamps, UTC storage, CLI, DuckDB schema,
  fixture directory, pytest coverage.
- Phase 1: fixture ingestion for universe, adjusted OHLCV prices, corporate
  actions, provider visibility, and daily data-quality reporting.
- Phase 2: point-in-time fundamentals, point-in-time theme membership, dynamic
  theme discovery dates, historical ex-post theme filtering, and symbol lifecycle
  as-of filtering.
- Phase 3: technical indicators, Stage 2 classification, relative strength,
  market regime, ThemeScore, StockOpportunityScore, and watchlist state machine.
- Phase 4: deterministic VCP/Pullback/Earnings Gap Base setup scaffolding,
  separated market/portfolio gates, signal categories, and daily report output.
- Pre-Phase-5 hardening: provider-priority price snapshots, ranked portfolio
  candidate selection, earnings timestamp to gap-session mapping, historical
  data-quality reporting, and additional PIT/execution-boundary tests.
- Phase 5A: read-only next-open execution-decision skeleton for frozen triggered
  setup candidates, including next-session open lookup, entry-extension rejects,
  stop validation, initial risk checks, and `execution_decisions` persistence.

Not implemented yet:

- Formal Phase 5 backtesting: exits, baselines, ablation, rolling/expanding
  validation, CAGR, Sharpe, max drawdown, annual returns, or performance
  conclusions.
- Phase 6: Streamlit dashboard, documentation polish, CI, and GitHub-ready demo
  workflow.
- Live data providers. Current ingestion is CSV/fixture based.

Current limitations:

- No true performance backtest, no performance claims, and no live provider
  ingestion.
- Triggered setups are labeled as research candidates only; `actionable` remains
  false in Phase 4 reports.
- Phase 5A `execution_decisions` recompute next-open risk/RR for candidate
  records only; they are not trade logs and not performance results.
- Earnings Gap Base requires a known public earnings timestamp, but event
  sourcing is still based on fixture fundamentals rather than a dedicated live
  earnings-events provider.
- Portfolio risk is still a scaffold: it handles per-day count and theme exposure
  sequencing, but not full open-position risk, sector exposure, or NEUTRAL sizing.
- Provider-priority price selection is currently an in-memory snapshot layer; it
  is not yet persisted as a frozen price snapshot table.

## Quick Start

```bash
uv venv --python 3.12
uv pip install '.[dev]'
.venv/bin/python -m pytest
```

Initialize a local fixture database:

```bash
rm -f data/sectorscout.duckdb data/sectorscout.duckdb.wal
.venv/bin/sectorscout init-db --config config.yaml
.venv/bin/sectorscout refresh-universe --from-csv data/fixtures/universe_sample.csv --provider fixture --config config.yaml
.venv/bin/sectorscout ingest-prices --from-csv data/fixtures/prices_sample.csv --provider fixture --config config.yaml
.venv/bin/sectorscout ingest-fundamentals --from-csv data/fixtures/fundamental_facts_sample.csv --source fixture --config config.yaml
.venv/bin/sectorscout ingest-themes --from-csv data/fixtures/themes_sample.csv --source fixture --config config.yaml
.venv/bin/sectorscout ingest-theme-members --from-csv data/fixtures/theme_members_sample.csv --source fixture --config config.yaml
```

Run the current research commands:

```bash
.venv/bin/sectorscout market-close 2024-11-29 --config config.yaml
.venv/bin/sectorscout universe-asof --asof 2024-11-29 --mode historical --config config.yaml
.venv/bin/sectorscout data-quality --asof 2024-11-29 --mode historical --config config.yaml
.venv/bin/sectorscout fundamentals-asof --asof 2024-11-29 --config config.yaml
.venv/bin/sectorscout theme-members-asof --asof 2024-11-29 --config config.yaml
.venv/bin/sectorscout score --asof 2024-11-29 --config config.yaml
.venv/bin/sectorscout detect-setups --asof 2024-11-29 --config config.yaml
.venv/bin/sectorscout execution-decisions --asof 2024-11-29 --config config.yaml
.venv/bin/sectorscout report --date 2024-11-29 --config config.yaml
```

The sample fixture price data is intentionally tiny, so the fixture report is
expected to be sparse. The setup detector is tested with synthetic 260-session
data in `tests/test_phase4_setups_report.py`.

## Review Focus

Important design constraints to review:

- Fundamentals and theme membership must be point-in-time.
- `historical_ex_post_theme` must not be used for unbiased historical discovery
  claims.
- Scores rank candidates; Phase 4 setup triggers create research candidates only.
- The system must not output direct buy labels or execution instructions.
- Phase 5A execution-decision records are not a performance backtest, so no
  performance claims should be inferred from the current repository.
