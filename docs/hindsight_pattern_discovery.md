# Hindsight Pattern Discovery

This lab is for turning historical leaders into testable pattern hypotheses. It is not a daily action engine and it does not modify SectorScout scores.

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

## Safety Rules

- Mark missing price history explicitly.
- Keep case-study diagnostics row-level.
- Do not convert hindsight findings into live instructions.
- Keep point-in-time data, universe membership, provider mix, and config hash visible.
- Treat external intel as context only.

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
