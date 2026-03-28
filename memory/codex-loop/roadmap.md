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

- Track: NLP Signal Enrichment
- Phase: Post-Deployment Observation
- Status: In Progress (user override)
- Reason: The server has now been reconciled to `44783ee` and restarted cleanly, but the post-deploy monitoring window only saw duplicate polling and idle heartbeats. The next bounded step is collecting the first fresh live item that actually exercises the deployed NLP, sector, and volume paths.

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

1. Observe the next fresh live paper item and confirm that a current-day runtime log row contains `news_signal`, sector momentum, and volume fields with plausible values.
2. If polling remains duplicate-only with no current-day structured log, inspect upstream feed freshness before changing runtime strategy logic.
3. Once live semantic-enrichment coverage is confirmed, return to the next highest-leverage hypothesis with this richer signal surface available to analysis and prompt tuning.

## Deferred

- Live execution wiring
- `deploy/` changes
- Secrets or credential handling changes
