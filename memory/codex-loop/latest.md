Hypothesis: If late-day `close` scheduling edge cases and the `3bb1116` liquidation-path boundaries are covered by explicit regression tests, future edits to `SnapshotScheduler` will be less likely to silently break after-cutoff close handling, t+5m checkpoint exits, or trailing-stop thresholds.

Changed files:
- `tests/test_price.py`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Validation:
- Added regression coverage for:
  - near-market-close close snapshot scheduling
  - after-cutoff zero-delay `close` snapshot scheduling
  - t+5m gap-down path where stop-loss should win before the loss-checkpoint branch
  - profitable t+5m gap-up checkpoint state
  - exact-equality trailing-stop boundaries for normal and t+5m-tight trailing
  - repeated fills tracked independently by `event_id`
- `.venv/bin/python -m pytest tests/test_price.py -q` passed (`31 passed`)
- `python3 -m compileall src/kindshot tests` passed
- LSP diagnostics on `tests/test_price.py` returned `0` errors
- `.venv/bin/python -m pytest -q` passed (`752 passed, 1 warning`)

Risk and rollback note:
- This run changes tests and run summaries only; production strategy, runtime execution, and deploy behavior are unchanged.
- Remaining validation note is the existing `tests/test_health.py` `NotAppKeyWarning` warning during the full suite.
- Roll back by reverting `tests/test_price.py`, `memory/codex-loop/latest.md`, and `memory/codex-loop/session.md`.
