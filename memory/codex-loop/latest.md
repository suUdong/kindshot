Hypothesis: If Kindshot carries the avg20d volume-ratio entry-quality slice through local validation, pushes it as a reproducible commit, and redeploys the clean runtime tree to the paper server, then weak-liquidity BUYs will be constrained by the new shared signal path without changing deploy, secret, or live-order behavior.

Changed files:
- `DEPLOYMENT_LOG.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`
- `scripts/entry_filter_analysis.py`
- `src/kindshot/config.py`
- `src/kindshot/context_card.py`
- `src/kindshot/guardrails.py`
- `src/kindshot/models.py`
- `src/kindshot/pipeline.py`
- `src/kindshot/replay.py`
- `tests/test_context_card.py`
- `tests/test_guardrails.py`
- `tests/test_pipeline.py`

Implementation summary:
- Added `min_volume_ratio_vs_avg20d` / `volume_ratio_surge_threshold` config hooks and threaded avg20d volume data through:
  - `ContextCardData`
  - `ContextCard`
  - replay guardrail inputs
  - the final guardrail check
- Added a regular-session hard stop `VOLUME_RATIO_TOO_THIN` plus `apply_volume_ratio_confidence_adjustment()` so BUY confidence and block decisions use the same avg20d liquidity signal.
- Reduced `scripts/entry_filter_analysis.py` to a thin wrapper over the shared `kindshot.entry_filter_analysis` helpers, keeping report generation consistent with runtime/replay logic.
- Fixed the pipeline test helper to use a deterministic timestamp so shadow-snapshot expectations no longer depend on the wall clock.
- Committed the slice as `709cfd7` and pushed it to `origin/main`.
- Re-synced the clean runtime tree to `kindshot-server:/opt/kindshot` via `rsync`, recompiled/reinstalled the remote venv, and restarted both services.

Deployment evidence summary:
- Remote host: `kindshot-server` (`/opt/kindshot`)
- Deployed runtime commit: `709cfd7`
- Services:
  - `systemctl is-active kindshot` → `active`
  - `systemctl is-active kindshot-dashboard` → `active`
- `systemctl status` showed:
  - `kindshot` active since `2026-03-28 06:29:58 KST`
  - `kindshot-dashboard` active since `2026-03-28 06:29:58 KST`
- Remote `/health` summary returned:
  - `status=healthy`
  - `started_at=2026-03-28T06:30:00.854671+09:00`
  - `last_poll_source=feed`
  - `last_poll_age_seconds=12`
  - `guardrail_state.configured_max_positions=4`
  - `trade_metrics.total_trades=0`
  - `trade_metrics.total_pnl_pct=0.0`
  - `recent_pattern_profile.total_trades=14`
- Remote dashboard HTTP probe returned:
  - `HEAD http://127.0.0.1:8501/` → `HTTP/1.1 200 OK`
  - `Content-Type: text/html`

Validation:
- local `python3 -m compileall src scripts tests dashboard`
- local `.venv/bin/python scripts/entry_filter_analysis.py`
- local `.venv/bin/python -m pytest tests/test_guardrails.py tests/test_context_card.py tests/test_pipeline.py tests/test_entry_filter_analysis.py -q` → `232 passed`
- local `.venv/bin/python -m pytest -q` → `1013 passed, 1 skipped, 1 warning`
- local `python3 -m compileall tests/test_pipeline.py`
- diagnostics `lsp_diagnostics_directory` → `0 errors`, `0 warnings`
- `git push origin main` → `709cfd7` pushed
- remote `./.venv/bin/python -m compileall src/kindshot scripts tests dashboard`
- remote `./.venv/bin/python -m pip install -e . --quiet`
- remote `systemctl is-active kindshot kindshot-dashboard` → both `active`
- remote `curl -fsS http://127.0.0.1:8080/health` → healthy JSON
- remote `curl -sSI http://127.0.0.1:8501/` → `HTTP/1.1 200 OK`

Simplifications made:
- Reused the shared `kindshot.entry_filter_analysis` helpers instead of maintaining a second copy of the evidence logic under `scripts/`.
- Kept the new avg20d liquidity check as a narrow guardrail/confidence extension rather than adding another standalone filter subsystem.
- Reused the established `rsync` + remote venv reinstall lane instead of introducing a new deployment path.

Remaining risks:
- The server is still running in paper mode with VTS quote limitations, so fresh live-session evidence is still needed for intraday entry/exit behavior under actual market hours.
- The avg20d volume-ratio thresholds are still calibrated from limited local history and should be revisited only after fresh runtime coverage accumulates.
- `/health.latency_profile` remains empty immediately after restart because no new post-deploy events have flowed through the pipeline yet.

Rollback note:
- Re-sync the prior known-good runtime tree to `/opt/kindshot`, rerun `./.venv/bin/python -m pip install -e . --quiet`, and restart `kindshot` plus `kindshot-dashboard`.
