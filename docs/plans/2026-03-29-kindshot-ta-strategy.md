# 2026-03-29 Kindshot TA Strategy

## Goal

Add a first bounded technical-analysis strategy to Kindshot's multi-strategy framework without disturbing the current news-driven runtime path.

## Hypothesis

If Kindshot adds a polling `TechnicalStrategy` that reuses existing technical and multi-timeframe enrichment and persists emitted `TradeSignal` rows through a runtime consumer, then the `Strategy` protocol becomes a real execution surface for TA research while the current news pipeline remains intact.

## Evidence

- `strategy.py` already defines the framework primitives but only `NewsStrategy` exists.
- `context_card.py` already computes the TA inputs needed for a conservative momentum strategy.
- `mtf_analysis.py` already computes alignment across 5m/15m/1h candles.
- `main.py` registers strategies but only runs the legacy news pipeline, so a non-news strategy currently has nowhere to send its signals.

## Scope

- Add one polling TA strategy.
- Add runtime signal consumption/logging for framework-emitted signals.
- Add config, tests, and design docs.
- Keep the feature disabled by default.

## Design

### 1. Strategy rule

The initial TA rule is intentionally conservative and deterministic:

- ticker must be in an explicit configured universe
- MTF alignment must meet a minimum score
- RSI must be in a bounded momentum band rather than oversold/overbought extremes
- MACD histogram must be positive
- Bollinger position must remain below an overheat cap
- intraday return must be non-negative
- volume ratio must clear a minimum liquidity floor

This keeps the first TA slice narrow and easy to reason about.

### 2. Runtime behavior

- `TechnicalStrategy` polls on a fixed interval.
- It emits `TradeSignal` objects through `stream_signals()`.
- A runtime consumer persists those signals as JSONL rows and records the day’s log path in the runtime artifact index.
- The existing news `run_pipeline()` remains untouched.

### 3. Safety / rollout

- TA strategy is disabled by default.
- No deploy behavior, secret handling, or live-order path changes.
- Size hint stays conservative.
- Repeated emissions are suppressed with a per-ticker cooldown.

## Validation

1. targeted framework/config/runtime tests
2. `python3 -m compileall src tests`
3. affected-file diagnostics

## Rollback

- Revert the TA strategy files, config surface, runtime signal consumer, and related tests/docs.
- No environment or deployment rollback is required.
