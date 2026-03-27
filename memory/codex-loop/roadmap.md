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

- Track: User-Directed Strategy Validation And Reporting
- Phase: Monthly Full-Strategy Backtest
- Status: Complete
- Reason: The user explicitly requested a full local backtest/report pass with `v70` plus later entry/exit/LLM/risk work reflected as far as local evidence allows. The latest slice added a unified monthly report command, fixed embedded snapshot backfill, generated `logs/daily_analysis/monthly_full_strategy_backtest_20260328.{json,txt}`, and produced a current-strategy estimate plus `v64`~`v70` comparison without changing runtime deployment surfaces.

## Phases

### Phase 0: Loop Discipline

- Goal: Make Codex runs choose work from a durable roadmap instead of ad hoc local context.
- Status: Complete

### Phase 1: KIS Domain Layer

- Goal: Replace ad hoc KIS response dict handling with typed endpoint wrappers and shared request policy.
- Status: Complete

### Phase 2: KIS Feed Integrity

- Goal: Improve disclosure polling correctness and replay safety.
- Status: Complete

### Phase 3: Market And Quote Enrichment

- Goal: Increase signal quality in quant and guardrail inputs.
- Status: Complete

### Phase 4: Pipeline Normalization

- Goal: Make downstream components consume normalized KIS contracts instead of raw API payloads.
- Status: Complete

### Phase 5: Observability And Regression Defense

- Goal: Make automated runs easier to trust and review.
- Status: Complete

### Phase 6: Historical Collection Foundation

- Goal: Build a restart-safe `collect backfill` path that can persist historical news and daily market data without colliding with same-day runtime ingest.
- Status: In Progress

## Next Run Candidates

1. Restore a working LLM replay path or a funded local provider so prompt-path changes can be measured directly instead of through historical BUY proxies.
2. Choose one bounded follow-up hypothesis from the fresh monthly report. The leading current blockers were `ADV_TOO_LOW`, low-confidence gates, and thin intraday participation.
3. If runtime-side validation matters next, wait for a live Korean market session and confirm whether the current entry/exit guard stack behaves in production paper trading as the local report suggests.

## Deferred

- Live execution wiring
- `deploy/` changes
- Secrets or credential handling changes
