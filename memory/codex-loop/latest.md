Hypothesis: If Kindshot adds one unified monthly backtest/report path that reuses local replay evidence, versioned exit simulation, and current deterministic guardrail/risk logic, then operators can compare `v64`~`v70`, estimate current-strategy performance, and confirm the best-supported parameter set without depending on fresh paid LLM replay.

Changed files:
- `src/kindshot/trade_db.py`
- `tests/test_trade_db.py`
- `scripts/monthly_full_strategy_backtest.py`
- `tests/test_monthly_full_strategy_backtest.py`
- `docs/plans/2026-03-28-monthly-full-strategy-backtest.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`
- `memory/codex-loop/roadmap.md`

Implementation summary:
- Fixed `trade_db.backfill_from_logs()` so embedded `price_snapshot` rows from `logs/kindshot_*.jsonl` are used even when standalone runtime snapshot files are missing.
- Added `scripts/monthly_full_strategy_backtest.py`, a unified local report command that:
  - selects the latest available 30-day local log window
  - reconstructs current exit behavior from historical executed BUY trades
  - simulates current deterministic preflight, current guardrails, and risk-v2 portfolio progression
  - generates `v64`~`v70` comparison metrics on the same trade set
  - surfaces the best-supported entry / exit / risk parameter summary
  - records the LLM replay blocker explicitly instead of pretending the opaque model was replayed
- Added regression tests for embedded snapshot backfill and the new monthly report script.
- Generated analysis artifacts:
  - `logs/daily_analysis/monthly_full_strategy_backtest_20260328.json`
  - `logs/daily_analysis/monthly_full_strategy_backtest_20260328.txt`

Backtest result summary:
- Requested “1 month” window, available local evidence window: `2026-03-10` → `2026-03-27` (`14` log files)
- Current-strategy estimate on reconstructable historical BUY candidates:
  - candidates: `14`
  - accepted after current deterministic guards/risk: `5`
  - blocked: `9`
  - accepted win rate: `40.0%`
  - total return: `-1.4325%`
  - approximate PnL: `-106,980 KRW`
- Current-strategy blocked reasons:
  - `ADV_TOO_LOW`: `3`
  - `LOW_CONFIDENCE`: `2`
  - `OPENING_LOW_CONFIDENCE`: `1`
  - `PRE_OPENING_LOW_CONFIDENCE`: `1`
  - `INTRADAY_VALUE_TOO_THIN`: `1`
  - `PRIOR_VOLUME_TOO_THIN`: `1`
- Version comparison on the same trade set:
  - `v64`: total `-5.8541%`, PF `0.10`, MDD `-6.4824%`
  - `v65`~`v70`: total `-5.0093%`, PF `0.11`, MDD `-5.6376%`
- Best-supported parameter set from current local evidence:
  - entry: `max_entry_delay_ms=60000`, `min_intraday_value_vs_adv20d=0.15`, `orderbook_bid_ask_ratio_min=0.8`
  - exit: current shipped set remained the top-ranked candidate in the local slice (`TP 2.0`, `SL -1.5`, trailing activation `0.5`, trailing `0.5/0.8/1.0`, `max_hold 20`, `t5m_loss_exit=True`)
  - risk v2: `max_positions=4`, `consecutive_loss_halt=3`, recent trade window `4`

Validation:
- local `python3 -m compileall src scripts tests`
- local `.venv/bin/python -m pytest tests/test_trade_db.py tests/test_monthly_full_strategy_backtest.py -q` → `20 passed`
- local `.venv/bin/python scripts/monthly_full_strategy_backtest.py`
- local `.venv/bin/python -m pytest -q` → `997 passed, 1 skipped, 1 warning`
- diagnostics `lsp_diagnostics_directory` → `0 errors`, `0 warnings`

Simplifications made:
- Reused `backtest_analysis.py`, `version_report.py`, and existing guardrail/risk primitives instead of adding a second offline simulation stack.
- Reused historical logged BUY decisions as the opaque-model proxy when fresh LLM replay was unavailable, while still reapplying current deterministic preflight and guardrails.
- Kept the new slice local-analysis only; no deployment/runtime behavior was changed.

Remaining risks:
- The report does not replay the opaque current LLM prompt path because Anthropic replay returned `400 invalid_request_error` due to insufficient credits and NVIDIA is unconfigured locally.
- The available local evidence window is not a full calendar month; it is bounded by checked-in logs from `2026-03-10` through `2026-03-27`.
- Sector concentration inside risk v2 can only be simulated when sector metadata exists in local context artifacts; the current local historical context is sparse there.

Rollback note:
- Revert the monthly backtest/reporting commit to remove the new script, tests, backfill fix, and run-summary docs. No deploy rollback is required because this slice changes only local analysis/reporting surfaces.
