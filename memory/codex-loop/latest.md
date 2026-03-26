Hypothesis: Kindshot's current server-operability gap is not just missing trades but missing operator visibility before the runtime log appears. A consolidated monitor that combines runtime-log existence, polling-trace activity, heartbeat progress, NVIDIA journal counts, and structured BUY/SKIP counts should remove the need for repeated manual shell inspection and make the next real paper/live verification window faster to interpret.

Changed files:
- `deploy/server_monitor.py`
- `deploy/logs.sh`
- `deploy/status.sh`
- `tests/test_server_monitor.py`
- `docs/plans/2026-03-27-server-monitoring-improvement.md`
- `.omx/context/server-monitoring-20260326T193323Z.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Validation:
- Added `deploy/server_monitor.py` to summarize:
  - runtime log existence / metadata
  - structured event/decision/snapshot counts
  - BUY/SKIP and decision-source split
  - polling trace totals and latest positive poll
  - journal NVIDIA `200 OK`, service starts, timeout failures, and latest heartbeat
- Wired the monitor into `deploy/logs.sh monitor [날짜]` and `deploy/status.sh`
- Added parser/formatting coverage in `tests/test_server_monitor.py`
- `git diff --check` passed
- `python3 -m py_compile deploy/server_monitor.py` passed
- `source .venv/bin/activate && python -m pytest tests/test_server_monitor.py tests/test_daily_report.py tests/test_strategy_observability.py -q` passed (`7 passed`)

Risk and rollback note:
- This slice changes only operator tooling and documentation; it does not change strategy, execution, or deployment wiring.
- `deploy/status.sh` now delegates summary logic to Python, so future monitor output changes should be made in `deploy/server_monitor.py` instead of re-adding bespoke shell parsing.
- Roll back by reverting `deploy/server_monitor.py`, the shell wrapper updates, the new tests, and restoring the previous `memory/codex-loop/latest.md` / `memory/codex-loop/session.md`.
