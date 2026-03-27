# 2026-03-27 Manifest-Aware Backfill Notifications

## Goal

`kindshot collect status` is already manifest-aware, but the operator-facing backfill notification path still summarizes only log-derived reasons. This slice makes the Telegram/stdout notification path reuse the same status-report contract so a failed or partial backfill run carries the current backlog health and manifest-backed blocker context directly in the notification.

## Current Gap

- `collect status` can already surface:
  - backlog `health`
  - stale-age metadata
  - manifest path/status/reason for blocked dates
- `format_backfill_notification()` currently reads only `CollectionLogSummary`.
- That means auto/manual backfill notifications can say a date is partial, but not which manifest replay will later trust or what the manifest-side blocker metadata says.

## Hypothesis

If backfill notifications reuse the collector status report instead of ad hoc summary-only formatting, operators can triage partial/error backlogs from the notification itself and the human-readable Telegram path will stay aligned with the machine-readable `collect status --json` contract.

## Scope

- Keep collector write semantics unchanged.
- Keep notification output additive:
  - existing success/failure header stays stable
  - existing range/count lines stay stable
  - add health/stale-age and manifest-backed blocker detail lines
- Reuse the collector status-report builder instead of duplicating manifest lookup logic in notification code.
- Update backfill notification tests and run-summary documents.

## Design

### Shared Status Report Helper

- Add a small public helper in `collector.py` that loads:
  - collector state
  - collection log summary
  - manifest-aware backlog details
- Make `log_collection_status()` and `print_collection_status_json()` call that helper so all status consumers share one read contract.

### Notification Enrichment

- Extend `format_backfill_notification()` to optionally accept the status report payload.
- Add concise lines for:
  - `health=<label>`
  - `oldest_blocked_age_s=<seconds>`
  - current-run partial detail rows with manifest reason/path when available
  - current error backlog rows with manifest status/path when available
- Keep skipped-date formatting lightweight because skipped dates are not backlog entries and may legitimately reuse older manifests.

### Call Sites

- `scripts/collect_backfill_notify.py`
- `scripts/collect_backfill_auto.py`

Both scripts should build the shared status report after the run and feed it into the formatter so manual and automated notification paths stay consistent.

## Non-Goals

- Do not change `collection_log.jsonl` schema.
- Do not change manifest write format.
- Do not change collector retry/cutoff policy.
- Do not add new notification channels.

## Validation

- unit test: shared status-report loader returns manifest-aware backlog details
- unit test: backfill notification includes health/stale-age lines from status report
- unit test: backfill notification includes manifest-backed partial detail lines
- unit test: backfill notification includes error backlog detail lines
- targeted collector + telegram tests
- full repository test suite

## Rollback

- Revert:
  - `src/kindshot/collector.py`
  - `src/kindshot/telegram_ops.py`
  - `scripts/collect_backfill_notify.py`
  - `scripts/collect_backfill_auto.py`
  - related tests and run-summary updates
- No data migration is required because the change only enriches read/notification paths.
