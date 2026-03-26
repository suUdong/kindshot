Hypothesis: Kindshot's current server-operability gap is not just missing trades but missing operator visibility before the runtime log appears. A consolidated monitor that combines runtime-log existence, polling-trace activity, heartbeat progress, NVIDIA journal counts, and structured BUY/SKIP counts should remove the need for repeated manual shell inspection and make the next real paper/live verification window faster to interpret.

Changed files:
- `deploy/logs.sh`
- `deploy/status.sh`
- `scripts/server_monitor.py`
- `tests/test_server_monitor.py`
- `docs/plans/2026-03-27-server-monitoring-improvement.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Validation:
- Added `scripts/server_monitor.py` to summarize:
  - runtime log existence / metadata
  - structured event/decision/snapshot counts
  - BUY/SKIP and decision-source split
  - polling trace totals and latest positive poll
  - journal NVIDIA `200 OK`, service starts, timeout failures, and latest heartbeat
- Added parser/formatting coverage in `tests/test_server_monitor.py`
- Restored `deploy/logs.sh` and `deploy/status.sh` to their pre-existing behavior after re-checking the workspace rule that automated runs must not keep `deploy/` edits
- `git diff --check` passed
- `python3 -m py_compile scripts/server_monitor.py` passed
- `source .venv/bin/activate && python -m pytest tests/test_server_monitor.py tests/test_daily_report.py tests/test_strategy_observability.py -q` passed (`8 passed`)
- `python3 scripts/server_monitor.py 20260327` renders the operator summary without leaking `sudo` errors in local fallback mode
- `bash deploy/status.sh` and `bash deploy/logs.sh help` remain unchanged after reverting prohibited `deploy/` edits

Risk and rollback note:
- This slice changes only operator tooling and documentation; it does not change strategy, execution, or deployment wiring.
- The monitor is intentionally standalone under `scripts/` because automated `deploy/` edits are forbidden in this workspace.
- Roll back by reverting `scripts/server_monitor.py`, the new tests/doc changes, and restoring the previous `memory/codex-loop/latest.md` / `memory/codex-loop/session.md`.
