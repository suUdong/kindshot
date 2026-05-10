# Disable `NEWS_WEAK` / `POS_WEAK`

## Problem

Kindshot still allows `POS_WEAK` headlines to travel through the paper BUY pipeline even though the codebase already documents that this surface underperformed badly enough to justify a dedicated penalty. Leaving the bucket active keeps weak-positive headlines consuming LLM, quant, and guardrail budget while preserving a path for low-quality entries to slip through.

## Hypothesis

If `POS_WEAK` remains observable but is disabled as an entry surface in the paper pipeline and UNKNOWN auto-promotion path, then Kindshot reduces a known low-quality BUY lane without changing stronger `POS_STRONG` behavior or breaking downstream review tooling.

## Scope

- Add an explicit runtime config gate for weak-positive entries.
- Short-circuit `POS_WEAK` inside the main pipeline before expensive decision work.
- Block UNKNOWN auto-promotion into `POS_WEAK` when the gate is off.
- Align strategy-performance reporting/tests with the disabled lane.

## Non-goals

- Rewriting bucket classification keywords.
- Removing `POS_WEAK` from historical logs or research docs.
- Changing `NEG_*`, `POS_STRONG`, or deploy/runtime infrastructure outside this lane.

## Design

### Runtime policy

- Introduce `NEWS_WEAK_ENABLED` config with a default of `false`.
- Keep bucket classification unchanged so operators can still see weak-positive headlines in logs and review flows.
- In `execute_bucket_path()`, if the classified bucket is `POS_WEAK` and the gate is disabled:
  - mark the event as skipped at `SkipStage.BUCKET`
  - record a stable skip reason such as `NEWS_WEAK_DISABLED`
  - avoid quant, LLM, guardrail, and price-tracking work for that event

### UNKNOWN promotion policy

- When the gate is disabled, UNKNOWN review promotion must not produce a promoted `POS_WEAK` event.
- Reject those promotions with a stable gate reason rather than letting them reach the disabled pipeline path.

### Analysis / operator surfaces

- Keep historical trade classification logic intact where it is describing past trades.
- Update the strategy-performance label so weak-positive historical rows are explicitly shown as disabled policy rather than a currently active strategy lane.

## Logging / Observability

- Disabled weak-positive events remain visible through event logs with `bucket=POS_WEAK` and `skip_reason=NEWS_WEAK_DISABLED`.
- UNKNOWN promotion logs should expose the same policy via a promotion gate reason.

## Rollout

1. Write design + PRD + test spec.
2. Add config gate and pipeline short-circuit.
3. Block UNKNOWN promotion into `POS_WEAK` when disabled.
4. Align report/test expectations.
5. Run compile, targeted pytest, diagnostics.

## Rollback

- Revert the config gate and pipeline/promotion/report changes from this slice.
- Because this change is policy-only, rollback is code-only and does not require deploy, schema, or secret changes.
