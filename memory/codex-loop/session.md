# Kindshot Codex Session State

## Current Session

- Branch: `main`
- Phase: `Contract Preflight Guard`
- Focus: enforce a narrow deterministic preflight for weak `수주`/contract headlines before the LLM path so recent repeat losers are cut upstream.
- Active hypothesis: article-style, incremental, chase, downtrend, and large-cap contract headlines are weak enough to skip without LLM review.

## Environment

- Host: local workspace
- Runtime target: Python `3.11+`
- Current local venv: `.venv` uses Python `3.12.3`
- Validation status:
  - `source .venv/bin/activate && python -m pytest tests/test_decision.py tests/test_rule_fallback.py tests/test_pipeline.py -q` passed (`89 passed`)
  - `source .venv/bin/activate && python -m pytest -q` passed (`614 passed, 1 warning`)

## Last Completed Step

- Wrote `docs/plans/2026-03-26-contract-preflight-guard.md` for a narrow contract/order preflight strategy change.
- Added `RULE_PREFLIGHT` contract-family SKIP logic in `src/kindshot/decision.py` and threaded `keyword_hits` into live/replay decision calls.
- Locked the new behavior with `tests/test_decision.py` and verified the broader suite still passes.

## Next Intended Step

- Verify `RULE_PREFLIGHT` decisions on the next real paper/live log window and confirm that weak `수주` headlines stop reaching BUY.
- If contract losers persist after that evidence arrives, split the next hypothesis between `ESS` contract handling and `공급계약` hold/entry tightening.

## Notes

- This slice changes decision behavior but keeps deployment paths and live-order boundaries untouched.
- The working tree still contains unrelated pre-existing untracked paths that were not part of this slice.
