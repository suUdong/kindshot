# 2026-03-27 Trading Log Analysis Report

## Goal

Add a standalone analysis script that can turn a single Kindshot JSONL trading log into an operator-readable report without requiring ad hoc one-off notebooks or manual shell counting.

## Problem

Current log analysis is fragmented across:

- `deploy/daily_report.py` for end-of-day realized return summaries
- `scripts/live_monitor.py` for current-day live BUY monitoring
- manual one-off parsing used for NVIDIA day reports and trading-log investigations

What is still missing is a single standalone report for one JSONL log that answers:

- How many structured decisions were made?
- What was the BUY/SKIP split?
- Which `decision_source` paths dominated?
- How much inline BUY appetite existed before execution?
- Which guardrails blocked those BUYs?
- What reason patterns dominated SKIPs?

## Scope

Implement a bounded analysis surface:

1. Add `scripts/trading_log_report.py`
2. Accept either:
   - `--date YYYYMMDD`
   - `--log-file /path/to/kindshot_YYYYMMDD.jsonl`
3. Produce a text report with:
   - file metadata and record counts
   - structured decision BUY/SKIP totals
   - `decision_source` breakdown
   - inline `decision_action` BUY/SKIP counts from `event` rows
   - BUY-side guardrail blocker breakdown
   - hourly structured decision distribution
   - top repeated skip reasons by source
4. Add focused unit tests for parsing/aggregation and rendering

## Non-Goals

- No deployment changes
- No strategy changes
- No Telegram integration
- No new dependencies
- No direct remote/SSH execution logic

## Design

### Entry point

`python3 scripts/trading_log_report.py --date 20260326`

or

`python3 scripts/trading_log_report.py --log-file logs/kindshot_20260326.jsonl`

### Input model

Single JSONL log containing:

- `event`
- `decision`
- `price_snapshot`

### Output sections

1. `File`
   - path
   - size
   - line count
   - record-type counts
2. `Structured Decisions`
   - total decisions
   - BUY / SKIP
   - `decision_source` table
3. `Inline Intent`
   - inline BUY / SKIP from `event.decision_action`
   - BUY guardrail blocker counts
4. `Time Of Day`
   - per-hour decision counts by source
5. `Top Reasons`
   - repeated skip reasons by source
6. `Bottom Line`
   - one-line interpretation:
     - fully defensive
     - mixed
     - BUY-heavy

### Implementation note

Keep the implementation standalone under `scripts/` and avoid coupling it to `deploy/`. Reuse simple JSONL parsing patterns already used elsewhere, but do not add a new shared abstraction unless the script clearly needs it.

## Validation

- `python3 -m py_compile scripts/trading_log_report.py`
- `source .venv/bin/activate && python -m pytest tests/test_trading_log_report.py -q`

## Rollback

- Revert `scripts/trading_log_report.py`
- Revert `tests/test_trading_log_report.py`
- Revert this design doc and the run-summary updates tied to the slice
