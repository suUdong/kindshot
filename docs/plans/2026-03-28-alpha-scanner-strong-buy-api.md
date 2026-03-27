# Alpha Scanner STRONG_BUY API Integration

## Objective
Allow `kindshot` to consume `alpha-scanner`'s current `STRONG_BUY` view as external conviction context without changing execution policy directly.

## Scope
- add config-backed alpha-scanner API settings
- fetch a per-ticker `STRONG_BUY` payload during context-card construction
- preserve the payload in runtime context artifacts
- expose the signal in the LLM prompt via `ctx_signal`

## Contract
- Endpoint: `GET /kindshot/signals/current?ticker=<ticker>`
- Success response:
  - `status`
  - `ticker`
  - `has_signal`
  - `signal_type`
  - `score_current`
  - `confidence`
  - `size_hint`
  - `score_delta`
  - `regime`
  - `reason`
  - `created_at`
  - `age_hours`
- `has_signal=true` only when the latest signal exists, is `STRONG_BUY`, and is inside the freshness window.

## Design choices
- Keep alpha-scanner side lightweight with stdlib HTTP serving to avoid new dependencies.
- Consume the signal in `context_card.py` because that is the existing per-ticker enrichment boundary.
- Pass the result into `decision.py` prompt context rather than altering deterministic trading rules in the same slice.
- Treat fetch failures as soft failures: missing external alpha data must not break kindshot processing.

## Validation
- alpha-scanner tests for payload/HTTP behavior
- kindshot tests for config loading, context enrichment, and prompt inclusion

## Rollback
- Remove alpha-scanner API config usage from kindshot and stop the alpha-scanner API server path.
