# 2026-03-29 Liquidation Pipeline Inspection

## Goal

Close the remaining confidence gap in the Kindshot liquidation path by proving that pipeline-triggered forced exits reach the real runtime close-bookkeeping path end to end.

## Hypothesis

If `NEG_STRONG` and correction-triggered liquidations are exercised through the real `pipeline.py -> SnapshotScheduler.force_exit_ticker() -> _handle_trade_close()` path, then Kindshot can trust that operator-visible forced exits close positions and persist final trade outcomes even after partial take profit has already reduced the position.

## Current State

- `tests/test_price.py` proves `force_exit_ticker()` in isolation.
- `tests/test_pipeline.py` proves the pipeline asks the scheduler for a `news_exit`, but only with a mocked scheduler.
- `tests/test_exit_pipeline_e2e.py` proves scheduler close callbacks persist final trade outcomes, but not when liquidation is triggered from `pipeline.py`.
- No current test proves the full forced-liquidation path after a partial close or from the correction/withdrawal early-return branch.

## Design

1. Extend the existing exit E2E surface instead of adding new production code.
2. Add one end-to-end regression for `NEG_STRONG` liquidation after a partial take profit.
3. Add one end-to-end regression for correction-event liquidation from `process_registered_event()`.
4. Assert against persisted performance artifacts and guardrail state, not just mocked callback arguments.

## Validation

1. `pytest -q tests/test_exit_pipeline_e2e.py`
2. `python3 -m compileall src tests`
3. targeted `pytest -q tests/test_price.py tests/test_pipeline.py tests/test_exit_pipeline_e2e.py`

## Rollback

- Revert the added regression tests and planning docs.
- No runtime rollback is required because this slice adds proof only.
