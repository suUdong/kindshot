# PRD: Entry Strategy Optimization

## Goal

Improve Kindshot entry quality with one bounded hypothesis that tightens paper-mode BUY admission using:

1. stale-entry timing analysis and a maximum acceptable news-to-entry delay,
2. orderbook bid/ask depth imbalance as an entry filter,
3. stronger liquidity participation confirmation before BUY.

## Why This Slice

- The previous run completed exit-strategy optimization; the next highest-leverage risk-adjusted return slice is entry quality.
- Current runtime already captures `delay_ms`, `orderbook_snapshot`, and liquidity participation metrics, so this can be implemented as a narrow guardrail upgrade instead of a new subsystem.
- Local history shows materially worse BUY outcomes once disclosure-to-entry delay becomes large enough to imply stale news.

## Hypothesis

If Kindshot blocks BUY entries that arrive too long after the news, rejects orderbooks with clear aggregate sell-side dominance, and requires stronger intraday participation before entry, then paper trading will skip late/thin entries that have poor follow-through while preserving the existing LLM, scheduler, and deployment paths.

## Scope

In scope:

- a local analysis command that summarizes entry-delay evidence and current guardrail coverage,
- one new timing guardrail based on disclosure-to-entry delay,
- one new orderbook imbalance guardrail using existing total bid/ask depth,
- one tighter liquidity participation default for BUY entries,
- tests, docs, run summary, commit/push, and deployment.

Out of scope:

- intentional delayed-entry scheduling after signal arrival,
- new data vendors or dependencies,
- `deploy/` edits,
- secret or credential handling,
- live-order enablement.

## Functional Requirements

- The pipeline must pass disclosure delay into the final BUY guardrail step.
- BUY entries must be blocked when disclosure delay exceeds the configured maximum.
- BUY entries must be blocked when aggregate bid depth is too weak versus ask depth.
- BUY entries must continue using the existing intraday participation gate, but with a tighter default floor supported by the local evidence window.
- Analysis output must capture evidence and explicitly report coverage gaps for orderbook-derived history.

## Non-Functional Requirements

- Small, reversible diff.
- No new dependencies.
- Existing operator-facing flows, logs, and price tracking remain intact.
- Remote service restart path remains the same.

## Evidence Basis

- Local paper BUY history with close reconstruction shows:
  - `20` reconstructable BUYs from `logs/kindshot_*.jsonl`
  - `delay >= 30s`: `9` trades, average close return about `-0.95%`
  - `delay >= 60s`: `5` trades, average close return about `-1.40%`
  - `delay >= 120s`: `1` trade, close return about `-2.30%`
- The same history window and the active entry-filter draft both indicate the current default floor `0.01` is too loose; this run aligns the bounded rollout target at `0.15`.
- Historical runtime artifacts do not yet provide enough real BUY outcomes with stored orderbook totals, so the orderbook-imbalance filter should be conservative and explicitly documented as low-coverage.

## Rollout

1. Add/update design docs and test spec.
2. Add a targeted entry-filter analysis script and run it on local history.
3. Implement timing and orderbook guardrails plus tighter liquidity default.
4. Add/update tests.
5. Run compile, targeted tests, full tests, and diagnostics.
6. Commit with Lore trailers, push `main`, deploy to `kindshot-server`, and verify health.

## Observability

- Keep blocked BUY reasons explicit in existing event logs.
- Record analysis output under `logs/daily_analysis/`.
- Append deploy evidence to `DEPLOYMENT_LOG.md`.

## Rollback

- Revert the entry-filter optimization commit.
- Re-deploy the previous tree to `/opt/kindshot`.
- Restart the existing services.
