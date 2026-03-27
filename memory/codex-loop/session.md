# Kindshot Codex Session State

## Current Session

- Branch: `main`
- Phase: `v70 Pattern Profitability Profiles Deployed`
- Focus: align runtime recent-pattern profiles with the real backtest-analysis trade window, persist the profile summary, and verify the deployed runtime uses the expected recent loss cohorts.
- Active hypothesis: building `RecentPatternProfile` from the same reconstructed trade semantics as `scripts/backtest_analysis.py` is the smallest reliable fix because it prevents runtime/operator drift in which recent loss/boost cohorts are computed from different trade windows.
- Blocker: none.

## Environment

- Host: local workspace
- Runtime target: AWS Lightsail `kindshot-server` (`/opt/kindshot`, paper mode)
- Local note: deployment again used a clean `git archive` export from pushed `HEAD`
- Validation status:
  - local `python3 -m compileall src scripts tests dashboard` passed
  - local targeted pytest (`test_strategy_observability`, `test_pattern_profile`, `test_backtest_analysis`, `test_pipeline`, `test_config`) passed
  - local full `pytest -x -q` passed (`963 passed, 1 skipped, 1 warning`)
  - local affected-file diagnostics returned `0 errors`, `0 warnings`
  - remote `python3 -m compileall src/kindshot scripts tests dashboard` passed
  - remote `python -m pip install . --quiet` passed in `/opt/kindshot/.venv`
  - remote `sudo systemctl restart kindshot kindshot-dashboard` succeeded
  - remote `curl http://127.0.0.1:8080/health` returned `recent_pattern_profile.enabled=true`, `analysis_dates=['20260319', '20260320', '20260327']`, `loss_guardrail_patterns=2`
  - remote `curl -I http://127.0.0.1:8501` returned `HTTP/1.1 200 OK`
  - remote journal showed clean startup, trade backfill, `RecentPatternProfile loaded`, and health server bind

## Last Completed Step

- Verified the current recent-pattern runtime path against local backtest-analysis outputs and tightened the runtime/profile alignment.
- Updated the default recent-pattern lookback to `7` log days and made the runtime persist the resulting summary artifact.
- Pushed commit `c0c42e2`, redeployed it to `kindshot-server`, restarted both services with `sudo`, and captured fresh remote health/dashboard/journal evidence.

## Next Intended Step

- Observe the next market session to confirm whether the remote profile remains loss-only (`boost=0`, `loss=2`) or starts emitting a stable boost cohort under live paper data.
- If the remote profile continues to produce no boost cohorts, decide whether the profitability hypothesis should stay drawdown-first (loss guards only) or whether thresholds/lookback should be widened deliberately.
- If real quote keys become available, re-verify pattern profile behavior outside VTS mode and compare it to the current VTS-driven recent window.

## Notes

- This run changed recent-pattern profile sourcing and summary persistence only; it did not alter `deploy/`, secrets, `.env`, or live-order behavior.
- The server remains in VTS mode for pricing, so stale-price warnings are expected at startup.
- Fresh deployment evidence is recorded in `DEPLOYMENT_LOG.md` and `memory/codex-loop/latest.md`.
