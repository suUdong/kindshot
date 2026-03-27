# 2026-03-27 Backfill Auto Reporting

## Goal

`scripts/collect_backfill_auto.py` can run multiple backfill rounds and send a final Telegram/stdout summary, but it does not leave behind one machine-readable artifact for the automation batch itself. This slice makes the scheduler path persist a default JSON report so operators and follow-up automation can reopen what the nightly batch actually did.

## Current Gap

- `kindshot collect backfill --json` persists a report artifact.
- `scripts/collect_backfill_notify.py` also persists the latest backfill report artifact.
- `scripts/collect_backfill_auto.py` may run several rounds, stop because of `--stop-hour`, `--max-rounds`, or catch-up, and send a human-readable message only.
- That means cron-driven runs do work, but post-run evidence still depends on shell logs or chat notifications.

## Hypothesis

If the auto-backfill path writes a config-backed JSON report that records the request policy, per-round outcomes, stop reason, and final collector status, operators can audit nightly collection batches without scraping logs, and automation can consume one stable artifact for the batch outcome.

## Scope

- Add a default config path for the auto-backfill report.
- Add shared helper(s) in `src/kindshot/backfill_auto.py` to build and write the report.
- Update `scripts/collect_backfill_auto.py` to:
  - persist the latest single-round backfill report after each executed round
  - persist one final auto-batch report on success, noop, stop-hour stop, max-round stop, or error
- Keep collector write semantics unchanged.
- Keep Telegram formatting unchanged.

## Design

### Default Path

- Add `collector_backfill_auto_report_path` to `Config`.
- Default value: `data/collector/backfill/auto_latest.json`

### Auto Report Contract

The report should include:

- `source`
- `generated_at`
- `request`:
  - `max_days`
  - `max_rounds`
  - `stop_hour_kst`
  - `oldest_date`
  - `notify_noop`
- `result`:
  - `status`
  - `stop_reason`
  - `round_count`
  - `total_processed_dates`
  - `total_completed_dates`
  - `total_partial_dates`
  - `total_skipped_dates`
  - `latest_backfill_report_path`
- `rounds`:
  - one row per executed round
  - requested range
  - finalized date
  - processed/completed/partial/skipped date lists
  - per-date count maps copied from `BackfillResult`
- `collector_state`
- `collector_status`
- optional `error` block with exception type/message

### Stop Reasons

Use explicit stop reasons so batch outcomes are queryable:

- `backfill_floor_reached`
- `caught_up`
- `stop_hour_reached`
- `max_rounds_reached`
- `error`

### Reuse Of Existing Backfill Reports

- After each successful round, call the existing `write_collection_backfill_report()` helper so `data/collector/backfill/latest.json` still points at the latest executed round.
- On error, persist the error backfill report before writing the enclosing auto-batch report.

## Non-Goals

- Do not add a new CLI entrypoint.
- Do not change Telegram delivery behavior or formatting.
- Do not change auto-backfill planning policy.
- Do not add historical retention for past auto-batch reports in this slice.

## Validation

- unit test: round report builder copies range/date/count details from `BackfillResult`
- unit test: auto report builder includes request policy, stop reason, and latest backfill report path
- unit test: auto report writer uses the default config path
- targeted `tests/test_backfill_auto.py`
- full repository test suite

## Rollback

- Revert:
  - `docs/plans/2026-03-27-backfill-auto-reporting.md`
  - `src/kindshot/backfill_auto.py`
  - `src/kindshot/config.py`
  - `scripts/collect_backfill_auto.py`
  - related tests and memory summary updates
- No data migration is required because the change only adds a new read/report artifact.
