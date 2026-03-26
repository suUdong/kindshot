# Daily NVIDIA LLM BUY/SKIP Report

Date: 2026-03-26

## Scope

- Snapshot time: `2026-03-26 16:14:37 KST`
- Snapshot source: `kindshot-server:/opt/kindshot/logs/kindshot_20260326.jsonl`
- Snapshot size: `1,795` rows
- Focus: today server log only, using structured `decision` rows for executable BUY/SKIP counts and `event` rows for inline BUY intent / guardrail blocking

## Measurement Caveat

- `journalctl -u kindshot` on `2026-03-26` shows successful `POST https://integrate.api.nvidia.com/v1/chat/completions` calls, so the live LLM path was hitting NVIDIA today.
- Structured `decision` rows still record `llm_model=claude-haiku-4-5-20251001`, so `llm_model` is not reliable provider evidence by itself.
- In this report, "NVIDIA LLM" means the server-side `decision_source=LLM` path, interpreted together with the same-day journal evidence above.

## Executive Summary

- Executable decisions: `28`
- Total BUYs: `0`
- Total SKIPs: `28`
- NVIDIA LLM path: `9 SKIP`, `0 BUY`
- Rule fallback: `14 SKIP`
- Rule preflight: `5 SKIP`
- Inline BUY intent from `event` rows: `6`
- Guardrail-blocked inline BUYs: `6 / 6` (`100.0%`), all `LOW_CONFIDENCE`

Read:

- Today the server never produced a single executable BUY.
- The NVIDIA LLM path was active, but every structured LLM decision was a `SKIP` with confidence fixed at `50`.
- Raw BUY appetite still appeared in `POS_STRONG`, but it died before execution because all six BUY intents were blocked by the low-confidence guardrail.

## Structured Decision Stats

| Source | BUY | SKIP | Total | Share |
|---|---:|---:|---:|---:|
| `LLM` | 0 | 9 | 9 | `32.1%` |
| `RULE_FALLBACK` | 0 | 14 | 14 | `50.0%` |
| `RULE_PREFLIGHT` | 0 | 5 | 5 | `17.9%` |
| Total | 0 | 28 | 28 | `100.0%` |

Additional shape:

- LLM buy rate: `0.0%`
- Every LLM decision used confidence `50`
- Rule fallback confidence band: mostly `68-72`
- Rule preflight confidence band: `35-45`

## Inline BUY Intent

`event` rows show the pre-execution intent more broadly than `decision` rows.

| Metric | Count | Share |
|---|---:|---:|
| Inline BUY | 6 | `17.6%` |
| Inline SKIP | 28 | `82.4%` |
| Total actionable inline rows | 34 | `100.0%` |

Guardrail outcome on inline BUY rows:

| Guardrail result | Count |
|---|---:|
| `LOW_CONFIDENCE` | 6 |

Bucket split on inline rows:

| Bucket | BUY | SKIP |
|---|---:|---:|
| `POS_STRONG` | 6 | 24 |
| `POS_WEAK` | 0 | 4 |

Interpretation:

- The system did surface some positive `POS_STRONG` candidates.
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
| `16` | 4 | 0 | 0 | 4 |

Interpretation:

- Morning flow was dominated by rule-based skips.
- The first structured LLM decision did not appear until `14:48:55 KST`.
- From that point through the snapshot, the LLM path still emitted only `SKIP`.

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

## LLM Skip Pattern

The nine structured LLM skips were narrow and repetitive:

- large-cap / already-priced framing:
  - `adv=2000억 대형주, 뉴스 반영 이미 완료`
  - `대형주 adv=792억, 뉴스 이미 반영, confidence 72 BUY 불가`
- overheated / chase framing:
  - `과열 종목 진입 금지, ret_3d>20%`
  - `ret_today=2.46%, adv=23억 소규모 계약`
  - `ret_today=2.46%, 금액 16억원 소규모 계약`
- weak research-note framing:
  - `ret_3d=7.3% 모멘텀 소진, 뉴스 반영 가능성`
  - `adv=527억, 뉴스 반영 이미 완료, confidence 상한 72`

The model was not exploring nuanced BUY sizing or borderline approvals today. It was acting like a hard SKIP classifier.

## Operational Notes

- `systemctl status kindshot` at inspection time showed the service running and freshly restarted at `2026-03-26 16:10:20 KST`.
- Same-day journal shows the previous process stopped by timeout and was killed before restart.
- Both before and after the restart, journal entries show successful NVIDIA endpoint calls with `HTTP/1.1 200 OK`.

## Bottom Line

The server's NVIDIA-backed LLM path did run today, but it produced `0 BUY / 9 SKIP`. Including rule paths, the full executable decision stream was `0 BUY / 28 SKIP`. Inline BUY intent existed, but all six BUY candidates were stopped by `LOW_CONFIDENCE` before they could become executable trades.
