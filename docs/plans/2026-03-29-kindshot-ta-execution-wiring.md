# 2026-03-29 Kindshot TA Execution Wiring

## Goal

Wire the existing `TechnicalStrategy` output into Kindshot's guardrail and order-tracking lane so an explicitly enabled TA strategy can exercise the same runtime execution path as executable news decisions.

## Hypothesis

If runtime keeps persisting `strategy_signal` rows but also executes those TA `TradeSignal` objects through a bounded shared consumer that rebuilds context, applies existing guardrails, and schedules paper/live follow-through, then Kindshot can evaluate a TA lane end-to-end without rewriting the legacy news pipeline.

## Evidence

- `TechnicalStrategy` already emits conservative BUY-only `TradeSignal` objects with confidence, size hint, and metadata.
- `strategy_runtime.py` currently logs those signals but never sends them into guardrails, paper tracking, or live order attempts.
- `pipeline.py` already contains the production guardrail and follow-through behavior; reusing its primitives is lower risk than inventing TA-only safety rules.
- The latest run summary explicitly records the missing link as the TA lane not reaching the guardrail/order path.

## Scope

- Keep `strategy_signal` persistence.
- Add a bounded strategy execution consumer for non-news signals.
- Rebuild fresh market/context inputs at execution time, then apply existing guardrails and scheduler/order side effects.
- Emit runtime `event` and `decision` rows for executable TA signals so downstream reporting can observe them.
- Keep the legacy news loop intact.

## Non-Goals

- Rewriting the full news pipeline around `TradeSignal`.
- Adding a new strategy bucket taxonomy.
- Changing deploy behavior, secrets, or default live execution policy.

## Design

### 1. Runtime consumer grows from logging-only to logging-plus-execution

- `consume_strategy_signals(...)` remains the runtime entrypoint.
- It always writes the `strategy_signal` record first.
- When execution dependencies are supplied by `main.py`, it also calls a bounded strategy-signal executor.

### 2. Strategy-signal executor

- Generate a deterministic synthetic `event_id` for each signal.
- Build a fresh `ContextCard` / `ContextCardData` for the signal ticker.
- Translate the signal into:
  - a synthetic positive `EventRecord`
  - a `DecisionRecord` sourced as `STRATEGY_SIGNAL`
- Apply:
  - market-halt guardrail
  - existing `check_guardrails(...)`
  - existing paper/live follow-through:
    - `guardrail_state.record_buy(...)`
    - `scheduler.schedule_t0(...)`
    - live `OrderExecutor.buy_market_with_retry(...)` when in live mode

### 3. Observability and rollout

- Preserve `strategy_signal` rows for raw strategy output.
- Add `event` and `decision` rows only for the executable TA lane.
- Keep behavior opt-in through the existing TA strategy enablement; if TA is disabled, nothing changes.
- Keep a synthetic headline / analysis tag so operators can recognize TA-originated decisions in runtime logs.

## Validation

1. Regression test the default logging-only consumer behavior.
2. Regression test TA execution path:
   - guardrail pass -> `event` + `decision` + scheduled tracking
   - live mode -> order attempt invoked
3. `python3 -m compileall src tests`
4. targeted pytest for TA/runtime/main wiring
5. affected-file diagnostics

## Rollback

- Revert the strategy-runtime execution helper, runtime wiring, and TA execution tests/docs.
- Because the TA lane remains explicitly opt-in, rollback is code-only.
