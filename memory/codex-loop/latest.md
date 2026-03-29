Hypothesis: If Kindshot proves the real `pipeline.py -> SnapshotScheduler.force_exit_ticker() -> _handle_trade_close()` path for both negative-news and correction-triggered liquidations, then the liquidation pipeline can be trusted to close positions and persist final trade outcomes end to end, including after a partial take profit.

Changed files:
- `src/kindshot/strategy_observability.py`
- `tests/test_exit_pipeline_e2e.py`
- `tests/test_pipeline.py`
- `tests/test_replay.py`
- `tests/test_strategy_comparison.py`
- `tests/test_strategy_observability.py`
- `docs/plans/2026-03-29-liquidation-pipeline-inspection.md`
- `docs/plans/2026-03-29-deploy-readiness-exit-e2e.md`
- `.omx/plans/prd-liquidation-pipeline-inspection-20260329.md`
- `.omx/plans/test-spec-liquidation-pipeline-inspection-20260329.md`
- `.omx/context/liquidation-pipeline-inspection-20260329T002141Z.md`
- `memory/codex-loop/latest.md`

Implementation summary:
- Added an end-to-end regression proving `execute_bucket_path()` can trigger a `NEG_STRONG` forced liquidation after a partial take profit and still persist exactly one final cumulative trade row through `_handle_trade_close()`, with the later `close` snapshot proving no duplicate final record is emitted.
- Added an end-to-end regression proving `process_registered_event()` correction handling triggers `correction_exit` liquidation and persists the final realized loss through the real runtime bookkeeping path.
- Updated the deploy-readiness plan text to record that pipeline-originated liquidation requests are now part of the proof surface.
- Synced `StrategyReportConfig` with live runtime defaults and refreshed stale replay/strategy test expectations so observability/report reconstruction follows the current 30-minute hold and trailing/t5m defaults.

Validation:
- `pytest -q tests/test_exit_pipeline_e2e.py` -> `4 passed`
- `pytest -q tests/test_replay.py::test_replay_passes_normalized_guardrail_context tests/test_strategy_comparison.py tests/test_strategy_observability.py` -> `5 passed`
- `pytest -q tests/test_price.py tests/test_pipeline.py tests/test_exit_pipeline_e2e.py tests/test_sell_triggered_fix.py` -> `86 passed, 1 skipped`
- `python3 -m compileall src tests` -> success
- `pytest -q` -> `1153 passed, 1 skipped, 1 warning`
- diagnostics:
  - `src/kindshot/strategy_observability.py` -> `0 errors`
  - `tests/test_exit_pipeline_e2e.py` -> `0 errors`
  - `tests/test_pipeline.py` -> `0 errors`
  - `tests/test_strategy_comparison.py` -> `0 errors`
  - `tests/test_strategy_observability.py` -> `0 errors`

Simplifications made:
- Reused the existing `_handle_trade_close()` runtime helper and `SnapshotScheduler` instead of introducing new liquidation-specific harness code.
- Extended the existing exit E2E test file rather than creating separate bespoke pipeline test infrastructure.
- Removed hard-coded strategy observability defaults in favor of `Config()`-derived values to prevent future drift.

Remaining risks:
- The new proof covers paper-mode liquidation bookkeeping; it does not add new live-order evidence.
- Strategy report reconstruction now follows current runtime defaults, so historical reports generated under older parameter regimes may need an explicit pinned config if exact backdated reproduction is required.

Rollback note:
- Revert the added E2E tests, observability default sync, and planning artifacts; no runtime or deploy rollback is required because this slice adds proof/report alignment only.
