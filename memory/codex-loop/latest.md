Hypothesis: If backfill notifications include the persisted report path(s), operators can jump directly from Telegram/stdout alerts to the durable JSON evidence (`latest.json`, `auto_latest.json`) without re-deriving the storage location from code or config.

Changed files:
- `docs/plans/2026-03-27-backfill-notification-artifact-paths.md`
- `scripts/collect_backfill_notify.py`
- `scripts/collect_backfill_auto.py`
- `src/kindshot/telegram_ops.py`
- `tests/test_telegram_ops.py`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Validation:
- Added optional `report_paths` support to `format_backfill_notification()`.
- Updated `scripts/collect_backfill_notify.py` to surface the persisted `backfill_report` path in notifications.
- Updated `scripts/collect_backfill_auto.py` to surface both:
  - `backfill_report` when a latest single-run report exists
  - `auto_report` for the enclosing auto-batch artifact
- Added regression coverage for notification lines that expose `backfill_report` and `auto_report`.
- `python3 -m compileall src/kindshot tests scripts` passed
- `.venv/bin/python -m pytest tests/test_telegram_ops.py -q` passed (`4 passed`)
- `.venv/bin/python -m pytest -q` passed (`781 passed, 1 warning`)

Risk and rollback note:
- This run changes notification text only; collector write semantics, report payload schemas, manifest schema, replay execution, deploy behavior, and live trading behavior remain unchanged.
- Remaining validation note is the existing `tests/test_health.py` `NotAppKeyWarning` warning during the full suite.
- Roll back by reverting the files listed above.
