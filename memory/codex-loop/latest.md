Hypothesis: If Kindshot keeps raw-title bucketing intact but adds a source-aware analysis headline parser for contract/article handling downstream, it can reduce weak KIS article-style `수주` / `공급계약` false positives without muting direct disclosure-style catalysts.

Changed files:
- `docs/plans/2026-03-27-news-signal-source-hardening.md`
- `src/kindshot/headline_parser.py`
- `src/kindshot/decision.py`
- `src/kindshot/guardrails.py`
- `src/kindshot/pipeline.py`
- `src/kindshot/price.py`
- `src/kindshot/strategy_observability.py`
- `tests/test_headline_parser.py`
- `tests/test_bucket.py`
- `tests/test_decision.py`
- `tests/test_guardrails.py`
- `tests/test_pipeline.py`
- `tests/test_price.py`
- `tests/test_strategy_observability.py`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Validation:
- Added `headline_parser.py` to normalize analysis headlines and detect commentary-style contract headlines.
- Kept `classify(raw.title)` unchanged so existing IGNORE protections for broker/article prefixes remain intact.
- Wired normalized analysis headlines into contract preflight, article penalty, headline-quality penalty, and hold-profile routing.
- Added regression coverage for:
  - raw-title bucket IGNORE preservation
  - contract/article preflight tightening
  - normalized analysis headline handoff into decision flow
  - headline parser helpers
- Fixed deterministic validation gaps uncovered by the full suite:
  - `price.py` now records entry KST from `t0_ts` instead of wall-clock now
  - strategy observability defaults/tests align with current runtime config
- Architect verification passed after follow-up fixes for disclosure-source `dorg` handling, fallback raw-headline article detection, and enabled pipeline propagation coverage.
- `python3 -m compileall src/kindshot tests scripts` passed
- `.venv/bin/python -m pytest tests/test_headline_parser.py tests/test_bucket.py tests/test_decision.py tests/test_guardrails.py tests/test_pipeline.py -q` passed (`347 passed`)
- `.venv/bin/python -m pytest tests/test_price.py tests/test_strategy_observability.py -q` passed (`32 passed, 1 skipped`)
- `.venv/bin/python -m pytest -q` passed (`811 passed, 1 skipped, 1 warning`)

Risk and rollback note:
- Main residual risk is heuristic over-suppression: some non-disclosure KIS article titles with contract language may now be skipped earlier. This is intentional, but should be verified against fresh paper logs.
- Raw event logging, deploy behavior, secrets, and live-order behavior remain unchanged.
- Roll back by reverting the files listed above; the change is confined to analysis/evaluation/reporting paths.
