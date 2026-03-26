Hypothesis: The latest verifiable paper-trading window is being dragged down by weak `수주` and article-style / mega-cap contract flow, while the apparent `공급계약` stability is mostly a single-winner illusion.

Changed files:
- `docs/2026-03-26-fire-performance-analysis.md`
- `memory/codex-loop/latest.md`

Validation:
- Recomputed BUY coverage, realized returns, bucket breakdowns, and excluded rows directly from `logs/kindshot_20260310.jsonl` through `logs/kindshot_20260319.jsonl`
- Verified synthetic runtime artifacts from `data/runtime/context_cards/20260322-20260326.jsonl` and `data/runtime/price_snapshots/20260322-20260326.jsonl` were excluded because they are test-fixture pollution
- Full tests: `source .venv/bin/activate && python -m pytest -q` passed (`614 passed, 1 warning`)

Risk and rollback note:
- This slice is documentation-only and does not change runtime behavior.
- The main analytical gap remains incomplete `close` snapshots for `7` of `23` BUY decisions, concentrated on `2026-03-18`.
- One full-suite run hit a transient timeout in `tests/test_pipeline.py::test_pipeline_passes_quote_risk_state_to_guardrails` before the targeted rerun and the subsequent full rerun both passed, so pipeline-test flakiness remains a minor validation risk.
- Roll back by reverting the new analysis document and restoring the previous `memory/codex-loop/latest.md`.
