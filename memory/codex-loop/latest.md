Hypothesis: Recent contract/order losers are obvious enough before the LLM call that a narrow deterministic preflight guard can cut the weakest `수주`/contract cohort without removing the cleaner mid-cap confirmed contract winners.

Changed files:
- `docs/plans/2026-03-26-contract-preflight-guard.md`
- `src/kindshot/decision.py`
- `src/kindshot/pipeline.py`
- `src/kindshot/replay.py`
- `tests/test_decision.py`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Validation:
- Targeted tests:
  - `source .venv/bin/activate && python -m pytest tests/test_decision.py tests/test_rule_fallback.py tests/test_pipeline.py -q` passed (`89 passed`)
- Full tests:
  - `source .venv/bin/activate && python -m pytest -q` passed (`614 passed, 1 warning`)
- Design verification:
  - `docs/plans/2026-03-26-contract-preflight-guard.md` recorded scope, rollout, observability, validation, and rollback before implementation

Risk and rollback note:
- This slice adds a new deterministic `RULE_PREFLIGHT` SKIP path for contract/order-family headlines before the LLM call.
- The main risk is false negatives if the preflight patterns are too broad; the rules were kept narrow to article-style, incremental-order, chase, downtrend, and large-cap contract cases only.
- Roll back by reverting the `decision.py` preflight helper, the `pipeline.py` / `replay.py` keyword pass-through, and the matching regression tests.
