Hypothesis: If the remote runtime tree is reconciled to local `main` at `44783ee` and both services restart cleanly, then Kindshot will be deployment-consistent for the full `v71 + NLP + volume + sector` stack, and the remaining uncertainty will narrow to whether fresh live events arrive to exercise those paths.

Changed files:
- `DEPLOYMENT_LOG.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`
- `.omx/context/ralph-kindshot-final-deploy-20260328T010904Z.md`

Implementation summary:
- Reconciled `/opt/kindshot` with local `main` via `rsync`, closing remote drift in `src/kindshot/config.py`, `src/kindshot/main.py`, `src/kindshot/telegram_ops.py`, and `tests/test_telegram_ops.py`.
- Recompiled and reinstalled the remote package, then restarted both `kindshot` and `kindshot-dashboard`.
- Verified `/health` and dashboard HTTP after warm-up, then watched live journal and polling output for roughly 150 seconds to determine whether new market events exercised the runtime.

Deployment evidence summary:
- Remote host: `kindshot-server` (`/opt/kindshot`)
- Target commit synced from local: `44783ee`
- Services:
  - `kindshot` active since `2026-03-28 10:16:13 KST`
  - `kindshot-dashboard` active since `2026-03-28 10:16:13 KST`
- Remote `/health` after warm-up returned:
  - `status=healthy`
  - `started_at=2026-03-28T10:16:15.416286+09:00`
  - `last_poll_source=feed`
  - `last_poll_age_seconds=7`
  - `events_seen=0`
  - `events_processed=0`
  - `llm_calls=0`
- Remote dashboard probe returned:
  - `HEAD http://127.0.0.1:8501/` → `200`
  - `Content-Type: text/html`

Validation:
- local `python3 -m compileall src tests scripts dashboard`
- local `.venv/bin/python -m pytest tests/test_news_semantics.py tests/test_decision.py tests/test_pipeline.py tests/test_trade_db.py tests/test_context_card.py tests/test_guardrails.py tests/test_entry_filter_analysis.py tests/test_dashboard.py tests/test_volatility_regime.py -q` → `363 passed`
- local `.venv/bin/python -m pytest -q` → `1030 passed, 1 skipped, 1 warning`
- local diagnostics `lsp_diagnostics_directory` → `0 errors`, `0 warnings`
- remote `rsync --dry-run --checksum` found drift in `config.py`, `main.py`, `telegram_ops.py`, and `tests/test_telegram_ops.py`
- remote `rsync --checksum` synced `src/`, `dashboard/`, `tests/`, `scripts/`, `pyproject.toml`, `README.md`, `requirements.lock`
- remote `./.venv/bin/python -m compileall src/kindshot tests scripts dashboard`
- remote `./.venv/bin/python -m pip install -e . --quiet`
- remote `sudo -n systemctl restart kindshot kindshot-dashboard`
- remote `curl -fsS http://127.0.0.1:8080/health`
- remote dashboard `HEAD http://127.0.0.1:8501/`
- remote live monitor window:
  - journal heartbeats only, no post-start runtime error
  - polling trace repeated `items=0 raw=40 dup=40 max_t=235650 last_t=235650`
  - `polling_trace_20260328.jsonl` stats showed `2137` polls, `2` total new items, `0` errors

Simplifications made:
- Kept deployment file-based with direct `rsync` and remote reinstall instead of changing `deploy/` automation.
- Reused existing health endpoint, dashboard probe, journal, and polling trace surfaces rather than adding new operator tooling in this run.

Remaining risks:
- No fresh live events arrived during the monitor window, so the NLP / sector / volume decision paths were not exercised after restart.
- No current-day `kindshot_20260328.jsonl` existed during monitoring, which matches the `events_seen=0` state but leaves structured-event verification pending the next live item.
- The server remains in VTS-backed paper mode because `KIS_REAL_APP_KEY` / `KIS_REAL_APP_SECRET` are absent; stale-exit and T5M loss-exit behavior remain limited in this environment.

Rollback note:
- Re-sync the prior known-good runtime tree to `/opt/kindshot`, rerun `./.venv/bin/python -m pip install -e . --quiet`, and restart `kindshot` plus `kindshot-dashboard`.
