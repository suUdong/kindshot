# 2026-03-28 Pattern Profitability Profiles

## Why This Slice

v69 observability is deployed, but the runtime still treats recent realized trade evidence as an operator-only artifact. Recent paper-trading analysis shows that some cohorts are repeatedly weak while a few cohorts are measurably stronger. This slice makes the runtime consume that evidence directly.

## Active Hypothesis

If Kindshot builds a conservative recent-trade profitability profile from existing logs and snapshots, then it can improve paper-trading risk-adjusted returns by giving small confidence boosts to winner cohorts and blocking repeated loser cohorts with a dedicated guardrail.

## Current Evidence

- Recent reconstructed window (`20260319`, `20260320`, `20260327`) contains `14` executed BUY trades with total realized return `-2.086%`.
- Positive cohorts:
  - hour `11`: `3` trades, `66.7%` win rate, `+0.286%` total return
  - `other_positive`: `2` trades, `100%` win rate, `+0.314%` total return
- Negative cohorts:
  - `contract + open`: `2` trades, `0%` win rate, `-1.365%` total return
  - ticker `068270`: `2` trades, `0%` win rate, `-0.614%` total return

These samples are still small, so runtime effects must stay bounded and conservative.

## Planned Implementation

1. Reuse `TradeDB.backfill_from_logs(...)` to reconstruct recent realized trades from existing artifacts.
2. Add a small profitability-profile module that ranks winner and loser cohorts across:
   - news type + hour bucket
   - news type + ticker
   - news type + ticker + hour bucket
3. Persist the selected profile to runtime state.
4. In `pipeline.py`, apply:
   - a small confidence boost for matched winner cohorts
   - a dedicated guardrail block for matched loser cohorts
5. Add tests covering generation, matching, and runtime behavior.

## Safety Bounds

- Conservative sample thresholds only.
- Small boost cap only.
- Loss-pattern blocks require repeated realized losers.
- No changes to live-order mode, `deploy/`, or secrets.

## Validation

- compile
- targeted tests for trade DB / guardrails / pipeline
- full test suite
- remote paper deployment verification

## Rollback

- Revert the profile module and runtime wiring.
- Remove persisted profitability profile state if it causes noisy behavior.
