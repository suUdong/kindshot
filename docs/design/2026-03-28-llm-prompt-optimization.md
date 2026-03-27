# 2026-03-28 LLM Prompt Optimization

## Objective

Optimize the live LLM decision path after v70 with one reversible slice that improves offline decision quality, confidence usefulness, and call efficiency.

## Current State

- `decision.py` builds a structured prompt and sends it through `LlmClient`.
- Existing deterministic call reduction is limited to contract-family preflight skips plus short-lived in-memory cache/dedup.
- Existing analysis (`docs/nvidia-perf.md`) shows:
  - low BUY rate,
  - weak BUY follow-through,
  - large SKIP misses concentrated in narrow cohorts,
  - confidence behaving more like a hard action separator than a calibrated score.

## Problem

The current prompt path lacks a disciplined offline comparison loop and still spends LLM calls on some low-value cases that historical evidence already marks as poor. Confidence is useful as a threshold but not expressive enough as a ranking signal.

## Design

### 1. Offline evaluation surface

Add or extend a local evaluator so it can:

- reconstruct decision rows from `logs/kindshot_*.jsonl`
- map BUY outcomes using observed forward price snapshots
- map blocked BUY opportunities using `shadow_` snapshots
- compare baseline against prompt variants using the same event set
- report source split and approximate LLM-call savings from deterministic skips

### 2. Prompt A/B variants

Evaluate at least:

- baseline prompt
- one stricter-structure variant that:
  - weights `ctx_signal` more explicitly,
  - treats article/commentary language as a distinct failure mode,
  - spreads confidence more intentionally across strong/medium/weak cases,
  - avoids defaulting to the low-80s cluster for routine contract headlines

The variant should preserve hard safety rules while reducing confidence compression.

### 3. Confidence improvement

Do not add a second opaque model layer. Improve confidence by:

- making the prompt map stronger catalysts to wider score bands,
- converting some repeatable weak cohorts into deterministic skips before the model,
- preserving post-parse safety checks for malformed or too-low BUY outputs.

### 4. Cost optimization

Add one bounded pre-LLM deterministic skip for a historically weak cohort if offline evidence supports it. The preferred target is a cohort already identified as weak in local analysis rather than a broad prompt-wide relaxation/tightening.

Candidate examples:

- research-note / 전망형 POS_WEAK salvage or skip segmentation
- large-cap commentary-like contract headlines
- late-session fast-decay headlines already unlikely to pass downstream

Final cohort choice should be evidence-led from the baseline run.

## Logging / Observability

- Keep `decision_source` authoritative.
- Keep runtime logs compatible with existing dashboards.
- Write local analysis artifacts for baseline and variant comparison.

## Validation

- targeted tests for evaluator + decision logic
- full test suite
- compileall
- changed-file diagnostics
- baseline/variant local analysis outputs
- remote deploy health checks

## Rollback

- revert the prompt/preflight/evaluator commit
- redeploy the previous known-good commit/tree
- confirm remote health recovers
