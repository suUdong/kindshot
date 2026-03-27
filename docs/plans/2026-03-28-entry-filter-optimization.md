# 2026-03-28 Kindshot Entry Filter Optimization

## Goal

Harden Kindshot's BUY entry quality with one reversible slice that blocks stale entries, rejects weak orderbook imbalance setups, and skips thin-liquidity names earlier.

## Why This Slice

- The user explicitly redirected the loop from exit work toward entry optimization.
- Existing code already exposes the needed signals at decision time, so this can stay as a small guardrail-layer diff.
- Historical paper evidence is limited but sufficient to justify an effective late-entry cutoff and a conservative prior-volume gate.

## Current State

- `pipeline.py` already computes raw `delay_ms` from `disclosed_at` versus `detected_at`.
- `build_context_card()` already fetches realtime orderbook totals, bid/ask ratio, and participation data.
- `check_guardrails()` already uses:
  - spread
  - ADV
  - top-of-book liquidity
  - intraday participation
  - confidence/time/session constraints
  - raw stale-entry cutoff
  - total bid/ask imbalance
- Missing pieces for this request:
  - delay is still anchored to raw disclosure time instead of effective market delay
  - there is no hard regular-session prior-volume gate
  - there is no reusable local analysis command for this slice

## Evidence Used

- `scripts/backtest_analysis.py` on local history reconstructs `14` BUY trades.
- Delay cohort snapshot using effective delay:
  - `<=60s`: `12` trades, avg `-0.073%`, win rate `33.3%`
  - `>60s`: `2` trades, avg `-0.602%`, win rate `0.0%`
- Effective delay should be measured from `max(disclosed_at, 09:00 KST)` so pre-open disclosures are not treated as stale before the market can react.
- Prior-volume coverage is thin, but the only regular-session trade with `prior_volume_rate < 70` was negative and the `0.0` samples were all pre-open.
- Orderbook ratio evidence is not yet complete in historical runtime artifacts, so the imbalance filter stays conservative and adds observability for future recalibration.

## Design

### 1. Effective late-entry hard stop

- Re-anchor delay to `effective_delay_ms = entry_time - max(disclosed_at, 09:00 KST)`.
- Pass `effective_delay_ms` into `check_guardrails()`.
- Block BUY when `effective_delay_ms >= max_entry_delay_ms`.
- Keep the existing confidence deduction logic, but apply it to effective delay rather than raw delay.
- Emit explicit guardrail reason: `ENTRY_DELAY_TOO_LATE`.

### 2. Orderbook imbalance filter

- Reuse `total_bid_size / total_ask_size` from the existing KIS orderbook snapshot.
- Keep surfacing that ratio in the normalized context payload for runtime analysis.
- Block BUY when the ratio is below `orderbook_bid_ask_ratio_min`.
- Emit explicit guardrail reason: `ORDERBOOK_IMBALANCE`.

### 3. Prior-volume liquidity gate

- Keep the current `intraday_value_vs_adv20d` guardrail unchanged.
- Add a second, explicit liquidity gate using `prior_volume_rate`.
- Only enable the new gate from `10:00 KST` onward so pre-open and the opening ramp are not falsely blocked.
- Block BUY when `prior_volume_rate < 70`.
- Emit explicit guardrail reason: `PRIOR_VOLUME_TOO_THIN`.

## Analysis / Observability

- Add `scripts/entry_filter_analysis.py` to summarize:
  - delay buckets
  - prior-volume coverage
  - orderbook-ratio coverage and cohorts when available
- Persist the output under `logs/daily_analysis/`.

## Validation

1. compile
2. targeted tests for `guardrails`, `context_card`, `pipeline`, and `entry_filter_analysis`
3. full test suite
4. changed-file diagnostics
5. local analysis command
6. commit with Lore trailers
7. push `main`
8. deploy and remote health checks

## Rollback

- Revert the entry-filter optimization commit.
- Re-sync the prior tree to `/opt/kindshot`.
- Reinstall into the remote venv and restart services.
