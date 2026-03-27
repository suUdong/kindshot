# 2026-03-27 Default Backfill Report Path

## Goal

`kindshot collect backfill --json` can now print a machine-readable run report, but operators still have to remember `--output PATH` if they want that artifact to survive after stdout disappears. This slice gives backfill reporting a default latest-report path so the most recent run artifact is durable by default.

## Current Gap

- Backfill report generation exists.
- The report is persisted only when an explicit output path is provided.
- The single-run notify script still sends a human-readable message without persisting the JSON run artifact.

## Hypothesis

If backfill reporting uses a config-backed default latest-report path, operators can always reopen the most recent run artifact after a CLI or notify-script run, and automation can depend on one stable location without wrapping the command in extra shell redirection.

## Scope

- Add one config-backed default report path for collector backfill runs.
- Keep `--output PATH` as an explicit override.
- Keep stdout behavior unchanged.
- Persist the same report shape produced in the previous slice; do not redesign the payload.
- Extend the single-run notify script to persist the same latest report artifact.

## Design

### Default Path

- Add `collector_backfill_report_path` to `Config`.
- Default value:
  - `data/collector/backfill/latest.json`

### Collector Helpers

- Add a small output-path helper that mirrors replay/unknown-review report-path conventions.
- Split "build report" from "write report" so callers can:
  - print + persist
  - persist only

### Behavior

- `kindshot collect backfill --json`
  - prints the report to stdout
  - also writes the report to `collector_backfill_report_path`
- `kindshot collect backfill --json --output PATH`
  - prints to stdout
  - writes to `PATH`
- `scripts/collect_backfill_notify.py`
  - persists the same latest report artifact after success or failure

## Non-Goals

- Do not change collector log schema.
- Do not add a multi-round automation report contract in this slice.
- Do not change Telegram message formatting.
- Do not change collector write semantics.

## Validation

- unit test: default backfill report path helper resolves config path
- unit test: `print_collection_backfill_json()` writes to the default latest path when no explicit output is provided
- unit test: explicit `--output PATH` still overrides the default path
- unit test: notify script or equivalent helper path persists the report artifact
- targeted collector tests
- full repository test suite

## Rollback

- Revert:
  - `src/kindshot/config.py`
  - `src/kindshot/collector.py`
  - `scripts/collect_backfill_notify.py`
  - related tests and run-summary updates
- No data migration is required because the change only adds a default read/report location.
