# 2026-03-28 Sector Priority Trading

## Goal

Use the completed alpha-scanner sector analysis surface inside Kindshot so rising-sector stocks are handled first and sector momentum directly influences BUY confidence.

## Current State

- Kindshot already consumes alpha-scanner `STRONG_BUY` signal context through `context_card.py`.
- Alpha-scanner now also exposes a sector snapshot shape containing prioritized stocks and sector rotation metadata.
- `pipeline_loop()` is still FIFO and therefore ignores sector leadership when several tickers are pending together.
- Confidence shaping currently uses price, volume, delay, market, volatility, category, pattern, and ticker-learning inputs, but not sector momentum.

## Hypothesis

If Kindshot reuses the alpha-scanner sector snapshot as a cached per-ticker lookup, then it can both:

1. process rising-sector candidates earlier in the queue
2. give a bounded confidence edge to candidates already aligned with strong sector rotation

This should improve paper-trading selectivity without adding a new dependency or changing order execution semantics.

## Design

### 1. Shared sector snapshot helper

- Add a small alpha-scanner helper that:
  - fetches a sector snapshot from the existing base URL
  - probes a short list of compatible endpoint paths
  - caches successful results in-process with a TTL
  - returns `None` on failure so Kindshot remains fail-open

### 2. Queue prioritization

- Switch pipeline work intake from FIFO to priority-based ordering.
- Priority order:
  - `LEADING` / `IMPROVING`
  - `NEUTRAL`
  - `WEAKENING` / `LAGGING` / unknown
- Inside the same tier, prefer higher alpha-scanner `priority_score`, then preserve arrival order.

### 3. Context enrichment and confidence shaping

- Surface per-ticker sector metadata into:
  - normalized `ContextCard`
  - raw runtime context payloads
- Apply a bounded deterministic confidence rule from sector rotation:
  - boost leading / improving sectors
  - penalize weakening / lagging sectors
  - strengthen the effect slightly for extreme momentum-score values

## Rollout / Safety Notes

- The feature remains soft-dependency based on alpha-scanner availability.
- No `.env`, secret, or `deploy/` changes are required.
- Existing guardrails remain authoritative; sector priority only affects candidate ordering and confidence shaping.

## Validation

1. compile
2. targeted pytest for guardrails/context-card/pipeline
3. full pytest
4. affected-file diagnostics
5. commit with Lore trailers
6. push
7. deploy and verify remote health
