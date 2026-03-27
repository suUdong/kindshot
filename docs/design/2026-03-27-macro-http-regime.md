# Design: Kindshot Macro Regime HTTP Integration

## Scope

Allow `kindshot` to consume macro regime from `macro-intelligence` over HTTP and include it in market context / LLM prompt construction without altering deployment scripts or live-order behavior.

## Approach

- Add config for macro API base URL and timeout.
- During `MarketMonitor.update()`, fetch `GET /regime/current` when configured.
- Extend `MarketContext` with optional macro regime fields relevant to Korean equities:
  - `macro_overall_regime`
  - `macro_overall_confidence`
  - `macro_kr_regime`
  - `macro_crypto_regime`
- Include the macro regime summary in `decision._build_prompt()` when present.

## Logging / Observability

- Runtime market-context JSONL gains macro fields automatically via `model_dump`.
- HTTP failures log warnings and do not block market updates.

## Validation

- Add market tests for successful HTTP fetch and graceful failure.
- Add prompt test to confirm macro regime text is present when available.

## Rollback

- Remove macro fields from `MarketContext`.
- Remove HTTP fetch path from `MarketMonitor`.
- Clear macro config values so runtime ignores the feature.
