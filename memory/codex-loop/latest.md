Hypothesis: If `/health` reads the same feed heartbeat that the watchdog logs use and the runtime exports closed-trade live metrics, operators can trust health/dashboard observability during quiet polling periods without changing trading behavior.

Changed files:
- `dashboard/app.py`
- `src/kindshot/health.py`
- `src/kindshot/main.py`
- `src/kindshot/performance.py`
- `tests/test_dashboard.py`
- `tests/test_health.py`
- `tests/test_performance.py`
- `docs/plans/2026-03-28-v69-observability-alignment.md`
- `DEPLOYMENT_LOG.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`
- `memory/codex-loop/roadmap.md`

Implementation summary:
- `HealthState` now reads `feed.last_poll_at` as the primary `last_poll_at` source and exposes `last_poll_source` / `last_poll_age_seconds` so `/health` matches the watchdog heartbeat path even when polls are empty.
- `PerformanceTracker` now computes intraday closed-trade live metrics (`win_rate`, total P&L, average return, peak, MDD), and `main.py` wires that tracker into the health state.
- `dashboard/app.py` now surfaces those live metrics in both the performance tab and system-status tab, alongside heartbeat freshness.
- Pushed commit `f0e1bc4`, deployed it to `kindshot-server` via clean `git archive` export + `rsync`, restarted both services, and verified the new health payload shape remotely.

Validation:
- local `python3 -m compileall src dashboard tests`
- local `.venv/bin/python -m pytest tests/test_health.py tests/test_performance.py tests/test_dashboard.py -q` → `39 passed`
- local `.venv/bin/python -m pytest -q` → `956 passed, 1 skipped, 1 warning`
- local affected-file diagnostics → `0 errors`
- remote `python3 -m compileall src/kindshot dashboard tests`
- remote `source .venv/bin/activate && python -m pip install . --quiet`
- remote `sudo systemctl restart kindshot kindshot-dashboard`
- remote `systemctl is-active kindshot kindshot-dashboard` → `active`, `active`
- remote `curl -sf http://127.0.0.1:8080/health` returned:
  - `last_poll_source: "feed"`
  - `trade_metrics` block present
  - `last_poll_at=2026-03-28T00:20:31.008898+09:00`
- remote `curl -I http://127.0.0.1:8501` → `HTTP/1.1 200 OK`
- remote journal after restart showed:
  - `kindshot 0.1.3 starting`
  - `Health server started on 127.0.0.1:8080`
  - heartbeat `last_poll=00:20:14`
- remote source grep confirmed deployed files include:
  - `trade_metrics`
  - `last_poll_source`
  - `실시간 트레이딩 메트릭`

Simplifications made:
- Reused the existing clean-export `rsync` deployment path instead of touching remote git metadata.
- Reused the existing intraday `PerformanceTracker` trade list rather than adding a second metrics store.
- Kept dashboard live metrics health-driven so the UI can reflect runtime state without rebuilding it from lagging log files.

Remaining risks:
- Current live metrics are realized/closed-trade only; open-position MTM is still outside this slice.
- The remote runtime is still in VTS quote mode, so live-day metric evolution needs market-hours observation to confirm behavior under real event flow.
- Dashboard rendering was verified by service startup and deployed source inspection, not by browser screenshot automation.

Rollback note:
- Re-sync the prior known-good tree to `/opt/kindshot`, reinstall with the remote venv, and restart `kindshot` + `kindshot-dashboard`.
