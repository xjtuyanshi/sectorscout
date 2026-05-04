# SectorScout

SectorScout is a staged, point-in-time research system for finding strong market
themes, ranking strong stocks inside those themes, and waiting for technical
setups before labeling anything actionable.

Current status: Phase 5B8 reproducibility scaffolding is implemented. It creates
next-open execution-decision records from frozen Phase 4 triggered setup
candidates, then builds simulated position lifecycle, exit-decision,
trade-ledger QA, and baseline coverage QA records, with stronger run provenance,
persisted frozen price snapshot validation, non-price lifecycle input metadata
guardrails, frozen non-price lifecycle input snapshots, and run-manifest
rowset-hash validation. It does not calculate strategy performance.

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
  stop validation, initial risk checks, source-signal provenance preservation,
  run-level identity, and `execution_decisions` persistence.
- Phase 5B1: position lifecycle / exits skeleton reading only accepted execution
  decisions, with `simulated_positions`, `exit_decisions`, and
  `lifecycle_skips`, `baseline_price_series`, and QA summary scaffolding.
- Phase 5B2: trade ledger QA and baseline scaffold hardening reading only
  lifecycle/execution-run tables, with `trade_ledger`, `lifecycle_qa`, row-level
  gross R diagnostics, baseline coverage/provider mix QA, provenance blocks, and
  warning flags.
- Phase 5B3: reproducibility / provenance hardening, including run-level source
  signal metadata on `execution_runs`, richer `trade_ledger` identity fields,
  per-symbol baseline coverage QA, calendar-vs-trading-session holding counts,
  and `price_snapshot_runs` / `price_snapshot_rows` scaffolding.
- Phase 5B4/5B5: persisted price snapshot hardening, including CLI
  `--price-snapshot-id` wiring, fail-fast snapshot coverage checks, exact
  execution/lifecycle session validation, benchmark coverage validation,
  snapshot mismatch warnings, and rowset hash validation.
- Phase 5B6: non-price lifecycle input guardrails for `market_regime` and
  `theme_scores`, with metadata-set audit rows and ledger warnings when those
  live-table inputs do not match the source signal/execution provenance, when
  expected rows are missing, when lifecycle input QA is absent, or when source
  signal metadata is mixed.
- Phase 5B7: frozen non-price lifecycle input snapshot scaffolding for
  `market_regime` and `theme_scores`, with persisted required key coverage,
  source metadata sets, rowset hashes, fail-fast validation, lifecycle CLI
  wiring, and ledger provenance.
- Phase 5B8: run manifest / provenance validation scaffolding that ties
  together source-signal provenance, `execution_run_id`, execution-decision
  rowset hash, persisted `price_snapshot_id`, persisted
  `lifecycle_input_snapshot_id`, schema version, config hash, git commit, and
  validation status. The `provenance-validate` command fails if persisted
  manifest rowsets drift.

Not implemented yet:

- Formal Phase 5 backtesting: full baselines, ablation, rolling/expanding
  validation, performance metrics, or performance conclusions.
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
- Phase 5B1 lifecycle records are scaffolding for exit-rule QA, not a formal
  result report.
- Phase 5B1 skips lifecycle generation when `through_date` is before entry or
  when the local price path or entry-session price row is missing; those cases
  are recorded as lifecycle QA, not silently treated as open positions.
- Phase 5B2 `gross_r_multiple` is a row-level diagnostic field only. It is not
  aggregated into summary metrics or used as evidence of strategy quality.
- Phase 5B2 baseline rows are coverage/provider QA scaffolding only; they are
  not a benchmark return model.
- Phase 5B5 frozen price snapshot tables are still scaffolding, but
  execution/lifecycle can now use persisted `price_snapshot_id` rows with
  fail-fast coverage and rowset hash validation.
- `trade_ledger.gross_r_multiple` remains a row-level QA diagnostic before
  slippage/commission and is not aggregated into strategy metrics.
- Theme-score deterioration uses a strict consecutive market-session streak;
  missing theme-score rows reset the streak.
- `SIMULATED_NEXT_OPEN_ACCEPTED` means a research simulation record passed the
  current next-open filters; it is not a broker fill.
- Execution records store source signal metadata separately from execution-run
  metadata so changed configs do not overwrite the provenance of frozen signals.
- The default next-open policy allows opens below the trigger/pivot if other
  execution and risk filters pass. `require_next_open_above_trigger` can tighten
  that behavior for later research variants.
- Earnings Gap Base requires a known public earnings timestamp, but event
  sourcing is still based on fixture fundamentals rather than a dedicated live
  earnings-events provider.
- Portfolio risk is still a scaffold: it handles per-day count and theme exposure
  sequencing, but not full open-position risk, sector exposure, or NEUTRAL sizing.
- Provider-priority price selection can be persisted as frozen
  `price_snapshot_runs` / `price_snapshot_rows`. Market-regime and theme-score
  lifecycle inputs can now be persisted as frozen lifecycle input snapshots, but
  those snapshots are still provenance scaffolding and not formal validation or
  performance evidence.
- Phase 5B8 run manifests require persisted price and lifecycle-input snapshots
  for a PASS status; they validate rowset hashes only and do not produce returns
  or strategy conclusions.

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
.venv/bin/sectorscout price-snapshot --asof 2024-12-31 --config config.yaml
.venv/bin/sectorscout execution-decisions --asof 2024-11-29 --price-snapshot-id <price_snapshot_id> --config config.yaml
.venv/bin/sectorscout lifecycle-input-snapshot --execution-run-id <execution_run_id> --through 2024-12-31 --config config.yaml
.venv/bin/sectorscout position-lifecycle --execution-run-id <execution_run_id> --through 2024-12-31 --price-snapshot-id <price_snapshot_id> --lifecycle-input-snapshot-id <lifecycle_input_snapshot_id> --config config.yaml
.venv/bin/sectorscout trade-ledger-qa --lifecycle-run-id <lifecycle_run_id> --config config.yaml
.venv/bin/sectorscout run-manifest --lifecycle-run-id <lifecycle_run_id> --config config.yaml
.venv/bin/sectorscout provenance-validate --run-manifest-id <run_manifest_id> --config config.yaml
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
- Phase 5A through Phase 5B8 records are not a performance backtest, so no
  performance claims should be inferred from the current repository.
