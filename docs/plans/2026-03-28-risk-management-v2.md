# 2026-03-28 Kindshot Risk Management v2

## Goal

Extend the existing portfolio guardrails so the deployed paper runtime reacts faster to degrading trade quality and actually enforces sector concentration in live state bookkeeping.

This slice covers three linked risk controls:

1. recent win-rate based daily loss-floor tightening
2. consecutive-loss auto halt
3. same-sector simultaneous position limits with runtime-accurate state updates
4. sane simultaneous-position cap governance from repository-owned config

## Current State

- `guardrails.py` already computes an effective daily loss floor, but it only reacts to realized PnL profit lock and stop-loss streak thresholds.
- `check_guardrails()` already contains `CONSECUTIVE_STOP_LOSS` and `SECTOR_CONCENTRATION` branches.
- Runtime bookkeeping is incomplete:
  - `pipeline.py` records buys without sector metadata
  - `main.py` records sells without sector metadata
  - `context_card.py` exposes a `sector` field but does not populate it
- Result: the branch logic exists, but sector concentration is not reliably enforced in production state.
- `Config.max_positions` exists, but repository `.env` currently sets `MAX_POSITIONS=9999`, which effectively disables the simultaneous-position cap despite the intended risk control.

## Hypothesis

If the guardrail state persists recent closed-trade outcomes and open-position sector mappings, and `max_positions` is sourced from a repository-governed risk config with a sane default cap, then the runtime can tighten its daily loss floor when the same-day recent win rate deteriorates, halt after repeated losses, and block both over-concentrated sector exposure and excessive simultaneous positions without relying on an effectively-unlimited `.env` override.

## Design

### 1. Recent win-rate tightening

- Extend `GuardrailState` with a bounded recent closed-trade outcome history for the current KST day.
- Use that history inside `resolve_daily_loss_budget()` to derive a win-rate multiplier.
- Apply the most conservative multiplier across:
  - base loss limit
  - loss-streak multiplier
  - recent win-rate multiplier
- Important safety rule:
  - recent win-rate logic only tightens or leaves the base limit unchanged
  - it never expands the configured max daily loss above the base limit
- Persist these fields in `guardrail_state.json` so midday restarts preserve the same effective budget.

Suggested defaults:
- lookback: 4 fully closed trades
- activation minimum: 3 closed trades
- tighten when recent win rate < 50%
- stronger tighten when recent win rate == 0%

### 2. Consecutive-loss halt

- Keep the existing BUY-side `CONSECUTIVE_STOP_LOSS` block.
- Make the state more observable by exposing the configured halt threshold and current recent closed-trade stats through health snapshots.
- Preserve current semantics:
  - increment on final losing close
  - reset on profitable final close
  - reset on new KST day

### 3. Sector concentration enforcement

- Populate `ContextCardData.sector` during context-card construction.
- Pass sector into `guardrail_state.record_buy(...)` from `pipeline.py`.
- Persist a ticker-to-sector mapping for open positions in `GuardrailState`.
- On final close, let `record_sell(ticker)` recover the stored sector even if the callback does not provide it explicitly.
- This keeps sector counts correct across:
  - partial exits
  - full exits
  - same-day restarts

### 4. Max position cap governance

- Introduce a repository-owned risk default for `max_positions` with a target paper-trading cap of `4`.
- Treat the checked-in risk config as the source of truth for this guardrail.
- Preserve environment overrides only when they remain inside the allowed simultaneous-position range.
- Ignore legacy or obviously unsafe values such as `9999` so an old `.env` cannot silently disable the guardrail.
- Expose the resolved `max_positions` value through health output so deployment checks can verify the active cap remotely.

Rollout / safety notes:
- No `.env` mutation is required for this slice.
- Existing BUY block reason stays `MAX_POSITIONS`; only the configured threshold source changes.
- This keeps the deployment reversible because rollback only requires redeploying the prior code path.

## Logging / Observability

- Include recent-win-rate stats in the daily loss budget snapshot.
- Expose through `/health`:
  - effective daily loss floor / remaining budget
  - recent closed-trade count
  - recent win rate
  - sector positions
  - configured max positions
  - configured consecutive-loss halt threshold
- Keep existing guardrail block reason reporting unchanged so dashboards and alerts remain compatible.

## Validation

1. `python3 -m compileall src scripts tests dashboard`
2. targeted pytest for guardrails, pipeline, health, config
3. full `pytest -q`
4. affected-file diagnostics
5. commit with Lore trailers
6. push `main`
7. deploy to `kindshot-server`
8. remote compile/install/restart/health checks

## Rollback

- Revert the risk-management-v2 commit.
- Re-sync the prior tree to `/opt/kindshot`.
- Reinstall into the remote venv and restart `kindshot` plus `kindshot-dashboard`.
- No `deploy/`, secret, or live-order changes are included in this slice.
