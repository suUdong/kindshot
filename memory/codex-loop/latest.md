Hypothesis: Surfacing normalized participation and liquidity context in the LLM prompt and cache key will improve BUY/SKIP precision more safely than adding a new predictive subsystem before replay tuning.

Changed files:
- `src/kindshot/decision.py`
- `tests/test_decision.py`
- `memory/codex-loop/roadmap.md`
- `memory/codex-loop/session.md`
- `memory/codex-loop/latest.md`

Validation:
- `$env:UV_CACHE_DIR='C:\workspace\study\kindshot\.uv-cache'; uv run --python 3.11 python -m compileall src tests` passed
- `$env:UV_CACHE_DIR='C:\workspace\study\kindshot\.uv-cache'; uv run --python 3.11 --extra dev pytest -q tests/test_decision.py` passed (`15 passed`)
- `$env:UV_CACHE_DIR='C:\workspace\study\kindshot\.uv-cache'; uv run --python 3.11 --extra dev pytest -q` passed (`167 passed, 3 skipped`)

Risk and rollback note:
- Risk is low to moderate because this run only changes the LLM prompt/cache context surface, but it can shift BUY/SKIP behavior by making the model react to participation and liquidity data that was previously guardrail-only.
- Roll back by reverting `src/kindshot/decision.py`, `tests/test_decision.py`, and the run-state files if you want to return to the pre-refinement prompt surface.
