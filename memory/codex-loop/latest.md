Hypothesis: If the latest validated Kindshot runtime and reporting tree is re-synced to the paper server, reinstalled in the remote venv, and both services are restarted cleanly, then the server will reflect the current backtest, entry, exit, and performance surfaces without changing secrets or deploy definitions.

Changed files:
- `DEPLOYMENT_LOG.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Implementation summary:
- Re-validated the current local workspace before deployment:
  - runtime/reporting compile pass
  - targeted regression suite for decision, health, pipeline, context card, trade DB, and monthly backtest
  - full `pytest` regression
  - fresh local report generation for:
    - `scripts/runtime_latency_report.py`
    - `scripts/entry_filter_analysis.py`
    - `scripts/monthly_full_strategy_backtest.py`
- Synced the latest runtime-relevant tree to `kindshot-server:/opt/kindshot` via `rsync`:
  - `src/`
  - `dashboard/`
  - `scripts/`
  - `tests/`
  - `config/`
  - `pyproject.toml`
  - `README.md`
  - `requirements.lock`
- Recompiled the remote tree and reinstalled the package in the existing venv.
- Worked around a broken remote `./.venv/bin/pip` shebang by switching to `./.venv/bin/python -m pip install -e . --quiet`.
- Restarted both `kindshot` and `kindshot-dashboard`, then verified runtime health and dashboard HTTP reachability.

Deployment evidence summary:
- Remote host: `kindshot-server` (`/opt/kindshot`)
- Services:
  - `systemctl is-active kindshot` → `active`
  - `systemctl is-active kindshot-dashboard` → `active`
- `systemctl status` showed:
  - `kindshot` active since `2026-03-28 04:27:18 KST`
  - `kindshot-dashboard` active since `2026-03-28 04:27:18 KST`
- `journalctl -u kindshot -n 20` after restart showed:
  - `kindshot 0.1.3 starting`
  - `RecentPatternProfile loaded: dates=20260319,20260320,20260327 trades=14 boost=1 loss=2`
  - `Health server started on 127.0.0.1:8080`
- Remote `/health` summary returned:
  - `status=healthy`
  - `started_at=2026-03-28T04:27:20.859306+09:00`
  - `guardrail_state.configured_max_positions=4`
  - `trade_metrics.total_trades=0`
  - `trade_metrics.total_pnl_pct=0.0`
  - `latency_profile.cache_layers={}`
- Remote dashboard HTTP probe returned:
  - `GET http://127.0.0.1:8501/` → `200 text/html`

Validation:
- local `python3 -m compileall src scripts tests`
- local `.venv/bin/python -m pytest tests/test_decision.py tests/test_health.py tests/test_pipeline.py tests/test_context_card.py tests/test_trade_db.py tests/test_monthly_full_strategy_backtest.py -q` → `140 passed, 1 warning`
- local `.venv/bin/python scripts/runtime_latency_report.py`
- local `.venv/bin/python scripts/entry_filter_analysis.py`
- local `.venv/bin/python scripts/monthly_full_strategy_backtest.py`
- local `.venv/bin/python -m pytest -q` → `1001 passed, 1 skipped, 1 warning`
- diagnostics `lsp_diagnostics_directory` → `0 errors`, `0 warnings`
- remote `./.venv/bin/python -m compileall src/kindshot scripts tests dashboard`
- remote `./.venv/bin/python -m pip install -e . --quiet`
- remote `systemctl is-active kindshot kindshot-dashboard` → both `active`
- remote `curl` equivalent via Python against `http://127.0.0.1:8080/health` → healthy JSON
- remote dashboard HTTP probe via Python against `http://127.0.0.1:8501/` → `200`

Simplifications made:
- Reused the established `rsync` deployment lane instead of introducing a new git- or deploy-script-based path.
- Synced only runtime-relevant directories/files rather than mirroring the whole repository, keeping secrets and non-runtime state untouched.
- Reused the existing remote venv and only changed the install invocation from broken `pip` wrapper to `python -m pip`.

Remaining risks:
- The server is still running in paper mode with VTS quote limitations, so fresh live-session evidence is still needed for intraday entry/exit behavior under actual market hours.
- `/health.latency_profile` currently has no samples immediately after restart because no new post-deploy events have flowed through the instrumented pipeline yet.
- This deployment reflects local worktree state; the server runtime now includes the current local `scripts/entry_filter_analysis.py` and `tests/test_context_card.py` changes even though they are not part of `HEAD`.

Rollback note:
- Re-sync the prior known-good runtime tree to `/opt/kindshot`, rerun `./.venv/bin/python -m pip install -e . --quiet`, and restart `kindshot` plus `kindshot-dashboard`.
