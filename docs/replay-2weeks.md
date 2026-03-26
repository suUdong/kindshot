# Replay 2 Weeks

Date: 2026-03-26

## Scope

- Requested window: `2026-03-13` to `2026-03-26` KST
- Reliable replay inputs in the window: `20260313`, `20260316`, `20260317`, `20260318`, `20260319`
- Excluded dates:
  - `20260320`, `20260321`: no local log or runtime artifacts
  - `20260322` to `20260326`: runtime artifact pollution (`run_id=test_run`, single repeated `event_id`)
- Execution path:
  - Re-ran `replay.py` via `python -m kindshot --replay logs/kindshot_YYYYMMDD.jsonl`
  - Ran the same replay pricing and guardrail path with `rule_fallback` injected for comparison

## Environment Constraint

- `NVIDIA_API_KEY` is unset in the current environment.
- The requested NVIDIA replay path therefore fell through to Anthropic.
- Anthropic replay calls failed with `400 invalid_request_error` because the account credit balance is too low.

This means there is no fresh NVIDIA-vs-fallback rerun result available from this machine today. The usable comparison is:

- actual `replay.py` rerun status in the current environment
- full-window `rule_fallback` replay result
- common-subset comparison between historical logged LLM decisions and current `rule_fallback`

## Replay Rerun Result

### Requested LLM replay path

| Strategy path | Actionable events | BUY | SKIP | LLM errors | Priced trades | Avg return | Approx PnL |
|---|---:|---:|---:|---:|---:|---:|---:|
| requested `NVIDIA` path, actual Anthropic fallback | `168` | `0` | `50` | `118` | `0` | `N/A` | `0 KRW` |

Interpretation:

- This rerun is operationally blocked, not economically informative.
- The blocker is external configuration/billing, not replay logic.

### `rule_fallback` replay on the full reliable subset

| Strategy | Actionable events | BUY | SKIP | Priced trades | Win rate | Avg return | Max drawdown | Profit factor | Approx PnL |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `rule_fallback` | `168` | `16` | `152` | `16` | `50.0%` | `-0.298%` | `-15.45%` | `0.71` | `-238,500 KRW` |

Pricing coverage:

- `4` trades from runtime `price_snapshot`
- `12` trades from `pykrx` open-to-close fallback

Daily breakdown:

| Date | BUY | Priced trades | Avg return | Approx PnL |
|---|---:|---:|---:|---:|
| `2026-03-13` | `4` | `4` | `+2.350%` | `+470,000 KRW` |
| `2026-03-16` | `10` | `10` | `-0.697%` | `-348,500 KRW` |
| `2026-03-17` | `0` | `0` | `N/A` | `0 KRW` |
| `2026-03-18` | `0` | `0` | `N/A` | `0 KRW` |
| `2026-03-19` | `2` | `2` | `-3.600%` | `-360,000 KRW` |

## Comparable Strategy Benchmark

Because fresh LLM replay is blocked, the apples-to-apples strategy comparison uses the `27` actionable events that already have historical logged LLM decisions in local logs (`2026-03-16` to `2026-03-19`).

### Historical logged LLM vs current `rule_fallback`

| Strategy | Common events | BUY | Win rate | Avg return | Max drawdown | Profit factor | Approx PnL |
|---|---:|---:|---:|---:|---:|---:|---:|
| historical logged LLM | `27` | `17` | `52.9%` | `-0.396%` | `-9.46%` | `0.57` | `-337,000 KRW` |
| current `rule_fallback` | `27` | `4` | `50.0%` | `-0.293%` | `-1.75%` | `0.33` | `-58,500 KRW` |

Interpretation:

- `rule_fallback` is much more selective on the same event set: `17 -> 4` buys.
- On this common subset it loses less money: `-337,000 KRW -> -58,500 KRW`.
- Drawdown is materially smaller for `rule_fallback`: `-9.46% -> -1.75%`.
- The trade-off is participation: `rule_fallback` effectively sits out the profitable `2026-03-18` cluster that helped the historical LLM path.

### Common-subset daily pattern

| Date | Historical LLM avg return | Historical LLM approx PnL | `rule_fallback` avg return | `rule_fallback` approx PnL |
|---|---:|---:|---:|---:|
| `2026-03-16` | `-2.126%` | `-531,500 KRW` | `-0.293%` | `-58,500 KRW` |
| `2026-03-17` | `-0.663%` | `-132,500 KRW` | `N/A` | `0 KRW` |
| `2026-03-18` | `+0.975%` | `+292,500 KRW` | `N/A` | `0 KRW` |
| `2026-03-19` | `+0.345%` | `+34,500 KRW` | `N/A` | `0 KRW` |

## Bottom Line

- The requested fresh `NVIDIA LLM vs rule_fallback` replay could not be completed on `2026-03-26` because the machine has no `NVIDIA_API_KEY`, and the Anthropic fallback account has insufficient credits.
- The usable `replay.py` result from today is the `rule_fallback` run: `16` priced trades, `-0.298%` average return, about `-238,500 KRW`.
- On the comparable historical-decision subset, `rule_fallback` is safer but narrower than the logged LLM path: smaller loss and drawdown, but far fewer trades and lower upside capture.
