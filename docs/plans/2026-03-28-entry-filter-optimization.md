# 2026-03-28 Kindshot Entry Filter Optimization

## Goal

Harden Kindshot's BUY entry quality with one reversible slice that blocks stale entries, rejects weak orderbook imbalance setups, and skips thin-liquidity names earlier.

## Why This Slice

- The user explicitly redirected the loop from exit work toward entry optimization.
- Existing code already exposes the needed signals at decision time, so this can stay as a small guardrail-layer diff.
- Historical paper evidence is limited but sufficient to justify a late-entry cutoff and a stronger liquidity threshold.

## Current State

- `pipeline.py` already computes `delay_ms` from `disclosed_at` versus `detected_at`.
- `build_context_card()` already fetches realtime orderbook totals and intraday participation data.
- `check_guardrails()` already uses:
  - spread
  - ADV
  - top-of-book liquidity
  - intraday participation
  - confidence/time/session constraints
- Missing pieces for this request:
  - no hard max-delay guardrail
  - no total bid/ask depth imbalance guardrail
  - liquidity threshold remains too permissive for the current evidence window

## Evidence Used

- `scripts/backtest_analysis.py` on local history reconstructs `14` BUY trades.
- Delay cohort snapshot:
  - `<=60s`: `12` trades, avg `-0.073%`, win rate `33.3%`
  - `>60s`: `2` trades, avg `-0.602%`, win rate `0.0%`
- Liquidity cohort snapshot:
  - `intraday_value_vs_adv20d >= 0.15`: `5` trades, avg `+0.071%`, win rate `60.0%`
  - `<0.15`: `9` trades, avg `-0.271%`, win rate `11.1%`
- Orderbook ratio evidence is not yet complete in historical runtime artifacts, so the imbalance filter starts conservative and adds observability for future recalibration.

## Design

### 1. Late-entry hard stop

- Pass `delay_ms` into `check_guardrails()`.
- Block BUY when `delay_ms > entry_delay_buy_limit_seconds * 1000`.
- Keep the existing confidence deduction logic; the new rule is a hard stop for materially stale entries.
- Emit explicit guardrail reason: `ENTRY_DELAY_TOO_LATE`.

### 2. Orderbook imbalance filter

- Compute `total_bid_size / total_ask_size` from the existing KIS orderbook snapshot.
- Surface that ratio in the normalized context payload for runtime analysis.
- Block BUY when the ratio is below `orderbook_bid_ask_ratio_min`.
- Emit explicit guardrail reason: `ORDERBOOK_IMBALANCE`.

### 3. Liquidity participation tightening

- Reuse the current `intraday_value_vs_adv20d` guardrail rather than introducing a second overlapping liquidity system.
- Raise the effective threshold to `0.15` by default.
- Keep the existing time-of-day relaxation logic so pre-open / early-open entries are not over-blocked.

## Analysis / Observability

- Add `scripts/entry_filter_analysis.py` to summarize:
  - delay buckets
  - liquidity participation buckets
  - orderbook-ratio coverage and cohorts when available
- Persist the output under `logs/daily_analysis/`.

## Validation

1. compile
2. targeted tests for `guardrails`, `context_card`, `pipeline`
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
