Hypothesis: If the runtime builds recent profitability profiles from the same reconstructed trade semantics used by operator backtest analysis, then pattern-based confidence boosts and loser-pattern guardrails will reflect the true recent trade window instead of a stale or narrower slice.

Changed files:
- `src/kindshot/config.py`
- `src/kindshot/pattern_profile.py`
- `docs/plans/2026-03-28-pattern-profitability-profiles.md`
- `DEPLOYMENT_LOG.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`
- `memory/codex-loop/roadmap.md`

Implementation summary:
- `pattern_profile.py` now attempts profile construction through the existing `scripts/backtest_analysis.py` reconstruction path before falling back to raw `TradeDB` backfill queries, so runtime selection uses the same executed-trade semantics as operator analysis.
- The recent pattern summary is now persisted to `recent_pattern_profile_path`, giving the runtime and health surface a concrete artifact to expose.
- The default recent-pattern lookback was widened from `6` to `7` log days so the current active recent window captures the `2026-03-19`, `2026-03-20`, `2026-03-27` trade cohort.
- Local reconstructed profile after the change produced:
  - top exact profit combo: `mna|005380|midday`
  - active boost cohort: `hour_bucket=midday`
  - active loss guardrails: `contract|open`, `clinical_regulatory|068270`
- Pushed commit `c0c42e2`, redeployed it to `kindshot-server` via clean `git archive` export + `rsync`, restarted both services with `sudo`, and verified the runtime health/dashboard surface remotely.

Validation:
- local `python3 -m compileall src scripts tests dashboard`
- local `.venv/bin/python -m pytest tests/test_strategy_observability.py tests/test_pattern_profile.py tests/test_backtest_analysis.py tests/test_pipeline.py tests/test_config.py -q` → `54 passed`
- local `.venv/bin/python -m pytest -x -q` → `963 passed, 1 skipped, 1 warning`
- local affected-file diagnostics → `0 errors`, `0 warnings`
- local runtime profile build (`build_recent_pattern_profile(Config())`) returned:
  - `analysis_dates=['20260319', '20260320', '20260327']`
  - `boost_patterns=['hour_bucket|midday']`
  - `loss_guardrail_patterns=['contract|open', 'clinical_regulatory|068270']`
- remote `python3 -m compileall src/kindshot scripts tests dashboard`
- remote `source .venv/bin/activate && python -m pip install . --quiet`
- remote `sudo systemctl restart kindshot kindshot-dashboard`
- remote `systemctl is-active kindshot kindshot-dashboard` → `active`, `active`
- remote `curl -sf http://127.0.0.1:8080/health` returned:
  - `status: "healthy"`
  - `recent_pattern_profile.enabled: true`
  - `recent_pattern_profile.analysis_dates: ['20260319', '20260320', '20260327']`
  - `recent_pattern_profile.loss_guardrail_patterns: 2`
  - `top_profit_exact.key: "mna|005380|midday"`
- remote `curl -I http://127.0.0.1:8501` → `HTTP/1.1 200 OK`
- remote journal after restart showed:
  - `Backfilled 20260318/19/20/23/26/27 BUY trades`
  - `RecentPatternProfile loaded: dates=20260319,20260320,20260327 trades=12 boost=0 loss=2`
  - `Health server started on 127.0.0.1:8080`

Simplifications made:
- Reused the existing `backtest_analysis.py` trade reconstruction path instead of maintaining a second runtime-only parser.
- Kept the runtime fallback to `TradeDB` backfill so profile construction still works if the analysis helper cannot load.
- Persisted only the summary artifact, not a second raw-trade cache layer.

Remaining risks:
- The deployed server's current log window still yields `0` active boost cohorts and `2` active loss guardrails, so this slice is currently acting as loss-filter tightening rather than confidence expansion on remote evidence.
- Runtime uses server-side log history as source of truth; local operator artifacts can show a stronger boost cohort than the current deployed server if the log windows differ.
- The runtime is still in VTS quote mode, so live-day pattern evolution needs market-hours observation before tuning thresholds further.

Rollback note:
- Re-sync the prior known-good tree to `/opt/kindshot`, reinstall with the remote venv, and restart `kindshot` + `kindshot-dashboard`.
