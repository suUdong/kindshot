Hypothesis: If backfill reporting always persists to a config-backed latest-report path, operators can reopen the most recent collector run artifact without remembering `--output PATH`, and the single-run notify workflow can leave behind the same machine-readable evidence as the CLI.

Changed files:
- `docs/plans/2026-03-27-default-backfill-report-path.md`
- `scripts/collect_backfill_notify.py`
- `src/kindshot/collector.py`
- `src/kindshot/config.py`
- `tests/test_collector.py`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Validation:
- Added `collector_backfill_report_path` with default `data/collector/backfill/latest.json`.
- Added `_backfill_report_output_path()` and `write_collection_backfill_report()` so backfill reports can be persisted without printing.
- Updated `print_collection_backfill_json()` to persist the latest report even when no explicit output path is supplied.
- Updated `scripts/collect_backfill_notify.py` to persist the same latest report artifact after success or failure.
- Added regression coverage for:
  - default/override backfill report path resolution
  - default latest-report file writing
  - `print_collection_backfill_json()` default persistence behavior
- `python3 -m compileall src/kindshot tests scripts` passed
- `.venv/bin/python -m pytest tests/test_collector.py -q` passed (`52 passed`)
- `.venv/bin/python -m pytest -q` passed (`768 passed, 1 warning`)

Risk and rollback note:
- This run changes collector read/report persistence paths only; collector write semantics, manifest schema, replay execution, deploy behavior, and live trading behavior remain unchanged.
- Remaining validation note is the existing `tests/test_health.py` `NotAppKeyWarning` warning during the full suite.
- Roll back by reverting the files listed above.
