Hypothesis: If the runtime keeps a same-day recent closed-trade outcome window and real open-position sector mappings inside `GuardrailState`, then Kindshot can tighten its effective daily loss floor when recent win rate degrades, halt after repeated losses, and enforce same-sector exposure limits using real runtime bookkeeping instead of partially wired branches.

Changed files:
- `src/kindshot/config.py`
- `src/kindshot/context_card.py`
- `src/kindshot/guardrails.py`
- `src/kindshot/health.py`
- `src/kindshot/kis_client.py`
- `src/kindshot/pipeline.py`
- `tests/test_config.py`
- `tests/test_guardrails.py`
- `tests/test_health.py`
- `tests/test_pipeline.py`
- `docs/plans/2026-03-28-risk-management-v2.md`
- `DEPLOYMENT_LOG.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`
- `memory/codex-loop/roadmap.md`

Implementation summary:
- `GuardrailState` now persists same-day closed-trade outcomes, ticker→sector mappings, and derived recent win-rate properties so dynamic daily loss tightening survives restarts.
- `resolve_daily_loss_budget()` now combines the existing streak/profit-lock logic with a recent win-rate multiplier that only tightens the floor and never expands beyond the configured base loss limit.
- KIS quote parsing now carries `bstp_kor_isnm` through `PriceInfo` and `ContextCardData`, and pipeline BUY bookkeeping records sector state so `SECTOR_CONCENTRATION` becomes runtime-effective.
- SELL bookkeeping now recovers persisted sector mappings when final closes arrive without an explicit sector argument.
- `/health.guardrail_state` now exposes recent closed-trade count, recent win rate, recent win-rate multiplier, sector positions, and the configured consecutive-loss halt threshold.
- Pushed commit `839ffdc`, redeployed the runtime to `kindshot-server` via `rsync` (`src/`, `tests/`) plus remote venv reinstall, and verified the new guardrail state fields remotely.

Validation:
- local `python3 -m compileall src scripts tests dashboard`
- local `.venv/bin/python -m pytest tests/test_guardrails.py tests/test_pipeline.py tests/test_health.py tests/test_config.py -q` → `213 passed, 1 warning`
- local `.venv/bin/python -m pytest -q` → `971 passed, 1 skipped, 1 warning`
- local affected-file diagnostics → `0 errors`, `0 warnings`
- remote `python3 -m compileall src/kindshot scripts tests dashboard`
- remote `.venv/bin/python -m pip install . --quiet`
- remote `sudo systemctl restart kindshot kindshot-dashboard`
- remote `systemctl is-active kindshot kindshot-dashboard` → `active`, `active`
- remote `curl -sf http://127.0.0.1:8080/health` returned:
  - `status: "healthy"`
  - `guardrail_state.dynamic_daily_loss_floor_won: -3000000.0`
  - `guardrail_state.dynamic_daily_loss_remaining_won: 3000000.0`
  - `guardrail_state.recent_closed_trades: 0`
  - `guardrail_state.recent_win_rate: null`
  - `guardrail_state.recent_win_rate_multiplier: 1.0`
  - `guardrail_state.consecutive_loss_halt_threshold: 3`
  - `guardrail_state.sector_positions: {}`
- remote `curl -I http://127.0.0.1:8501` → `HTTP/1.1 200 OK`
- remote journal after restart showed:
  - `kindshot 0.1.3 starting`
  - `RecentPatternProfile loaded: dates=20260319,20260320,20260327 trades=14 boost=1 loss=2`
  - `Health server started on 127.0.0.1:8080`

Simplifications made:
- Reused the existing `GuardrailState` persistence path instead of introducing a second performance-derived state store.
- Used the existing KIS quote payload field `bstp_kor_isnm` for sector wiring instead of adding a new external metadata lookup dependency.
- Tightened daily loss limits only; no win-rate-based risk expansion path was added.

Remaining risks:
- Sector concentration depends on KIS continuing to emit `bstp_kor_isnm`; if that field disappears or goes blank, the gate will fail open for that ticker.
- The deployed server currently has `recent_closed_trades: 0`, so the recent win-rate multiplier is idle until the next same-day trade closes.
- The runtime is still in VTS quote mode, so live market-hours observation is still needed for a production-faithful check of sector metadata quality.

Rollback note:
- Re-sync the prior known-good tree to `/opt/kindshot`, reinstall with the remote venv, and restart `kindshot` + `kindshot-dashboard`.
