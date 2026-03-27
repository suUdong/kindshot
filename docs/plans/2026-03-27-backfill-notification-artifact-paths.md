# 2026-03-27 Backfill Notification Artifact Paths

## Goal

Backfill notifications already summarize collector outcome and backlog health, but they still force operators to remember where the persisted JSON artifacts were written. This slice makes the notification itself point at the latest report files so operators can reopen the machine-readable evidence directly from the Telegram/stdout summary.

## Current Gap

- `kindshot collect backfill --json` persists `data/collector/backfill/latest.json`.
- `scripts/collect_backfill_notify.py` persists that same latest report.
- `scripts/collect_backfill_auto.py` now persists both:
  - latest single-run backfill report
  - latest auto-batch report
- `format_backfill_notification()` does not mention any of those artifact paths.
- That means operators still have to infer the path or inspect code/config after receiving a notification.

## Hypothesis

If backfill notifications include the persisted report path(s), operators can jump directly from the alert to the durable JSON evidence, and stdout logs keep a self-contained pointer to the same artifacts that automation will later consume.

## Scope

- Extend `format_backfill_notification()` to optionally accept additive report-path metadata.
- Update:
  - `scripts/collect_backfill_notify.py`
  - `scripts/collect_backfill_auto.py`
- Keep collector behavior and report payload schemas unchanged.
- Keep notification formatting concise and additive.

## Design

### Formatter Contract

Add optional `report_paths` input to `format_backfill_notification()`.

Supported keys in this slice:

- `backfill_report`
- `auto_report`

Formatting rules:

- Emit only non-empty paths.
- Keep one line per path:
  - `backfill_report=...`
  - `auto_report=...`
- Preserve existing notification lines and ordering.

### Call Sites

`scripts/collect_backfill_notify.py`

- capture the returned path from `write_collection_backfill_report()`
- include it in the notification as `backfill_report`

`scripts/collect_backfill_auto.py`

- include `backfill_report` when a round or error-path latest report exists
- include `auto_report` after the final auto-batch report is written
- noop and stop-only runs may legitimately emit only `auto_report`

## Non-Goals

- Do not change Telegram transport behavior.
- Do not change report file locations in this slice.
- Do not alter collector status or backlog formatting semantics.
- Do not add clickable URL generation or path shortening.

## Validation

- unit test: notification includes `backfill_report` when provided
- unit test: notification includes both `backfill_report` and `auto_report` when provided
- targeted `tests/test_telegram_ops.py`
- full repository test suite

## Rollback

- Revert:
  - `docs/plans/2026-03-27-backfill-notification-artifact-paths.md`
  - `src/kindshot/telegram_ops.py`
  - `scripts/collect_backfill_notify.py`
  - `scripts/collect_backfill_auto.py`
  - related tests and memory summary updates
- No data migration is required because the change only adds notification text.
