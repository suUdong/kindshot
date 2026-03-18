# Telegram Backfill Ops Notification

## Objective

- Add an operator-facing Telegram path for `collect backfill` runs.
- Keep secret handling unchanged: tokens remain environment-only and are never written to repo files.
- Make the first usable slice a single server-executable path that runs backfill, summarizes the result, and sends one Telegram message.

## Current State

- `deploy/daily_report.py --telegram` already sends end-of-day reports with:
  - `TELEGRAM_BOT_TOKEN`
  - `TELEGRAM_CHAT_ID`
- There is no equivalent path for collector/backfill runs.
- `collect backfill` is currently operator-driven and logs to:
  - `data/collector_state.json`
  - `data/collector/collection_log.jsonl`
  - `data/collector/manifests/`

## Scope

- Add a reusable Telegram helper in application code.
- Add a formatter for collector/backfill run summaries.
- Add one standalone script that:
  - runs `collect backfill`
  - loads collector state/log summary
  - sends one Telegram message on success or failure

## Non-Goals

- Do not modify `.env`, secrets, or credential persistence.
- Do not edit files under `deploy/`.
- Do not add background scheduling/cron changes in this slice.
- Do not add generalized notification support for every CLI path.

## Proposed Path

### Helper Module

- Add `src/kindshot/telegram_ops.py`.
- Responsibilities:
  - send Telegram messages via Bot API using stdlib only
  - format concise backfill result summaries
  - keep messages short enough for operator use

### Standalone Script

- Add `scripts/collect_backfill_notify.py`.
- Invocation model:
  - `python scripts/collect_backfill_notify.py --from 20260316 --to 20260316`
  - same range semantics as `kindshot collect backfill`
- Behavior:
  - read `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` from environment
  - run backfill using application code, not shell scraping
  - send success/failure message
  - exit non-zero on collector failure

## Message Contract

- Header:
  - `Kindshot Backfill OK`
  - `Kindshot Backfill FAIL`
- Required fields:
  - requested range
  - finalized date
  - processed/complete/partial/skipped counts
  - collector health/state after run
- Failure message also includes:
  - exception class
  - exception message

## Rollout

1. Validate existing Telegram delivery with one-shot env injection.
2. Implement helper + script.
3. Run local tests.
4. Optionally copy the script/helper to server and use env-injected one-shot execution.

## Logging

- Keep collector logs unchanged.
- Script may print the same success/failure summary to stdout for shell capture.
- Telegram delivery failure should not silently swallow collector exceptions.

## Validation

- unit test: success message formatting
- unit test: failure message formatting
- unit test: Telegram sender request building with mocked transport
- local compile + pytest

## Rollback

- Remove:
  - `src/kindshot/telegram_ops.py`
  - `scripts/collect_backfill_notify.py`
  - `tests/test_telegram_ops.py`
- No production state migration is required.
