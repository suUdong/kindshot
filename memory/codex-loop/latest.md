Hypothesis: If backfill Telegram/stdout notifications reuse the same manifest-aware collector status report as `kindshot collect status`, operators can triage partial/error backlog state directly from the notification without opening raw manifest files, and the human-readable alert path will stay aligned with the replay-facing read contract.

Changed files:
- `docs/plans/2026-03-27-manifest-aware-backfill-notifications.md`
- `scripts/collect_backfill_auto.py`
- `scripts/collect_backfill_notify.py`
- `src/kindshot/collector.py`
- `src/kindshot/telegram_ops.py`
- `tests/test_collector.py`
- `tests/test_telegram_ops.py`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Validation:
- Added `load_collection_status_report()` so collector status readers share one manifest-aware report builder.
- Wired both backfill notification scripts to pass manifest-aware status reports into `format_backfill_notification()`.
- Extended notifications with backlog health, blocked-age, current-run partial detail lines, and error backlog detail lines backed by manifest context.
- Added regression coverage for the shared status-report helper and the enriched notification formatting.
- `python3 -m compileall src/kindshot tests scripts` passed
- `.venv/bin/python -m pytest tests/test_collector.py tests/test_telegram_ops.py -q` passed (`49 passed`)
- `.venv/bin/python -m pytest -q` passed (`761 passed, 1 warning`)

Risk and rollback note:
- This run changes collector read/notification paths only; collector write semantics, manifest schema, replay execution, deploy behavior, and live trading behavior remain unchanged.
- Remaining validation note is the existing `tests/test_health.py` `NotAppKeyWarning` warning during the full suite.
- Roll back by reverting the files listed above.
