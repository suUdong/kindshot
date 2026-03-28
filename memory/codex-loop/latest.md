Hypothesis: If blocked BUY rows are only backfilled when reconstructable shadow/skip snapshots exist, and date reprocessing deletes stale rows first, then `trade_history.db` will stop accumulating `exit_ret_pct = NULL` artifacts and version/analysis queries will reflect only real exit-capable rows.

Changed files:
- `src/kindshot/trade_db.py`
- `tests/test_trade_db.py`
- `memory/codex-loop/latest.md`
- `data/trade_history.db` (local force re-backfill result; not tracked)
- `.omx/context/exit-ret-pct-null-20260328T201711Z.md`
- `.omx/plans/prd-exit-ret-pct-null-trace-20260329.md`
- `.omx/plans/test-spec-exit-ret-pct-null-trace-20260329.md`

Implementation summary:
- Traced the runtime close path as `SnapshotScheduler._emit_trade_close()` -> `main._on_trade_close()` -> `PerformanceTracker.record_trade()`, then confirmed `trade_history.db` is populated separately by `backfill_from_logs()`.
- Confirmed the schema already supports `exit_ret_pct REAL`; the break was not the DB schema.
- Verified the null rows were coming from blocked BUY events inserted by `backfill_from_logs()` without reconstructable shadow data.
- Updated `backfill_from_logs()` to:
  - rebuild a processed date from scratch by deleting prior rows first
  - reuse a shared exit-metric path for both traded and blocked rows
  - resolve blocked-event snapshots from `shadow_{event_id}`, `skip_{event_id}`, then raw `event_id`
  - skip blocked rows entirely when no reconstructable snapshot data exists, instead of inserting `exit_ret_pct = NULL`
- Added regression tests for shadow snapshot backfill, `skip_` fallback, and stale-null cleanup during force re-backfill.
- Force re-backfilled the local `data/trade_history.db` using `logs/` plus `data/runtime/price_snapshots/`.

Root cause:
- The close pipeline itself was intact.
- The break was in the DB backfill boundary: blocked BUY rows were written into `trades` even when the opportunity-cost snapshot stream needed to reconstruct an exit was absent.
- Because those rows had no usable snapshot returns, `exit_ret_pct` stayed null by construction.

Validation:
- `pytest -q tests/test_trade_db.py` -> `21 passed`
- `python3 -m compileall src tests` -> success
- `pytest -q` -> `1080 passed, 1 skipped, 1 warning`
- diagnostics on changed files:
  - `src/kindshot/trade_db.py` -> 0 errors
  - `tests/test_trade_db.py` -> 0 errors
- local DB after force re-backfill:
  - total rows = `14`
  - `exit_ret_pct IS NULL` rows = `0`
  - by date: `20260327=5`, `20260320=7`, `20260319=2`

Simplifications made:
- Reused the existing strategy exit classifier for blocked-row reconstruction instead of adding a second exit simulation path.
- Kept blocked opportunity tracking in the same table, but only for rows that can actually be reconstructed.
- Used date-level replacement on re-backfill rather than adding schema or migration complexity.

Remaining risks:
- Historical blocked BUY rows without any preserved shadow/skip snapshots are now omitted, so opportunity-cost coverage depends on runtime snapshot preservation.
- Existing older logs appear not to contain reconstructable shadow streams for many blocked BUY events, so historical blocked coverage remains sparse even though null artifacts are removed.

Rollback note:
- Revert the commit and restore `data/trade_history.db` from `data/trade_history.db.bak` if you want the previous local analysis DB contents back.
