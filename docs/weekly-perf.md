# Weekly Performance

Date: 2026-03-26

## Scope

- Requested window: recent 7 days
- Effective evidence window used for this report: latest 7 real logged trading days, `20260311`, `20260312`, `20260313`, `20260316`, `20260317`, `20260318`, `20260319`
- Recent runtime artifacts `20260322` to `20260326` were excluded because they are test-fixture pollution, not real operator logs:
  - `data/runtime/context_cards/*.jsonl`: every row uses `run_id=test_run` and a single synthetic `event_id=4eb4648d2e75cbce`
  - `data/runtime/price_snapshots/*.jsonl`: every row uses `run_id=run1` and a single synthetic `event_id=evt1`
- Return reconstruction rule: use the synthetic exit selected by `classify_buy_exit()` from [src/kindshot/strategy_observability.py](/home/wdsr88/workspace/kindshot/src/kindshot/strategy_observability.py); if no exit is selected, fall back to `close`

## Coverage

- Total BUY decisions in the 7-day evidence window: `23`
- BUY decisions with reconstructable realized returns from local snapshots: `16`
- BUY decisions excluded because current local logs do not contain enough exit or `close` snapshots: `7`

Excluded BUY rows:

- `20260312` `209640` 와이제이링크, `41억 규모 SMT Full Line 공급계약`
- `20260318` `034020` 두산에너빌리티, `북미서 대형 복합발전 스팀터빈 수주`
- `20260318` `006400` 삼성SDI, `스텔란티스 美 합작법인, 다양한 방안 협의 중`
- `20260318` `005930` 삼성전자, `상법 개정 후 첫 주총 시즌… 기업 '정관 변경·자사주 소각' 대응`
- `20260318` `009150` 삼성전기, `시장 성장률 웃도는 매출 확대 추진`
- `20260318` `018260` 삼성SDS, `자사주 매입·소각, 상황 맞춰 추진`
- `20260319` `000660` SK하이닉스, `마이크론 최대 매출… '32만전자' 보인다`

## Executive Summary

- Reconstructed realized trades: `16`
- Wins: `5`
- Win rate: `31.2%`
- Average realized return: `-0.009%`
- Sum realized return: `-0.141%`
- Approximate PnL at `5,000,000 KRW` per trade: about `-7,049 KRW`

Interpretation:

- The latest verifiable 7-day window is effectively flat to slightly negative.
- The return distribution is narrow and fragile: only `2` trades hit `take_profit`, and those two trades account for the whole positive edge.
- The BUY surface remains concentrated in `POS_STRONG`, but most keyword buckets inside that surface are still net negative.

## Daily Breakdown

| Date | Realized BUYs | Wins | Avg return | Sum return |
|---|---:|---:|---:|---:|
| `20260311` | 1 | 0 | `-0.698%` | `-0.698%` |
| `20260312` | 4 | 1 | `+0.767%` | `+3.069%` |
| `20260313` | 0 | 0 | `N/A` | `N/A` |
| `20260316` | 5 | 3 | `+0.388%` | `+1.939%` |
| `20260317` | 4 | 0 | `-0.950%` | `-3.799%` |
| `20260318` | 1 | 0 | `-0.841%` | `-0.841%` |
| `20260319` | 1 | 1 | `+0.189%` | `+0.189%` |

- Best day: `20260312`
- Worst day: `20260317`

## Bucketed Returns

### Runtime bucket

All reconstructable realized BUYs in this window came from the runtime `POS_STRONG` bucket.

| Runtime bucket | Trades | Wins | Win rate | Avg return | Sum return |
|---|---:|---:|---:|---:|---:|
| `POS_STRONG` | 16 | 5 | `31.2%` | `-0.009%` | `-0.141%` |

### Keyword buckets

Ranked by cumulative realized return.

| Rank | Keyword bucket | Trades | Wins | Win rate | Avg return | Sum return |
|---|---|---:|---:|---:|---:|---:|
| 1 | `특허` | 1 | 1 | `100.0%` | `+3.678%` | `+3.678%` |
| 2 | `자사주 소각` | 1 | 0 | `0.0%` | `-0.065%` | `-0.065%` |
| 3 | `공급계약` | 6 | 3 | `50.0%` | `-0.061%` | `-0.366%` |
| 4 | `인수` | 2 | 1 | `50.0%` | `-0.254%` | `-0.508%` |
| 5 | `합작` | 1 | 0 | `0.0%` | `-0.841%` | `-0.841%` |
| 6 | `공급 계약` | 1 | 0 | `0.0%` | `-0.964%` | `-0.964%` |
| 7 | `수주` | 4 | 0 | `0.0%` | `-0.269%` | `-1.074%` |

Observations:

- `특허` was the only clearly additive bucket in this window.
- `공급계약` produced the most realized trades and the best breadth, but still finished slightly negative in cumulative return.
- `수주` showed the weakest realized bucket profile in the window: `4` realized trades, `0` wins, `-1.074%` cumulative return.

### Exit buckets

| Exit bucket | Trades | Wins | Win rate | Avg return | Sum return |
|---|---:|---:|---:|---:|---:|
| `take_profit` | 2 | 2 | `100.0%` | `+3.187%` | `+6.374%` |
| `max_hold` | 2 | 1 | `50.0%` | `-0.254%` | `-0.508%` |
| `open` | 7 | 2 | `28.6%` | `-0.079%` | `-0.552%` |
| `trailing_stop` | 2 | 0 | `0.0%` | `-0.300%` | `-0.599%` |
| `stop_loss` | 3 | 0 | `0.0%` | `-1.618%` | `-4.855%` |

## Takeaway

- On current local evidence, the recent 7-day BUY surface is not broadly healthy.
- The surface is concentrated in `POS_STRONG`, but bucket-level profitability is still narrow and mostly negative.
- If the next bounded strategy hypothesis is chosen from this report alone, `수주` and `공급계약` remain the highest-signal candidates for refinement because they produced enough realized trades to matter and still failed to produce positive cumulative return.
