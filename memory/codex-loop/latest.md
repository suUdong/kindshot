Hypothesis: The current server-side "NVIDIA LLM" path is not failing because it buys too often; it is too conservative on the structured LLM path, while the largest missed upside is concentrated in POS_WEAK research-note / outlook headlines and the 2026-03-26 runtime was partially degraded into rule fallback / preflight decisions.

Changed files:
- `docs/nvidia-perf.md`
- `memory/codex-loop/latest.md`

Validation:
- Snapshotted `kindshot-server:/opt/kindshot/logs/kindshot_*.jsonl` at `2026-03-26 15:53:39 KST`
- Recomputed structured `decision`-row source split for `2026-03-18` through `2026-03-26`
- Recomputed inline `event`-row BUY/SKIP counts and guardrail blockers for the same window
- Recomputed downstream return checks using `close`, then `t+30m`, then `t+15m` fallback horizons
- Verified the logging caveat in code: `src/kindshot/llm_client.py` routes by `LLM_PROVIDER`, but `src/kindshot/decision.py` writes `DecisionRecord.llm_model` from `config.llm_model`

Risk and rollback note:
- This slice is documentation-only and does not change runtime behavior.
- The report cannot prove exact upstream NVIDIA model usage per decision from structured logs alone because `DecisionRecord.llm_model` is not authoritative for provider routing.
- `2026-03-26` is a degraded mixed-source day (`LLM` + `RULE_FALLBACK` + `RULE_PREFLIGHT`), so it should not be used as a clean benchmark baseline.
- Roll back by reverting `docs/nvidia-perf.md` and restoring the previous `memory/codex-loop/latest.md`.
