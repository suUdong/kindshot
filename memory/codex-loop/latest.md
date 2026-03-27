Hypothesis: If `kindshot collect backfill` emits a machine-readable report that bundles the requested range, touched-date details, and post-run manifest-aware collector status, operators and automation can reuse one artifact per run instead of scraping stdout and raw logs.

Changed files:
- `docs/plans/2026-03-27-collect-backfill-reporting.md`
- `src/kindshot/collector.py`
- `tests/test_collector.py`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Validation:
- Added `build_collection_backfill_report()` and `print_collection_backfill_json()` to expose a machine-readable backfill run artifact.
- Extended `kindshot collect backfill` argument parsing with `--json` and `--output PATH`, including JSON artifact emission on success and failure paths.
- Added regression coverage for:
  - backfill CLI argument parsing
  - backfill report payload shape
  - stdout/file JSON output
  - `collect_main()` JSON dispatch for backfill
- `python3 -m compileall src/kindshot tests` passed
- `.venv/bin/python -m pytest tests/test_collector.py -q` passed (`49 passed`)
- `.venv/bin/python -m pytest -q` passed (`765 passed, 1 warning`)

Risk and rollback note:
- This run changes collector read/report surfaces only; collector write semantics, manifest schema, replay execution, deploy behavior, and live trading behavior remain unchanged.
- Remaining validation note is the existing `tests/test_health.py` `NotAppKeyWarning` warning during the full suite.
- Roll back by reverting the files listed above.
