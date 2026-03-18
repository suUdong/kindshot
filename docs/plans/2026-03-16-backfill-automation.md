# Backfill Automation Runner

## Objective

- Turn backfill from a manually chosen date range into a scheduler-friendly one-shot runner.
- Keep deployment unchanged: no `deploy/` edits, no systemd/cron file changes in repo.
- Make the automation path directly usable on the server with one command and Telegram reporting.

## Current State

- `kindshot collect backfill` works and keeps cursor/finalized state.
- `scripts/collect_backfill_notify.py` can run a chosen range and send one Telegram message.
- Operators still have to pick a range manually.
- There is no overlap protection if two backfill commands are started at once.

## Scope

- Add one automation helper module in app code.
- Add one scheduler-friendly wrapper script.
- Add tests for:
  - automatic range calculation
  - oldest-date clamp behavior
  - no-op behavior when the backlog target is already satisfied
  - overlap lock behavior

## Non-Goals

- Do not modify server cron/systemd files from the repo.
- Do not add daemon-style long-running loops.
- Do not persist Telegram secrets.
- Do not change collector log schema.

## Proposed Behavior

### Automatic Range Selection

- Default start date:
  - latest blocked date (`partial` or `error`) if present
  - otherwise `collector_state.cursor_date` if present
  - otherwise current `finalized_date`
- Default end date:
  - `start_date - (max_days - 1)`
- Optional floor:
  - `--oldest-date YYYYMMDD`
  - if the computed range would go older than this date, clamp to it
- If the current cursor is already older than `oldest_date`, treat the run as a no-op.
- If backlog contains blocked dates newer than the cursor, automation must retry the blocked date first instead of marching further into older history.

### Overlap Protection

- Use a lock file under `data/collector/backfill_auto.lock`.
- If the lock already exists, exit non-zero without calling the collector.
- Do not send Telegram on lock contention by default to avoid spam.

### Reporting

- Reuse existing Telegram backfill formatter for real collector runs.
- Add concise stdout/optional Telegram output for automation no-op cases:
  - finalized date
  - current cursor
  - oldest-date floor
  - reason the run did not execute

## CLI Shape

Add:

```bash
python scripts/collect_backfill_auto.py --max-days 4
python scripts/collect_backfill_auto.py --max-days 4 --oldest-date 20260301
python scripts/collect_backfill_auto.py --max-days 4 --oldest-date 20260301 --notify-noop
```

Behavior:

- Uses environment `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` if present.
- If Telegram vars are missing, still runs collector automation and prints stdout summary.
- Real collector failures still exit non-zero.

## Rollout

1. Add automation helper module and wrapper script.
2. Validate locally with unit tests.
3. Copy the script/module to the server.
4. Run one manual server invocation as the scheduler stand-in.
5. After validation, operators can place the command in cron/systemd outside the repo.

## Validation

- local compile
- targeted tests for automation helper and script-adjacent behavior
- full pytest
- one server smoke run with a short bounded range

## Rollback

- Remove:
  - `src/kindshot/backfill_auto.py`
  - `scripts/collect_backfill_auto.py`
  - `tests/test_backfill_auto.py`
- Revert any companion doc/memory updates.
