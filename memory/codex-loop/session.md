# Kindshot Codex Session State

## Current Session

- Branch: `main`
- Phase: `Risk Management v2 Deployed`
- Focus: make intraday portfolio guardrails more runtime-effective by tightening the daily loss floor from recent same-day win rate, preserving consecutive-loss halts, and wiring sector concentration through real buy/sell bookkeeping.
- Active hypothesis: persisting recent closed-trade outcomes and ticker-sector mappings inside `GuardrailState` is the smallest reliable fix because it upgrades existing guardrail branches into restart-safe runtime behavior without adding a second state store or new external metadata dependency.
- Blocker: none.

## Environment

- Host: local workspace
- Runtime target: AWS Lightsail `kindshot-server` (`/opt/kindshot`, paper mode)
- Local note: deployment used direct `rsync` of committed `src/` + `tests/`, then remote `.venv/bin/python -m pip install . --quiet`
- Validation status:
  - local `python3 -m compileall src scripts tests dashboard` passed
  - local targeted pytest (`test_guardrails`, `test_pipeline`, `test_health`, `test_config`) passed (`213 passed, 1 warning`)
  - local full `pytest -q` passed (`971 passed, 1 skipped, 1 warning`)
  - local affected-file diagnostics returned `0 errors`, `0 warnings`
  - remote `python3 -m compileall src/kindshot scripts tests dashboard` passed
  - remote `.venv/bin/python -m pip install . --quiet` passed in `/opt/kindshot`
  - remote `sudo systemctl restart kindshot kindshot-dashboard` succeeded
  - remote `curl http://127.0.0.1:8080/health` returned `guardrail_state.recent_closed_trades=0`, `recent_win_rate_multiplier=1.0`, `consecutive_loss_halt_threshold=3`, `sector_positions={}`
  - remote `curl -I http://127.0.0.1:8501` returned `HTTP/1.1 200 OK`
  - remote journal showed clean startup, `RecentPatternProfile loaded`, and health server bind

## Last Completed Step

- Implemented risk management v2 on `main` by extending `GuardrailState`, wiring KIS sector metadata through runtime context, and exposing the new guardrail state via `/health`.
- Pushed commit `839ffdc`, redeployed it to `kindshot-server`, restarted both services with `sudo`, and captured fresh remote health/dashboard/journal evidence.

## Next Intended Step

- Observe the next same-day closed trades on `kindshot-server` to confirm the recent win-rate multiplier starts tightening the floor when win rate drops below threshold.
- Verify during the next live paper session that KIS continues to emit non-empty `bstp_kor_isnm` values so sector concentration remains effective on real BUY attempts.
- If sector metadata proves unstable in VTS mode, decide whether to add a secondary cached sector source or narrow the guardrail to only enforce when sector quality is confirmed.

## Notes

- This run changed intraday risk bookkeeping and health observability only; it did not alter `deploy/`, secrets, `.env`, or live-order behavior.
- The server remains in VTS mode for pricing, so stale-price warnings are expected at startup.
- Fresh deployment evidence is recorded in `DEPLOYMENT_LOG.md` and `memory/codex-loop/latest.md`.
