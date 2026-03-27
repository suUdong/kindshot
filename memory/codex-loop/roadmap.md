# Kindshot Codex Roadmap

## Objective

- Improve risk-adjusted returns for paper trading.
- Prefer robustness, drawdown control, and data correctness over feature count.

## Operating Rules

- Execute one hypothesis per run.
- Keep changes small, reversible, and validated.
- Default to branch-based work and PR review before merging.
- Keep production and deployment behavior unchanged unless explicitly requested.
- Treat KIS official examples as the source of truth when parameter behavior is uncertain.

## Current Focus

- Track: User-Directed LLM Prompt Optimization
- Phase: Decision Prompt Calibration
- Status: In Progress (user override)
- Reason: The user explicitly redirected the loop after the v70 deploy to measure current LLM decision accuracy, improve prompt confidence semantics, and cut unnecessary LLM calls. The latest slice added offline prompt-eval tooling, tightened short-hold contract confidence guidance, and moved `FAST_PROFILE_LATE_ENTRY` ahead of the LLM call, pushed `425c07d`, and deployed it to `kindshot-server`.

## Phases

### Phase 0: Loop Discipline

- Goal: Make Codex runs choose work from a durable roadmap instead of ad hoc local context.
- Status: Complete
- Exit criteria:
  - `memory/codex-loop/roadmap.md` stays current.
  - `memory/codex-loop/session.md` records branch, blocker, and next intended step.
  - File roles for `roadmap.md`, `latest.md`, and `session.md` are documented.
  - The self-improve prompt reads roadmap state before proposing a hypothesis.
  - Each run updates both `latest.md` and roadmap status when priorities change.

### Phase 1: KIS Domain Layer

- Goal: Replace ad hoc KIS response dict handling with typed endpoint wrappers and shared request policy.
- Status: Complete
- Candidate outcomes:
  - Centralized pagination and `tr_cont` handling.
  - Shared token, rate-limit, retry, and error-shaping helpers.
  - Normalized models for quote, news, and market responses.

### Phase 2: KIS Feed Integrity

- Goal: Improve disclosure polling correctness and replay safety.
- Status: Complete
- Candidate outcomes:
  - Deterministic pagination across pages.
  - Clear ordering and duplicate suppression rules.
  - Better persistence of polling state across restart boundaries.

### Phase 3: Market And Quote Enrichment

- Goal: Increase signal quality in quant and guardrail inputs.
- Status: Complete
- Candidate outcomes:
  - Explicit abnormal quote-state gates from `inquire-price`.
  - Richer quote snapshot fields.
  - Better orderbook-derived liquidity measures.
  - Stronger market halt and risk context.

### Phase 4: Pipeline Normalization

- Goal: Make downstream components consume normalized KIS contracts instead of raw API payloads.
- Status: Complete
- Candidate outcomes:
  - Context card uses normalized market data.
  - Feed and market paths share the same error semantics.
  - Reduced dict-key branching in pipeline code.

### Phase 5: Observability And Regression Defense

- Goal: Make automated runs easier to trust and review.
- Status: Complete
- Candidate outcomes:
  - Structured KIS error metrics in logs.
  - Stronger fixture-based tests for KIS edge cases.
  - Clearer rollback notes and roadmap progress at the end of each run.

### Phase 6: Historical Collection Foundation

- Goal: Build a restart-safe `collect backfill` path that can persist historical news and daily market data without colliding with same-day runtime ingest.
- Status: In Progress
- Candidate outcomes:
  - `kindshot collect backfill` CLI entrypoint.
  - Finalized-day calculation and collector cursor state.
  - Historical KIS news fetch by date.
  - Daily price/index persistence for collected dates.
  - Replay-ready storage layout for subsequent analysis.

## Next Run Candidates

1. Restore prompt-replay provider credits and rerun `scripts/llm_prompt_eval.py --prompt ...` to get a true baseline-vs-variant replay comparison on the balanced historical sample.
2. Observe the next live paper session to confirm `FAST_PROFILE_LATE_ENTRY` blocks now happen before the LLM path and reduce runtime `llm_calls` without changing healthy startup behavior.
3. Use the next replay artifact to choose one narrow false-negative hypothesis for prompt tuning instead of broad BUY threshold relaxation.

## Deferred

- Live execution wiring
- `deploy/` changes
- Secrets or credential handling changes
