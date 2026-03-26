# Daily NVIDIA LLM BUY/SKIP Report

Date: 2026-03-26

## Scope

- Snapshot time: `2026-03-26 18:00:01 KST`
- Snapshot source: `kindshot-server:/opt/kindshot/logs/kindshot_20260326.jsonl`
- Snapshot size: `2,536` rows
- Focus: today server log only, using structured `decision` rows for executable BUY/SKIP counts and `event` rows for inline BUY intent / guardrail blocking

## Measurement Caveat

- `journalctl -u kindshot` on `2026-03-26` shows `55` successful `POST https://integrate.api.nvidia.com/v1/chat/completions` calls, so the live LLM path did hit NVIDIA today.
- Structured `decision` rows still record `llm_model=claude-haiku-4-5-20251001`, so `llm_model` is not reliable provider evidence by itself.
- In this report, "NVIDIA LLM" means the server-side `decision_source=LLM` path, interpreted together with the same-day journal evidence above.
- This is a point-in-time snapshot. The local copy was frozen at `18:00:01 KST` even though the service kept restarting and appending new rows afterward.

## Executive Summary

- Executable decisions: `41`
- Total BUYs: `0`
- Total SKIPs: `41`
- NVIDIA LLM path: `20 SKIP`, `0 BUY`
- Rule fallback: `14 SKIP`
- Rule preflight: `7 SKIP`
- Inline BUY intent from `event` rows: `11`
- Guardrail-blocked inline BUYs: `11 / 11` (`100.0%`), all `LOW_CONFIDENCE`

Read:

- Today the server never produced a single executable BUY.
- The NVIDIA LLM path stayed active through the afternoon, but every structured LLM decision was a `SKIP` with confidence fixed at `50`.
- Raw BUY appetite still appeared in `POS_STRONG`, but all eleven BUY intents died at the low-confidence guardrail before they could become executable trades.

## Structured Decision Stats

| Source | BUY | SKIP | Total | Share |
|---|---:|---:|---:|---:|
| `LLM` | 0 | 20 | 20 | `48.8%` |
| `RULE_FALLBACK` | 0 | 14 | 14 | `34.1%` |
| `RULE_PREFLIGHT` | 0 | 7 | 7 | `17.1%` |
| Total | 0 | 41 | 41 | `100.0%` |

Additional shape:

- LLM buy rate: `0.0%`
- Every LLM decision used confidence `50`
- Rule fallback confidence band: mostly `72` (`10 / 14`)
- Rule preflight confidence band: `35-45`

## Inline BUY Intent

`event` rows show the pre-execution intent more broadly than `decision` rows.

| Metric | Count | Share |
|---|---:|---:|
| Inline BUY | 11 | `21.2%` |
| Inline SKIP | 41 | `78.8%` |
| Total actionable inline rows | 52 | `100.0%` |

Guardrail outcome on inline BUY rows:

| Guardrail result | Count |
|---|---:|
| `LOW_CONFIDENCE` | 11 |

Bucket split on inline rows:

| Bucket | BUY | SKIP |
|---|---:|---:|
| `POS_STRONG` | 11 | 35 |
| `POS_WEAK` | 0 | 6 |

Interpretation:

- The system did surface positive `POS_STRONG` candidates throughout the day.
- None of them survived to become executable BUYs.
- This was not a mixed BUY/SKIP day. It was a fully defensive day end-to-end.

## Time-of-Day Shape

Structured decision rows converted to KST:

| Hour | LLM SKIP | Rule fallback SKIP | Rule preflight SKIP | Total |
|---|---:|---:|---:|---:|
| `11` | 0 | 5 | 0 | 5 |
| `12` | 0 | 3 | 0 | 3 |
| `13` | 0 | 4 | 1 | 5 |
| `14` | 1 | 2 | 2 | 5 |
| `15` | 4 | 0 | 2 | 6 |
| `16` | 9 | 0 | 0 | 9 |
| `17` | 6 | 0 | 2 | 8 |

Interpretation:

- Morning flow was dominated by rule-based skips.
- The first structured LLM decision did not appear until `14:48:55 KST`.
- From `15:00` onward, the LLM path became the dominant structured path, but it still emitted only `SKIP`.

## Notable Inline BUYs That Were Blocked

All of these were tagged `BUY` in the event row and then stopped at `LOW_CONFIDENCE`.

| Time | Ticker | Bucket | Confidence | Headline |
|---|---|---|---:|---|
| `11:10` | `000720` | `POS_STRONG` | 72 | 현대건설 “수주 33.4조·매출 27.4조원 목표…에너지 밸류체인 경쟁력 강화” |
| `11:15` | `004800` | `POS_STRONG` | 69 | 지식재산처, 민·관 협력으로 ‘글로벌 특허전쟁’ 대응 |
| `11:23` | `348210` | `POS_STRONG` | 69 | 넥스틴, SK하이닉스와 106억 규모 공급계약 체결 |
| `11:25` | `348210` | `POS_STRONG` | 69 | 넥스틴 수주공시 - Wafer Inspection System 공급계약 106.1억원 |
| `11:55` | `069620` | `POS_STRONG` | 69 | 대웅제약·종근당 자카비 특허 빗장 해제…후발주자 속도전 |
| `13:41` | `007390` | `POS_STRONG` | 72 | 네이처셀, 조인트스템 FDA 허가, 나스닥 상장 '투트랙' 전략 |
| `16:25` | `001740` | `POS_STRONG` | 73 | SK네트웍스, 2071만주 자사주 소각…“AI 전환 속도 높일 것” |
| `16:37` | `008770` | `POS_STRONG` | 73 | 이부진 호텔신라 사장, 자사주 200억 규모 매입 |
| `16:47` | `001740` | `POS_STRONG` | 73 | SK네트웍스, 자사주 2071만주 소각..."주당 가치 제고" |
| `16:49` | `001740` | `POS_STRONG` | 73 | SK네트웍스, AI 전환 가속…자사주 2071만주 소각 |
| `17:06` | `085620` | `POS_STRONG` | 71 | 미래에셋생명보험(주) 주식 소각 결정 |

## LLM Skip Pattern

The twenty structured LLM skips were narrow and repetitive:

- large-cap / already-priced framing:
  - `adv=2000억 대형주, 뉴스 이미 반영`
  - `대형주(adv>2000억), 이미 반영`
  - `대형주 adv=792억, 뉴스 이미 반영, confidence 72 BUY 불가`
- hard-rule framing:
  - `과열 종목 진입 금지, ret_3d>20%`
  - `HARD_RULES: ret_today>3% 추격매수 금지`
  - `HARD_RULES: adv_20d>5000억 초대형주 sell the news`
- article / non-firm-news framing:
  - `기사, 미확정 공시`
  - `기사, 미확정 표현 없음, 그러나 ... 뉴스기사 ... SKIP(35)`

The model was not exploring nuanced BUY sizing or borderline approvals. It was acting like a hard SKIP classifier.

## Operational Notes

- `systemctl show -p ActiveEnterTimestamp kindshot --value` reported the last service start at `Thu 2026-03-26 17:57:22 KST`.
- Same-day journal shows `49` timeout-driven restarts and `49` matching `Failed with result 'timeout'` entries.
- Even with that instability, journal entries still show `55` successful NVIDIA endpoint calls on the same date.

## Bottom Line

The server's NVIDIA-backed LLM path did run today, but it produced `0 BUY / 20 SKIP`. Including rule paths, the full executable decision stream was `0 BUY / 41 SKIP`. Inline BUY intent existed, but all `11` BUY candidates were stopped by `LOW_CONFIDENCE` before they could become executable trades.
