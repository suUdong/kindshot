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
- Reason: The requested NLP semantic-enrichment slice is now deployed. The next bounded step is collecting the first live-session evidence that numeric extraction, related-news clustering, and impact-score metadata are present and behaving sanely under real paper-mode headlines.

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

1. Observe the next live paper session and confirm that deployed event/context logs contain `news_signal` metadata with plausible contract/revenue/op-profit/cluster/impact values.
2. If live paper decisions appear over-boosted or under-boosted, replay a recent headline batch and recalibrate the bounded impact-score adjustment before changing other guardrails.
3. Once semantic-enrichment coverage is confirmed, return to the next highest-leverage hypothesis with this richer headline surface available to analysis and prompt tuning.

## Deferred

- Live execution wiring
- `deploy/` changes
- Secrets or credential handling changes
