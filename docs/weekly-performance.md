# Weekly Performance

Date: 2026-03-26

## Scope

- Requested window: recent 7 days
- Local evidence window actually available in this workspace: `20260311`, `20260312`, `20260313`, `20260316`, `20260317`, `20260318`, `20260319`
- Primary reconstruction surface: `deploy/daily_report.py`
- Effective return rule: use the synthetic exit selected by `classify_buy_exit()`; if no exit is selected, fall back to `close`

## Coverage

- Total BUY decisions found in the 7 logged days: `23`
- BUY decisions with reconstructable realized returns from current `daily_report` inputs: `16`
- Excluded from return tables because the current workspace lacks enough exit/close snapshots: `7`

Excluded BUY rows:

- `20260312` `209640` 와이제이링크, `41억 규모 SMT Full Line 공급계약`
- `20260318` `034020` 두산에너빌리티, `북미서 대형 복합발전 스팀터빈 수주`
- `20260318` `006400` 삼성SDI, `스텔란티스 美 합작법인 협의`
- `20260318` `005930` 삼성전자, `상법 개정 후 첫 주총 시즌… 정관 변경·자사주 소각 대응`
- `20260318` `009150` 삼성전기, `시장 성장률 웃도는 매출 확대 추진`
- `20260318` `018260` 삼성SDS, `자사주 매입·소각 추진`
- `20260319` `000660` SK하이닉스, `마이크론 최대 매출… ‘32만전자’ 보인다`

## Executive Summary

- Reconstructed realized trades: `16`
- Wins: `5`
- Win rate: `31.2%`
- Average realized return: `-0.009%`
- Sum realized return: `-0.141%`
- Approximate PnL at `5,000,000 KRW` order size: about `-7,049 KRW`

Interpretation:

- The 7-day window is effectively flat to slightly negative.
- Performance is concentrated in two `take_profit` trades; without them the window is clearly red.
- The heaviest trade-producing keyword bucket was `공급계약`, but it still finished negative on realized-return sum.

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

The best realized day was `20260312`; the worst was `20260317`.

## Bucket Returns

### Runtime bucket

All reconstructable realized BUYs in this window came from the runtime `POS_STRONG` bucket. There were no reconstructable realized BUYs from `POS_WEAK`.

| Runtime bucket | Trades | Wins | Win rate | Avg return | Sum return |
|---|---:|---:|---:|---:|---:|
| `POS_STRONG` | 16 | 5 | `31.2%` | `-0.009%` | `-0.141%` |

### Most profitable keyword buckets Top 5

Because the runtime BUY set collapsed to a single runtime bucket, the Top 5 view uses the finer `keyword_hits` buckets already attached to the same `deploy/daily_report.py` event records.

Ranked by cumulative realized return:

| Rank | Keyword bucket | Trades | Wins | Avg return | Sum return |
|---|---|---:|---:|---:|---:|
| 1 | `특허` | 1 | 1 | `+3.678%` | `+3.678%` |
| 2 | `자사주 소각` | 1 | 0 | `-0.065%` | `-0.065%` |
| 3 | `공급계약` | 6 | 3 | `-0.061%` | `-0.366%` |
| 4 | `인수` | 2 | 1 | `-0.254%` | `-0.508%` |
| 5 | `합작` | 1 | 0 | `-0.841%` | `-0.841%` |

Observations:

- `특허` was the only clearly positive bucket in this window.
- `공급계약` produced the most realized trades, but its cumulative result stayed slightly negative.
- `인수` and `합작` remained net negative even after the earlier M&A hold-profile tightening work.

## Exit Bucket View

| Exit bucket | Trades | Wins | Avg return | Sum return |
|---|---:|---:|---:|---:|
| `take_profit` | 2 | 2 | `+3.187%` | `+6.374%` |
| `max_hold` | 2 | 1 | `-0.254%` | `-0.508%` |
| `open` | 7 | 2 | `-0.079%` | `-0.552%` |
| `trailing_stop` | 2 | 0 | `-0.300%` | `-0.599%` |
| `stop_loss` | 3 | 0 | `-1.618%` | `-4.855%` |

This window was held up by a very small `take_profit` cohort and dragged down mainly by `stop_loss`.

## Takeaway

- On current local evidence, the recent 7 logged days do not show a broadly healthy BUY surface.
- The runtime BUY flow is still concentrated in `POS_STRONG`, but within that surface only the `특허` bucket was clearly additive.
- `공급계약` remained the biggest volume bucket and still failed to deliver positive realized-return sum, so it remains the most likely place to look for the next bounded strategy refinement once fresher logs are available.
