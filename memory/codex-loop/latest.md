Hypothesis: If the scheduler-driven auto-backfill path persists a config-backed batch report and also refreshes the latest single-round backfill report after each executed round, operators and automation can reopen nightly collection outcomes without scraping logs or Telegram output.

Changed files:
- `docs/plans/2026-03-27-backfill-auto-reporting.md`
- `scripts/collect_backfill_auto.py`
- `src/kindshot/backfill_auto.py`
- `src/kindshot/config.py`
- `tests/test_backfill_auto.py`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Validation:
- Added `collector_backfill_auto_report_path` with default `data/collector/backfill/auto_latest.json`.
- Added auto-backfill report helpers that build per-round rows plus a final batch report with request policy, stop reason, round totals, collector state, and collector status.
- Updated `scripts/collect_backfill_auto.py` to:
  - refresh `data/collector/backfill/latest.json` after each executed round
  - persist `data/collector/backfill/auto_latest.json` on success, noop, stop-hour/max-round stop, and error
- Added regression coverage for:
  - round report row generation from `BackfillResult`
  - auto-batch report request/result contract
  - default auto report path writing
- `python3 -m compileall src/kindshot tests scripts` passed
- `.venv/bin/python -m pytest tests/test_backfill_auto.py -q` passed (`9 passed`)
- `.venv/bin/python -m pytest -q` passed (`771 passed, 1 warning`)

Risk and rollback note:
- This run changes backfill automation read/report persistence only; collector write semantics, manifest schema, replay execution, deploy behavior, and live trading behavior remain unchanged.
- Remaining validation note is the existing `tests/test_health.py` `NotAppKeyWarning` warning during the full suite.
- Roll back by reverting the files listed above.
