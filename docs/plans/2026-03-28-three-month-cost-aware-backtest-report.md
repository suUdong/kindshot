# 2026-03-28 Three-Month Cost-Aware Backtest Report

## Goal

Deliver one reproducible local backtest/report path that answers three operator questions in one run:

1. what the current strategy would have produced on the latest requested three-month real-trading evidence window
2. whether slippage and fee reflection in the backtest matches the repository's runtime assumptions and explicit cost policy
3. what the resulting net performance report looks like in operator-facing text and JSON

## Hypothesis

If the existing monthly backtest path is extended into a cost-aware evidence-window report, then Kindshot operators will make tuning decisions from net returns and explicit coverage evidence instead of gross-only, one-month summaries.

## Current State

- The current unified report path is `scripts/monthly_full_strategy_backtest.py`.
- It currently selects logs by `lookback_days`, not by a requested calendar-month window.
- It reports gross returns / gross KRW PnL only.
- Runtime paper tracking applies a conservative half-spread entry penalty in [price.py](/home/wdsr88/workspace/kindshot/src/kindshot/price.py), but the backtest/report path does not explicitly validate or summarize that assumption.
- No explicit buy fee, sell fee, or sell tax model is present in the current backtest/report output.
- In-repo reconstructable runtime logs currently span `2026-03-10` through `2026-03-27`, so a requested three-month window will have partial local coverage unless more data is later added.

## Constraints

- Paper-only; no live execution.
- No changes under `deploy/`, `.env`, or secrets/credentials.
- Keep production/runtime trading behavior unchanged.
- Keep the diff small and reversible.
- Use only local repository evidence for the executed report.

## Design

### 1. Evidence-window selection

- Extend the unified backtest script to support an explicit calendar window request:
  - `--lookback-months 3`
  - derive `requested_window_start` from the latest available log date, not from wall-clock now
- Report both:
  - requested window
  - actual covered window from local evidence
- Include coverage metrics:
  - requested calendar days
  - covered log days
  - covered trade days
  - coverage ratio

### 2. Cost model

- Add a backtest-only transaction-cost model with explicit components:
  - entry slippage bps
  - exit slippage bps
  - buy fee bps
  - sell fee bps
  - sell tax bps
- Validation policy:
  - entry slippage must match runtime `_apply_entry_slippage()` semantics when `spread_bps` is present: half-spread on BUY entry
  - exit slippage uses half-spread when the exit snapshot has `spread_bps`; otherwise it remains `0` and is reported as uncovered rather than guessed
  - fees/tax use explicit fixed defaults derived from the repo research note for paper-trading analysis:
    - buy fee `1.5 bps`
    - sell fee `1.5 bps`
    - sell tax `20 bps`
- Every report must separate:
  - gross return / gross PnL
  - cost drag by component
  - net return / net PnL
  - coverage of validated slippage inputs

### 3. Report shape

- Extend the unified report JSON/text output to include:
  - requested vs actual evidence window
  - cost-model configuration
  - cost-validation summary
  - current-strategy gross summary
  - current-strategy net summary
  - version-comparison gross/net summaries
  - explicit limitations when the requested three-month window is only partially covered

### 4. Backfill / comparison alignment

- Extend `trade_db` backfill to persist snapshot spreads needed for exit-slippage validation where available.
- Keep version-comparison logic on the same cost model so "current strategy" and "version comparison" are directly comparable on a net basis.

## Logging / Artifacts

- Continue writing operator artifacts under `logs/daily_analysis/`.
- Emit:
  - `monthly_full_strategy_backtest_<date>.json`
  - `monthly_full_strategy_backtest_<date>.txt`
- The JSON must be rich enough for follow-on notebooks/scripts; the text output must highlight:
  - requested window
  - covered window
  - gross vs net performance
  - cost-validation coverage
  - limitations

## Validation

1. targeted pytest for the new window/cost/report behavior
2. `python3 -m compileall src scripts tests`
3. execute the unified backtest script with `--lookback-months 3`
4. inspect the generated JSON/text artifacts
5. full `pytest -q`
6. diagnostics on changed Python files
7. update `memory/codex-loop/latest.md`

## Rollback

- Revert the backtest/reporting commit.
- Delete the generated analysis artifacts if desired.
- No deployment rollback is needed because runtime trading behavior is unchanged.
