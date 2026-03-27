# Kindshot Codex Session State

## Current Session

- Branch: `main`
- Phase: `v66 Shadow Snapshot Verification`
- Focus: validate live `shadow_` snapshot collection, quantify opportunity-cost reporting on collected data, and harden shutdown behavior so already-due snapshots are not dropped during restart.
- Active hypothesis: shadow snapshot collection is already live on the paper server, but shutdown-time due-snapshot loss and weak operator reporting reduce trust in the collected data; fixing those two gaps should make v66 monitoring actionable.
- Blocker: none.

## Environment

- Host: local workspace
- Runtime target: Python `3.11+`
- Current local venv: `.venv` uses Python `3.12.3`
- Validation status:
  - `python3 -m compileall src/kindshot tests scripts` passed
  - `.venv/bin/python -m pytest tests/test_price.py tests/test_shadow_analysis.py -q` passed (`31 passed, 1 skipped`)
  - `.venv/bin/python -m pytest -q` passed (`811 passed, 1 skipped, 1 warning`)
  - `.venv/bin/python -m pytest -q` passed again after the shadow-snapshot slice (`815 passed, 1 skipped, 1 warning`)
  - remote `/opt/kindshot/data/runtime/price_snapshots/20260327.jsonl` contains `shadow_7c47fb630764e6b2` with 7 collected horizons
  - remote journal confirms live `Normalized analysis headline [...]`, `Technical indicator adj [...]`, and `Confidence graduated cap [...]` entries after the `2026-03-27 16:42 KST` restart
  - updated `scripts/shadow_analysis.py` correctly reports the copied remote shadow event as `MARKET_CLOSE_CUTOFF`, hour `16:00`, and flat-price/stale suspect

## Last Completed Step

- Verified that live paper runtime is emitting real `shadow_` snapshot data for guardrail-blocked `BUY` decisions.
- Replaced close-only shutdown flush with ready-snapshot flush so already-due snapshots are preserved on shutdown/restart.
- Expanded `scripts/shadow_analysis.py` with skip-reason breakdown, hour breakdown, and flat-price / stale-suspect reporting, then covered the slice with targeted tests and a passing full suite.

## Next Intended Step

- Keep monitoring live paper flow for a higher-volume sample of shadow events, especially intraday cases that are not `MARKET_CLOSE_CUTOFF`.
- If flat-price shadow events continue dominating, treat VTS/stale-price limitations as an operational blocker for deeper opportunity-cost conclusions and decide whether real-price snapshot sourcing can be enabled without touching secret-handling automation.
- After enough shadow and live v66 evidence accumulates, resume the roadmap-backed historical collection / real-environment validation slice.

## Notes

- This slice changes shutdown snapshot handling and operator analysis/reporting only; raw deploy paths, secrets, and live-order behavior remain unchanged.
- Full-suite warning remains in `tests/test_health.py` as `NotAppKeyWarning`; no new warnings were introduced by this slice.
- Current live monitoring caveat: paper runtime still warns that price snapshots use VTS/stale pricing when real KIS keys are absent, so after-close shadow events can look flat even when collection itself is working.
