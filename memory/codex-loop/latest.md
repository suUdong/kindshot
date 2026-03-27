Hypothesis: If the Kindshot dashboard is upgraded from the current v8 summary surface to an observability-first surface, operators should be able to read intraday/weekly P&L with drawdown, inspect blocked-BUY shadow outcomes, compare v64-v65-v66 trends, and monitor the live news feed without leaving the dashboard.

Changed files:
- `.omx/context/ralph-kindshot-dashboard-v8-upgrade-20260327T080706Z.md`
- `.omx/plans/prd-kindshot-dashboard-observability-upgrade.md`
- `.omx/plans/test-spec-kindshot-dashboard-observability-upgrade.md`
- `docs/plans/2026-03-27-dashboard-observability-upgrade.md`
- `dashboard/app.py`
- `dashboard/data_loader.py`
- `tests/test_dashboard.py`
- `DEPLOYMENT_LOG.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Validation:
- Local verification:
  - `python3 -m compileall dashboard tests scripts src` passed
  - `.venv/bin/python -m pytest tests/test_dashboard.py -q` passed (`21 passed`)
  - `.venv/bin/python -m pytest -q` passed (`820 passed, 1 skipped, 1 warning`)
  - diagnostics on `dashboard/app.py`, `dashboard/data_loader.py`, and `tests/test_dashboard.py` returned 0 issues
- Deployment:
  - committed dashboard/design changes as `fa47b64` and pushed `main`
  - synced `dashboard/` to `kindshot-server:/opt/kindshot/dashboard/` via `rsync`
  - restarted `kindshot-dashboard`
- Remote verification:
  - `/opt/kindshot/dashboard/app.py` contains `compute_daily_equity_curve`, `load_shadow_trade_pnl`, `load_version_trend`, and `실시간 뉴스 피드 모니터`
  - `systemctl is-active kindshot-dashboard` → `active`
  - `curl http://127.0.0.1:8080/health` → `healthy`
  - `curl -I http://127.0.0.1:8501` → `HTTP/1.1 200 OK`

Risk and rollback note:
- Residual risk is data availability, not code health: local shadow snapshot rows are still sparse, so the new shadow dashboard may show empty-state/pending-state until more blocked BUY samples accumulate.
- Roll back by re-syncing the prior `dashboard/` tree (or reverting `fa47b64`) and restarting `kindshot-dashboard`.
