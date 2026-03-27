Hypothesis: If `max_positions` is moved to a checked-in risk config and runtime only honors small explicit env overrides, then Kindshot can restore a real simultaneous-position cap without editing the legacy `.env` that currently sets `MAX_POSITIONS=9999`.

Changed files:
- `.env.example`
- `config/risk_limits.toml`
- `docs/plans/2026-03-28-risk-management-v2.md`
- `src/kindshot/config.py`
- `src/kindshot/guardrails.py`
- `src/kindshot/health.py`
- `tests/test_config.py`
- `tests/test_health.py`
- `DEPLOYMENT_LOG.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Implementation summary:
- Added checked-in `config/risk_limits.toml` with a repository-owned paper-trading `max_positions=4` default.
- Updated `Config.max_positions` to use the repo risk config by default and only honor `MAX_POSITIONS` env overrides when they stay inside `1..5`, so legacy `9999` values fall back to the safe cap.
- Exposed the resolved cap through `/health.guardrail_state.configured_max_positions`, then pushed commit `5ea0269` and deployed it to `kindshot-server` via `rsync` + remote venv reinstall.

Validation:
- local `python3 -m compileall src scripts tests dashboard`
- local `.venv/bin/python -m pytest tests/test_config.py tests/test_guardrails.py tests/test_health.py tests/test_pipeline.py -q` → `216 passed, 1 warning`
- local `.venv/bin/python -m pytest -q` → `974 passed, 1 skipped, 1 warning`
- local affected-file diagnostics → `0 errors`, `0 warnings`
- local `.venv/bin/python -c "from kindshot.config import Config; print(Config().max_positions)"` → `4`
- remote `python3 -m compileall src scripts tests dashboard`
- remote `./.venv/bin/python -m pip install . --quiet`
- remote `systemctl is-active kindshot kindshot-dashboard` → `active`, `active`
- remote `./.venv/bin/python -c 'from kindshot.config import Config; print(Config().max_positions)'` → `4`
- remote health summary returned:
  - `status: "healthy"`
  - `guardrail_state.configured_max_positions: 4`
  - `guardrail_state.position_count: 0`
  - `guardrail_state.dynamic_daily_loss_floor_won: -3000000.0`
  - `guardrail_state.recent_closed_trades: 0`
  - `guardrail_state.consecutive_loss_halt_threshold: 3`
  - `guardrail_state.sector_positions: {}`
  - `recent_pattern_profile.total_trades: 14`
- remote `curl -I http://127.0.0.1:8501` → `HTTP/1.1 200 OK`
- remote journal after restart showed:
  - `kindshot 0.1.3 starting`
  - `RecentPatternProfile loaded: dates=20260319,20260320,20260327 trades=14 boost=1 loss=2`
  - `Health server started on 127.0.0.1:8080`

Simplifications made:
- Reused the existing `rsync` + remote venv reinstall lane instead of introducing new deployment tooling or editing `.env`.
- Kept the override contract narrow: only `MAX_POSITIONS` values inside `1..5` are honored, and everything else falls back to the checked-in cap.

Remaining risks:
- The server is still in VTS pricing mode, so stale-price warnings remain expected until real quote keys are configured.
- The new cap has been proven in config/health, but it still needs live-session observation to confirm `MAX_POSITIONS` blocks fire as expected when four simultaneous paper positions are already open.

Rollback note:
- Revert or redeploy the tree before `5ea0269`, reinstall with the remote venv, and restart `kindshot` + `kindshot-dashboard`.
