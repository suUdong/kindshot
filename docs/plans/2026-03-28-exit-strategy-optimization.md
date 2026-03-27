# 2026-03-28 Kindshot Exit Strategy Optimization

## Goal

Upgrade the current trailing-stop plus time-based liquidation path so the paper runtime can exit faster when trade quality deteriorates after entry, while preserving small reversible diffs and existing operator flows.

## Hypothesis

If Kindshot treats same-ticker bad-news events as immediate liquidation triggers, adds a deterministic support-breach liquidation rule, and changes target handling to realize 50% at the target while trailing the remainder, then paper trading can reduce drawdown and keep more upside without widening risk exposure.

## Current State

- `SnapshotScheduler` already handles stop-loss, trailing stop, t+5m loser cuts, max-hold, and close exits.
- `SnapshotScheduler` also has partial take-profit plumbing, but the current behavior is not the requested one:
  - partial exits can happen before the actual TP target
  - once the actual target is reached, full take-profit wins before the partial branch
- `pipeline.py` processes `NEG_STRONG` events as non-buy candidates, but it does not use them to close an already-open same-ticker position.
- `context_card.py` exposes technical indicators but does not currently compute an explicit support reference that the exit engine can enforce.
- `main.py`, `performance.py`, and `telegram_ops.py` already support partial/final close bookkeeping, so the change should mostly reuse the current event shape rather than invent new bookkeeping flows.

## Design

### 1. News-based immediate liquidation

- Add an explicit scheduler entrypoint that can request liquidation for an open ticker by exit reason.
- Call that entrypoint from `pipeline.py` when a same-ticker negative catalyst is detected while the position is still open.
- Initial safety scope:
  - trigger on `NEG_STRONG`
  - keep the reason additive and explicit in logs/Telegram/performance (`news_exit`)
  - do not open a new decision path or modify buy-side semantics

### 2. Technical support-breach liquidation

- Extend context-card historical features with deterministic support references that can be computed from existing pykrx data without new dependencies.
- Use a conservative composite support level for exit enforcement:
  - near-term support from the most recent completed 5-session low
  - medium-term floor from the older portion of the completed 20-session low window
  - choose the stronger of the available levels so noise does not force early exits
- Store the support reference per buy event at schedule time and let `SnapshotScheduler` exit if a later price snapshot breaches it by a configurable margin.
- Emit an explicit exit reason (`support_breach`) and include the reference/support price in logs when triggered.

### 3. Partial take-profit semantics correction

- Remove the old "partial before full target" semantics from the runtime exit engine.
- Requested behavior:
  - by default, when the TP target is reached, close exactly 50% of the remaining position
  - keep the remaining 50% open
  - move the remainder under tighter trailing logic
- Keep `PARTIAL_TAKE_PROFIT_TARGET_RATIO` as an explicit operator knob, but default it to `1.0` so the shipped behavior matches the requested target-hit partial.
- Preserve the full-take-profit path only when partial take profit is disabled or the remaining position is already too small to split.

## Logging / Observability

- Log news-triggered liquidation requests and their fulfillment.
- Log support references when a buy is scheduled and when a support-breach exit fires.
- Preserve existing partial/final trade-close payload shape so Telegram and performance tracking continue to work.

## Validation

1. `python3 -m compileall src scripts tests`
2. targeted pytest for `price`, `pipeline`, `context_card`, `performance`, `telegram_ops`
3. full `pytest -q`
4. affected-file diagnostics
5. commit with Lore trailers
6. push `main`
7. deploy to `kindshot-server`
8. remote compile/install/restart/health checks

## Rollback

- Revert the exit-strategy optimization commit.
- Re-sync the prior tree to `/opt/kindshot`.
- Reinstall into the remote venv and restart the existing services.
- No `deploy/`, secret, or live-order changes are included, so rollback remains narrow.
