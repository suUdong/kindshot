# Fire Performance Analysis

Date: 2026-03-26

## Scope

- Evidence window: real paper-trading logs from `2026-03-10` to `2026-03-19`
- Source logs: `logs/kindshot_20260310.jsonl`, `logs/kindshot_20260311.jsonl`, `logs/kindshot_20260312.jsonl`, `logs/kindshot_20260313.jsonl`, `logs/kindshot_20260316.jsonl`, `logs/kindshot_20260317.jsonl`, `logs/kindshot_20260318.jsonl`, `logs/kindshot_20260319.jsonl`
- Exit reconstruction rule: `src/kindshot/strategy_observability.py` `classify_buy_exit()` output, then `close` fallback if no synthetic exit is selected
- Excluded from scope: `data/runtime/context_cards/20260322-20260326.jsonl` and `data/runtime/price_snapshots/20260322-20260326.jsonl`
  - Reason: those files are test-fixture pollution (`run_id=test_run` or `run_id=run1`, repeated synthetic `event_id`)

## Coverage

- Total BUY decisions in the window: `23`
- BUY decisions with reconstructable realized returns: `16`
- BUY decisions excluded because the local log set does not contain the required exit or `close` snapshot: `7`
- Realized-return coverage: `69.6%`

Excluded BUY rows:

- `2026-03-12` `209640` 와이제이링크, `41억 규모 SMT Full Line 공급계약`
- `2026-03-18` `034020` 두산에너빌리티, `[카드] 북미서 대형 복합발전 스팀터빈 수주`
- `2026-03-18` `006400` 삼성SDI, `스텔란티스 美 합작법인, 다양한 방안 협의 중`
- `2026-03-18` `005930` 삼성전자, `정관 변경·자사주 소각 대응`
- `2026-03-18` `009150` 삼성전기, `시장 성장률 웃도는 매출 확대 추진`
- `2026-03-18` `018260` 삼성SDS, `자사주 매입·소각 추진`
- `2026-03-19` `000660` SK하이닉스, `마이크론 최대 매출`

## Executive Summary

- Realized BUY trades: `16`
- Wins: `5`
- Win rate: `31.2%`
- Average realized return: `-0.009%`
- Cumulative realized return: `-0.141%`
- Approximate PnL at `5,000,000 KRW` per trade: about `-7,049 KRW`

Interpretation:

- The latest verifiable paper-trading window is effectively flat to slightly negative.
- The return profile is fragile rather than broad-based: the top `2` winners contributed `+6.374%`, while the other `14` realized trades summed to `-6.515%`.
- All reconstructable realized BUYs came from runtime `POS_STRONG`, so the current runtime bucket is too coarse to explain edge quality by itself.

## Daily Breakdown

| Date | Realized BUYs | Wins | Win rate | Avg return | Sum return |
|---|---:|---:|---:|---:|---:|
| `2026-03-10` | 0 | 0 | `N/A` | `N/A` | `N/A` |
| `2026-03-11` | 1 | 0 | `0.0%` | `-0.698%` | `-0.698%` |
| `2026-03-12` | 4 | 1 | `25.0%` | `+0.767%` | `+3.069%` |
| `2026-03-13` | 0 | 0 | `N/A` | `N/A` | `N/A` |
| `2026-03-16` | 5 | 3 | `60.0%` | `+0.388%` | `+1.939%` |
| `2026-03-17` | 4 | 0 | `0.0%` | `-0.950%` | `-3.799%` |
| `2026-03-18` | 1 | 0 | `0.0%` | `-0.841%` | `-0.841%` |
| `2026-03-19` | 1 | 1 | `100.0%` | `+0.189%` | `+0.189%` |

- Best day: `2026-03-12`
- Worst day: `2026-03-17`

## Bucket Returns

### Runtime bucket

All reconstructable realized BUY trades in this window came from runtime `POS_STRONG`.

| Runtime bucket | Trades | Wins | Win rate | Avg return | Sum return |
|---|---:|---:|---:|---:|---:|
| `POS_STRONG` | 16 | 5 | `31.2%` | `-0.009%` | `-0.141%` |

### Keyword bucket

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

### Exit bucket

| Exit bucket | Trades | Wins | Win rate | Avg return | Sum return |
|---|---:|---:|---:|---:|---:|
| `take_profit` | 2 | 2 | `100.0%` | `+3.187%` | `+6.374%` |
| `max_hold` | 2 | 1 | `50.0%` | `-0.254%` | `-0.508%` |
| `close_fallback` | 7 | 2 | `28.6%` | `-0.079%` | `-0.552%` |
| `trailing_stop` | 2 | 0 | `0.0%` | `-0.300%` | `-0.599%` |
| `stop_loss` | 3 | 0 | `0.0%` | `-1.618%` | `-4.855%` |

### Confidence bucket

All reconstructable realized BUY trades sat inside the same narrow confidence band.

| Confidence bucket | Trades | Wins | Win rate | Avg return | Sum return |
|---|---:|---:|---:|---:|---:|
| `70-79` | 16 | 5 | `31.2%` | `-0.009%` | `-0.141%` |

Observed realized confidence values were only `72` and `78`.

## Concentration And Failure Pattern

### Top winners

| Date | Ticker | Keyword bucket | Realized return | Headline |
|---|---|---|---:|---|
| `2026-03-12` | `196170` | `특허` | `+3.678%` | 알테오젠, 키트루다 SC 조성물 특허 미국 등록 |
| `2026-03-16` | `474610` | `공급계약` | `+2.696%` | RF시스템즈, 166.53억원 규모 공급계약 체결 |

### Top losers

| Date | Ticker | Keyword bucket | Exit bucket | Realized return | Headline |
|---|---|---|---|---:|---|
| `2026-03-17` | `178320` | `공급계약` | `stop_loss` | `-3.050%` | 서진시스템, 2702억 규모 ESS 장비 공급계약 |
| `2026-03-16` | `006400` | `공급 계약` | `stop_loss` | `-0.964%` | 삼성SDI, 美 에너지 기업과 1.5조 규모 ESS 공급 계약 체결 |
| `2026-03-18` | `373220` | `합작` | `stop_loss` | `-0.841%` | LG엔솔·GM 합작법인 생산라인 전환 |
| `2026-03-11` | `036570` | `인수` | `max_hold` | `-0.698%` | 엔씨, 유럽 모바일 캐주얼 플랫폼사 인수 |

### Contract and order cluster

If `공급계약`, `공급 계약`, `수주`를 하나의 contract-order 군으로 묶으면:

- Realized trades: `11`
- Wins: `3`
- Win rate: `27.3%`
- Average realized return: `-0.219%`
- Cumulative realized return: `-2.404%`

This cluster produced most of the actionable volume but still lost money.

### Hidden fragility inside `공급계약`

`공급계약` 자체는 겉으로 보면 `6`건 중 `3`승으로 나쁘지 않아 보이지만, 핵심 단일 승자인 `474610` RF시스템즈를 빼면 구조가 무너진다.

- `공급계약` 전체: `6` trades, `50.0%` win rate, `-0.366%` cumulative return
- `474610` 제외 후: `5` trades, `40.0%` win rate, `-3.062%` cumulative return

That means the bucket is not broadly healthy; one outsized winner is hiding a weak base cohort.

## Improvement Points

### 1. `수주`를 가장 먼저 더 세게 자를 것

Why:

- `수주` realized trades: `4`
- Wins: `0`
- Cumulative realized return: `-1.074%`
- Exit mix: `3` `close_fallback`, `1` `trailing_stop`

Implication:

- The current `수주` bucket is producing volume without verified edge.
- This is the cleanest next hypothesis for a bounded rule-tightening pass.

Recommended direction:

- Prefer a deterministic preflight or stronger quant gating for article-style and large-cap `수주` headlines before the LLM path.

### 2. `공급계약`은 하나의 버킷으로 두면 안 된다

Why:

- `공급계약` headline family mixes very different shapes:
  - mid-cap direct disclosure winners like `474610`
  - repeated losers like `178320`
  - weaker commentary-style or mega-cap contract news like `006400`

Implication:

- A single `공급계약` bucket is hiding materially different risk profiles.

Recommended direction:

- Split the bucket operationally into at least:
  - direct filing / concrete award / mid-cap cohort
  - article-style, follow-on, or mega-cap contract cohort
- The recent contract preflight hypothesis already fits this evidence.

### 3. Confidence is not ranking trade quality yet

Why:

- Every reconstructable realized BUY sat in the `70-79` band.
- The only realized confidence values were `72` and `78`.

Implication:

- Current confidence is acting like a pass/fail threshold, not a ranking signal.

Recommended direction:

- Either recalibrate the LLM confidence scale or stop treating it as a meaningful prioritization input until the spread becomes informative.

### 4. Close-snapshot completeness needs repair before the next analysis loop

Why:

- `7` of `23` BUY decisions (`30.4%`) could not be scored.
- `5` of those `7` gaps were concentrated on `2026-03-18`.

Implication:

- The strategy analysis loop is partially blind on the same days where many large-cap headline buys appeared.

Recommended direction:

- Backfill or guarantee end-of-day `close` snapshots for all BUY decisions before treating the next weekly analysis as decisive.

## Suggested Next Hypothesis

If only one trading hypothesis is chosen from this report, the highest-signal candidate is:

`수주` + weak contract/article-style preflight tightening

Reason:

- It targets the largest negative realized cohort with enough sample count to matter.
- It also aligns with the already observed `공급계약` / mega-cap loser pattern, while leaving the clearly positive `특허` and the single strong contract winner archetype untouched.

## Bottom Line

- The paper-trading surface is not broadly profitable on the latest verifiable evidence.
- Positive edge is concentrated in `특허` and one standout `공급계약` winner.
- The next refinement should focus on shrinking weak `수주` and weak contract/article-style flow, while fixing snapshot completeness so the next analysis has less blind area.
