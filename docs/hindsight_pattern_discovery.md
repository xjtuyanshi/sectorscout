# Historical Pattern Discovery

This lab is for turning historical leaders into testable pattern hypotheses. It is not a daily action engine and it does not modify SectorScout scores.

The goal is not to prove rules by looking backward. The goal is to study prior leaders with explicit missing-data flags, then separate industry conditions from technical conditions before any future validation work.

## Case Seed

Initial examples:

- `NVDA`, 2024, AI infrastructure leader
- `MU`, 2025-2026, AI memory / HBM cycle
- `SNDK`, 2025-2026, storage / NAND cycle
- `LITE`, 2025-2026, optical AI infrastructure

## Pattern Axes

Industry / theme:

- Large demand shock or capex cycle
- Improving sector breadth
- Multiple related symbols strengthening together
- Fundamental acceleration or revision cycle
- External intel theme appears before internal ranking

Technical:

- Strong relative strength near the start of the case window
- Stage 2 trend proxy
- Breakout / new-high proxy
- Volume expansion proxy
- Setup quality improving after theme confirmation

## Methodology Anchors

This module follows a research-log design, not a validation design:

- Event-study discipline: define the case window and event context separately
  from what happened afterward. See MacKinlay's event-study framework:
  https://www.bu.edu/econ/files/2011/01/MacKinlay-1996-Event-Studies-in-Economics-and-Finance.pdf
- Industry taxonomy discipline: keep a stable classification layer and do not
  rewrite history with only today's narrative. GICS is the reference framework:
  https://www.spglobal.com/spdji/en/landing/topic/gics/
- Fundamental data discipline: use filing-time-aware facts where possible. SEC
  EDGAR company facts and submissions APIs are the preferred public baseline:
  https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- Relative-strength discipline: momentum/relative strength is a hypothesis
  feature with known academic precedent, but SectorScout still treats it as a
  candidate observation requiring later validation.
- Model-risk discipline: every rule needs purpose, assumptions, data quality,
  limitations, monitoring, and review before it can graduate from research
  hypothesis to production logic. SR 11-7 is the governance reference:
  https://www.federalreserve.gov/supervisionreg/srletters/sr1107.htm

## Pattern Observation Model

The lab writes `hindsight_pattern_observations` alongside scan results. These
rows are intentionally split into three groups:

- `industry`: theme taxonomy and industry-cluster hypotheses.
- `technical`: daily OHLCV-derived observations such as RS near case start,
  trend proxy, new-high proxy, and volume expansion proxy.
- `manual_or_llm_required`: catalyst narrative and point-in-time theme
  discoverability checks that should not be inferred from prices alone.

Observation statuses are not validation outcomes. Current statuses include:

- `hypothesis_seed`
- `observed_hypothesis_feature`
- `not_observed_in_case_window`
- `needs_historical_data`
- `needs_manual_review`
- `outcome_only_not_predictive`

## Observation Review Workflow

Pattern observations are routed into the Research Checklist as
`hindsight_pattern_review` items when `requires_review = true`.

The user review status is stored in `intel_review_marks` with
`object_type = hindsight_pattern_observation`. Current user decisions:

- `confirmed_hypothesis`: useful enough to keep as a future research hypothesis.
- `rejected_hypothesis`: not supported or too hindsight-dependent.
- `needs_more_data`: keep open until more source/data evidence is collected.
- `unclear`: cannot classify yet.
- `not_applicable`: not useful for this research system.

Confirmed, rejected, and not-applicable observations leave the open review queue.
Future follow-up dates defer the queue item until due.

## Event Ledger And Gate Explain

The next audit layer is the event ledger. Each historical case gets an event row
before technical replay is interpreted:

- `event_date`
- `published_at_utc`
- `market_session`
- `first_tradable_date`
- `source_url`
- `source_quality`
- `evidence_type`
- `fundamental_evidence_available_at`
- `technical_replay_as_of`

The default NVDA, MU, SNDK, and LITE cases include official timing seeds from
SEC 8-K or spin-off filings so the lab can separate the event timestamp from
the first regular tradable session. Custom cases stay conservative: if only a
date-level catalyst is known, the event is marked `date_only_ambiguous`,
`first_tradable_date` stays unresolved, and event-reaction gates are `DATA_GAP`.
This prevents treating unavailable overnight or intraday information as if it
were known at the decision point.

Replay gates use explicit status labels:

- `PASS`: data exists and the rule condition is met.
- `FAIL`: data exists and the rule condition is not met.
- `DATA_GAP`: required timestamp, source, price, or benchmark data is missing.
- `PENDING`: the future review window is not complete.
- `N/A`: the gate does not apply to this event type.

Every gate stores formula, threshold, computed value, data used, required rows,
available rows, missing detail, source, and reason. Benchmark relative strength
uses a fixed primary benchmark and does not silently fall back to another index.

The UI adds a reviewer layer above the raw gate table:

- `Case Readiness`: one row per historical leader showing whether event timing,
  pre-event setup evidence, and first-tradable data are readable or blocked.
- `Status Guide`: plain-language definitions for `PASS`, `FAIL`, `DATA_GAP`,
  and `PENDING`.
- `Gate Review Guide`: each gate is phrased as a reviewer question, with next
  action and blocked conclusion. A `DATA_GAP` row is never counted as either
  support or rejection.

Every visible gate should let the reviewer answer five questions without leaving
the page: what is the claim, what evidence supports it, when was it knowable,
what gate status was assigned, and why that status was assigned.

## Current Commands

```bash
.venv/bin/sectorscout hindsight write-default-cases
.venv/bin/sectorscout hindsight seed
.venv/bin/sectorscout hindsight seed-events
.venv/bin/sectorscout hindsight fetch-prices
.venv/bin/sectorscout hindsight scan
.venv/bin/sectorscout hindsight build-gates
```

`fetch-prices` defaults to the public Yahoo chart JSON endpoint for case-study diagnostics and fetches a 320-calendar-day pre-event lookback by default. That lookback is required for pre-event technical replay gates such as trend, relative strength, and price coverage. Stooq remains available with `--provider stooq_public` when `STOOQ_API_KEY` is configured. Corporate-action adjustment status is marked with a warning in stored price rows, so split-sensitive cases still need provider-quality review before any formal validation.

After `scan`, review:

- `hindsight_scan_results` for case-level coverage and path diagnostics;
- `hindsight_pattern_observations` for the actual industry, technical, and
  manual-review hypothesis observations;
- `hindsight_event_ledger` for event timing and source availability;
- `hindsight_replay_gates` for PASS / FAIL / DATA_GAP / PENDING explain rows.

## Safety Rules

- Mark missing price history explicitly.
- Keep case-study diagnostics row-level.
- Do not convert hindsight findings into live instructions.
- Keep point-in-time data, universe membership, provider mix, and config hash visible.
- Treat external intel as context only.
- Treat confirmed observations as hypotheses, not validated rules.

## ChatGPT Pro Review Prompt

Review this SectorScout hindsight pattern-discovery design. The goal is to learn repeatable industry and technical patterns from historical leaders such as NVDA 2024 and MU/SNDK/LITE 2025-2026, without creating a hindsight-only illusion.

Evaluate:

- Whether the case seed set is too narrow or biased.
- Which industry-pattern fields should be added.
- Which technical-pattern fields are robust enough to automate.
- Where point-in-time safety could fail.
- What evidence should be required before a historical pattern is promoted into a live research rule.

Constraints:

- SectorScout is a research and QA system, not an auto-trading system.
- External intel is overlay-only.
- Hindsight diagnostics must not change base scores.
- Missing data must be shown as missing, not inferred.
