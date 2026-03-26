# 2026-03-27 Server Monitoring Improvement

## Goal

Add a single operator-facing monitor summary for Kindshot server runtime state that makes repeated ad hoc shell inspection unnecessary.

## Problem

Current server monitoring is fragmented:

- `deploy/status.sh` shows raw systemd status, recent journal output, and only shallow JSONL counts.
- `deploy/logs.sh poll` surfaces polling details, but not runtime-log absence, heartbeat progress, or NVIDIA journal counts in one place.
- When today's structured runtime log does not exist yet, operators currently have to manually combine:
  - JSONL existence checks
  - polling trace summaries
  - `journalctl` heartbeat lines
  - NVIDIA endpoint call counts

This is exactly the workflow the NVIDIA day1 reporting loop kept repeating.

## Scope

Implement a bounded monitoring slice:

1. Add a Python summary script that can inspect:
   - current-day runtime log
   - current-day polling trace
   - same-day journal output
2. Surface a concise summary with:
   - runtime log existence and file metadata
   - structured event/decision/price snapshot counts
   - decision BUY/SKIP totals and decision-source split when runtime log exists
   - polling trace recency, positive-poll count, raw-item total, and latest positive poll details
   - journal NVIDIA `200 OK` count, service starts, timeout failures, and latest heartbeat
3. Wire it into shell entrypoints so operators can run one command.
4. Add tests for the parsing/aggregation logic.

## Non-Goals

- No deployment/unit file changes.
- No runtime strategy changes.
- No changes to live order boundaries.
- No new dependencies.

## Design

### Primary entrypoint

Create `deploy/server_monitor.py`.

Why:

- JSONL parsing and summary formatting are easier to test in Python than in shell.
- It can be used directly from server shells and wrapped by existing scripts.

### Input model

For a given date:

- Runtime log: `/opt/kindshot/logs/kindshot_<date>.jsonl`
- Polling trace: `/opt/kindshot/logs/polling_trace_<date>.jsonl`
- Journal command:
  - `journalctl -u kindshot --since "<YYYY-MM-DD> 00:00" --until "<YYYY-MM-DD> 23:59:59" --no-pager`

### Output sections

1. `Current Files`
   - runtime log exists / missing
   - polling trace exists / missing
   - sizes and mtimes when present
2. `Structured Runtime`
   - event / decision / snapshot counts
   - BUY / SKIP totals
   - decision-source breakdown
3. `Polling Trace`
   - total poll_end count
   - total raw items
   - positive-poll count
   - latest poll_end timestamp
   - latest positive poll summary
4. `Journal`
   - NVIDIA `200 OK` count
   - service starts
   - timeout failures
   - latest heartbeat line
5. `Operator Verdict`
   - concise interpretation:
     - no runtime log yet
     - polling active but no structured decisions yet
     - structured runtime active

### Shell integration

- Add `monitor` subcommand to `deploy/logs.sh`
- Have `deploy/status.sh` invoke the Python monitor script instead of bespoke partial summaries

This preserves the existing operator entrypoints while consolidating logic in one testable module.

## Validation

- Unit tests for:
  - runtime log aggregation
  - polling trace aggregation
  - journal text summarization
  - verdict selection when runtime log is missing vs present
- Run:
  - `pytest tests/test_server_monitor.py tests/test_daily_report.py tests/test_strategy_observability.py -q`

## Rollback

- Revert `deploy/server_monitor.py`
- Revert the wrapper changes in `deploy/status.sh` and `deploy/logs.sh`
- Revert tests/docs tied to the new monitor
