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

- Track: Post-roadmap return refinement
- Phase: Complete
- Status: Complete
- Reason: The roadmap hardening phases are now covered by typed KIS contracts, restart-safe feed handling, market/quote guardrails, normalized context-card plumbing, and structured KIS observability; next work can focus on return-oriented refinements on top of that foundation.

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

## Next Run Candidates

1. Feed normalized participation/liquidity context into the LLM prompt or cache policy only where it can improve BUY/SKIP precision without loosening guardrails.
2. Use replay/log evidence to tune newly added market-breadth and participation thresholds toward better risk-adjusted paper returns.
3. Add a bounded post-decision sizing refinement that reacts to normalized liquidity quality instead of fixed size hints alone.

## Deferred

- Live execution wiring
- `deploy/` changes
- Secrets or credential handling changes
