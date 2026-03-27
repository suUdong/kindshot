# Kindshot Codex Session State

## Current Session

- Branch: `main`
- Phase: `News Signal Accuracy Hardening`
- Focus: harden article-style contract/news parsing so KIS commentary titles stop leaking weak `수주` / `공급계약` signals into the positive decision path.
- Active hypothesis: raw-title bucket IGNORE protections should stay intact, while a source-aware downstream analysis headline parser should tighten contract/article preflight and quality handling.
- Blocker: none.

## Environment

- Host: local workspace
- Runtime target: Python `3.11+`
- Current local venv: `.venv` uses Python `3.12.3`
- Validation status:
  - `python3 -m compileall src/kindshot tests scripts` passed
  - `.venv/bin/python -m pytest tests/test_headline_parser.py tests/test_bucket.py tests/test_decision.py tests/test_guardrails.py tests/test_pipeline.py -q` passed (`342 passed, 1 skipped`)
  - `.venv/bin/python -m pytest tests/test_price.py tests/test_strategy_observability.py -q` passed (`32 passed, 1 skipped`)
  - `.venv/bin/python -m pytest -q` passed (`806 passed, 2 skipped, 1 warning`)

## Last Completed Step

- Added `headline_parser.py` and wired normalized analysis headlines into decision/preflight/penalty/hold-profile paths.
- Preserved raw-title bucketing so existing IGNORE override protections for broker/article prefixes remain active.
- Added parser/decision/pipeline regression tests and fixed two unrelated full-suite validation gaps discovered during verification (`price.py` entry-time determinism and strategy observability config alignment).

## Next Intended Step

- Monitor fresh paper logs to confirm weak KIS article-style contract flow shrinks without suppressing direct disclosure-style catalysts.
- After collecting that evidence, resume the roadmap-backed historical collection / real-environment validation slice.

## Notes

- This slice changes analysis-time parsing and report reconstruction only; raw logging, deploy paths, secrets, and live-order behavior remain unchanged.
- Full-suite warning remains in `tests/test_health.py` as `NotAppKeyWarning`; no new warnings were introduced by this slice.
