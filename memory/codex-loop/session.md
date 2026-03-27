# Kindshot Codex Session State

## Current Session

- Branch: `main`
- Phase: `Guardrail Recalibration Deployed`
- Focus: use live `2026-03-26` / `2026-03-27` evidence to reduce confidence-driven overblocking with a small supportive-market dynamic guardrail profile and a durable blocked-vs-passed analysis surface.
- Active hypothesis: a market-aware relaxation on confidence-based guardrails and fast-profile cutoff can admit stronger borderline setups while leaving chase-buy, liquidity, and market-close hard stops intact.
- Blocker: none.

## Environment

- Host: local workspace
- Runtime target: Python `3.11+`
- Current local venv: `.venv` uses Python `3.12.3`
- Validation status:
  - `python3 -m compileall src/kindshot tests scripts` passed
  - `.venv/bin/python -m pytest tests/test_guardrails.py tests/test_pipeline.py tests/test_backtest_analysis.py -q` passed (`182 passed`)
  - `.venv/bin/python -m pytest -q` passed (`834 passed, 1 skipped, 1 warning`)
  - affected-file diagnostics returned 0 issues for the changed guardrail/analysis files
  - `.venv/bin/python scripts/backtest_analysis.py --dates 20260326 20260327 --format both --output logs/daily_analysis/guardrail_recalibration_20260326_20260327.txt` passed
  - remote `python3 -m compileall src/kindshot scripts tests` passed on `kindshot-server`
  - remote `pip install -e . --quiet` passed on `kindshot-server`
  - remote `systemctl restart kindshot` succeeded and service returned active at `2026-03-27 17:42:56 KST`
  - remote `trading_log_report.py`, `shadow_analysis.py`, and `backtest_analysis.py` smoke runs passed

## Last Completed Step

- Wrote the Ralph context snapshot, PRD, and test spec for the guardrail recalibration slice.
- Extended `scripts/backtest_analysis.py` with a guardrail review surface that reports passed vs blocked BUY counts, blocker mix, confidence bands, hour buckets, and shadow coverage.
- Added a supportive-market dynamic guardrail profile in runtime so confidence thresholds and fast-profile late-entry cutoff can relax modestly without weakening chase-buy, liquidity, or global market-close hard stops.
- Committed (`652e414`), pushed to `origin/main`, synced the changed files to `kindshot-server`, restarted the service, and ran remote smoke checks.

## Next Intended Step

- Observe the next full runtime day to see whether supportive-market relaxation admits new `76-77` confidence BUYs and whether their realized paths justify keeping the change.
- Increase blocked-BUY shadow coverage so future relax/tighten decisions rely on more than the current `2` server-side shadow traces.
- If `LOW_CONFIDENCE` remains dominant even after dynamic relaxation, inspect upstream LLM confidence collapse separately from guardrail policy.

## Notes

- This slice changes runtime guardrail behavior and analysis/reporting, but still leaves `deploy/`, secrets, and live-order behavior untouched.
- Full-suite warning remains in `tests/test_health.py` as `NotAppKeyWarning`; no new warnings were introduced by this slice.
- Remote venv does not currently include `pytest`, so server-side verification used compile/install/restart/script smoke checks instead of remote unit tests.
