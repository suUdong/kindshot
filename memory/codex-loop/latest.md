Hypothesis: If the deployed v70 + risk v2 + max_positions + observability stack is revalidated end-to-end and the dashboard smoke path is made warning-clean, then Kindshot can close the final integration check with cleaner operational evidence and no behavior change to the trading pipeline.

Changed files:
- `dashboard/app.py`
- `dashboard/data_loader.py`
- `DEPLOYMENT_LOG.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Implementation summary:
- Replaced deprecated Streamlit `use_container_width` calls in the dashboard with `width="stretch"` so the remote dashboard smoke gate no longer emits repeated runtime deprecation warnings.
- Normalized multi-day dashboard dataframe concatenation to drop all-NA columns before concat, removing pandas `FutureWarning` noise without changing operator-visible dashboard outputs.
- Pushed commit `6d1a3f4`, rsynced the two dashboard files to `kindshot-server:/opt/kindshot/dashboard/`, restarted `kindshot-dashboard`, and re-ran remote AppTest plus health/log validation.

Validation:
- local `python3 -m compileall src scripts tests dashboard`
- local `.venv/bin/python -m pytest tests/test_dashboard.py -q` → `22 passed`
- local `.venv/bin/python -m pytest -q` → `974 passed, 1 skipped, 1 warning`
- local changed-file diagnostics on `dashboard/app.py` and `dashboard/data_loader.py` → `0 errors`
- remote `python3 -m compileall dashboard`
- remote `systemctl is-active kindshot-dashboard` → `active`
- remote `curl -I http://127.0.0.1:8501` → `HTTP/1.1 200 OK`
- remote AppTest with `PYTHONPATH=. ../.venv/bin/python -W error::FutureWarning` returned:
  - `tab_labels`: `📊 시그널 현황`, `💰 매매 성과`, `📉 기술지표`, `🖥️ 시스템 상태`, `🔬 전략 분석`, `📈 히스토리 분석`
  - `exception_count: 0`
- remote `/health` returned:
  - `status: "healthy"`
  - `last_poll_source: "feed"`
  - `last_poll_age_seconds: 8`
  - `guardrail_state.configured_max_positions: 4`
  - `guardrail_state.recent_closed_trades: 0`
  - `guardrail_state.recent_win_rate_multiplier: 1.0`
  - `recent_pattern_profile.total_trades: 14`
- remote prior-session end-to-end evidence from `logs/kindshot_20260327.jsonl`:
  - `event: 789`
  - `decision: 37`
  - `price_snapshot: 1242`
  - executed BUY count: `5`
  - guardrail-blocked BUY count: `19`
  - sample executed chain: `event_id=4f6ac6e06147eb5d`, ticker `002990`, source `KIS`, bucket `POS_STRONG`, `decision_action=BUY`, plus `t0/t+30s/t+1m/t+2m/t+5m/t+10m` price snapshots
- remote live poll evidence from `logs/polling_trace_20260328.jsonl` showed current `poll_start/poll_end` cycles continuing on `2026-03-28`

Simplifications made:
- Limited the fix to dashboard rendering and data-loader warning paths; no trading, guardrail, config, deploy, or secret-handling code changed.
- Reused the existing `rsync` + systemd restart deployment lane instead of introducing new deployment tooling.

Remaining risks:
- `2026-03-28` is a Saturday in KST, so fresh same-day news-to-trade execution could not be observed; end-to-end confirmation uses live polling plus the latest trading-day log chain from `2026-03-27`.
- The server still runs in VTS pricing mode, so stale-price warnings remain expected until real quote keys are configured.
- `tests/test_health.py` still emits the pre-existing aiohttp `NotAppKeyWarning`; this run did not change that path.

Rollback note:
- Re-sync the prior dashboard files from `5ea0269` (or redeploy the previous known-good tree), then restart `kindshot-dashboard`.
