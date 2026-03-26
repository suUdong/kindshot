# NVIDIA LLM Day 1 Server Analysis

Date: 2026-03-27

## Scope

- Analysis time: `2026-03-27 04:22:22 KST`
- Current-day status:
  - `kindshot-server:/opt/kindshot/logs/kindshot_20260327.jsonl` does not exist yet
  - `kindshot-server:/opt/kindshot/logs/polling_trace_20260327.jsonl` exists and was last updated at `2026-03-27 04:22:22 KST`
  - `2026-03-27` journal shows `0` successful NVIDIA `200 OK` calls so far
- Day 1 closeout source:
  - `kindshot-server:/opt/kindshot/logs/kindshot_20260326.jsonl`
  - Size: `2,621,121` bytes
  - Last modified: `2026-03-26 21:17:23 KST`
- Journal source:
  - `sudo journalctl -u kindshot --since "2026-03-26 00:00" --until "2026-03-26 23:59:59"`
  - `sudo journalctl -u kindshot --since "2026-03-27 00:00"`

## Date Clarification

As of `2026-03-27 04:22 KST`, "today" has not produced a structured runtime log yet. The meaningful NVIDIA LLM "day 1" closeout is therefore the latest completed session, `2026-03-26`.

## Executive Summary

- `2026-03-27` so far:
  - executable decisions: `0`
  - NVIDIA `200 OK` calls: `0`
  - polling trace saw `3` raw items across `2` positive polls, but none produced a structured `event`/`decision` log
  - latest polling heartbeat still shows `events_seen=0`
- `2026-03-26` full-day closeout:
  - structured decisions: `51`
  - total BUYs: `0`
  - total SKIPs: `51`
  - NVIDIA-backed LLM path: `30 SKIP`, `0 BUY`
  - rule fallback: `14 SKIP`
  - rule preflight: `7 SKIP`
  - inline BUY intent from `event` rows: `15`
  - blocked inline BUYs: `15 / 15`
  - blocker split: `13 LOW_CONFIDENCE`, `2 MARKET_CLOSE_CUTOFF`
  - successful NVIDIA endpoint calls: `71`
  - timeout restarts: `53`

Read:

- The completed first NVIDIA day still produced zero executable BUYs.
- The LLM path was live and busy enough to hit the NVIDIA endpoint `71` times, but every structured LLM decision was a `SKIP` at confidence `50`.
- Late-day extensions increased the count beyond the earlier 18:00 snapshot, but they did not change the shape. More rows arrived; no BUYs escaped.

## Structured Decision Stats

`decision` rows on `2026-03-26`:

| Source | BUY | SKIP | Total | Share |
|---|---:|---:|---:|---:|
| `LLM` | 0 | 30 | 30 | `58.8%` |
| `RULE_FALLBACK` | 0 | 14 | 14 | `27.5%` |
| `RULE_PREFLIGHT` | 0 | 7 | 7 | `13.7%` |
| Total | 0 | 51 | 51 | `100.0%` |

Additional shape:

- LLM buy rate: `0.0%`
- Every LLM decision used confidence `50`
- Structured decision window ran from `11:04` to `20:47 KST`
- First structured LLM decision appeared at `14:48 KST`
- Final structured LLM skip landed after market hours at `20:47 KST`

## Inline BUY Intent

`event` rows with inline `decision_action` show the pre-execution appetite more broadly than `decision` rows.

| Metric | Count | Share |
|---|---:|---:|
| Inline BUY | 15 | `22.7%` |
| Inline SKIP | 51 | `77.3%` |
| Total actionable inline rows | 66 | `100.0%` |

Guardrail outcome on inline BUY rows:

| Guardrail result | Count | Share of inline BUY |
|---|---:|---:|
| `LOW_CONFIDENCE` | 13 | `86.7%` |
| `MARKET_CLOSE_CUTOFF` | 2 | `13.3%` |

Bucket split on inline rows:

| Bucket | BUY | SKIP |
|---|---:|---:|
| `POS_STRONG` | 15 | 35 |
| `POS_WEAK` | 0 | 16 |

Interpretation:

- BUY appetite existed only inside `POS_STRONG`.
- None of those BUY intents survived to executable trades.
- Most BUY deaths still happened at the low-confidence guardrail, but two stronger late-day candidates were blocked only because they arrived after the market-close cutoff.

## Time-Of-Day Shape

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
| `18` | 9 | 0 | 0 | 9 |
| `20` | 1 | 0 | 0 | 1 |

Interpretation:

- Morning flow was still dominated by rule-based defensive skips.
- From mid-afternoon onward, the structured path became almost entirely LLM-driven.
- Even with that larger late-day LLM footprint, the model remained a pure SKIP classifier.

## Notable Blocked BUYs

These were tagged `BUY` in the event row but never became executable:

| Time | Ticker | Guardrail | Confidence | Headline |
|---|---|---|---:|---|
| `11:10` | `000720` | `LOW_CONFIDENCE` | 72 | 현대건설 “수주 33.4조·매출 27.4조원 목표…에너지 밸류체인 경쟁력 강화” |
| `11:23` | `348210` | `LOW_CONFIDENCE` | 69 | 넥스틴, SK하이닉스와 106억 규모 공급계약 체결 |
| `13:41` | `007390` | `LOW_CONFIDENCE` | 72 | 네이처셀, 조인트스템 FDA 허가,나스닥 상장 '투트랙' 전략 |
| `16:49` | `001740` | `LOW_CONFIDENCE` | 73 | SK네트웍스, AI 전환 가속…자사주 2071만주 소각 |
| `18:59` | `088350` | `MARKET_CLOSE_CUTOFF` | 76 | 한화생명금융서비스, 출범 5년만에 사상최대 매출 2조…출범 후 7.4배 성장 |
| `19:10` | `008770` | `MARKET_CLOSE_CUTOFF` | 78 | 이부진 호텔신라 대표, 200억 원 규모 주식 매수 사전 공시 |

## LLM Skip Pattern

Top repeated LLM skip framings:

- `3`: `HARD_RULES 3. ret_3d<-5%: 원칙적 SKIP(50).`
- `2`: `adv=2000억 대형주, 뉴스 이미 반영`
- `2`: `기사, 미확정 공시`

This is still narrow behavior. The model is not selectively approving stronger late-day cases. It is repeatedly collapsing to hard-skip templates.

## Operational Notes

- `2026-03-26` journal lines: `6,489`
- `2026-03-26` NVIDIA endpoint `200 OK`: `71`
- `2026-03-26` NVIDIA non-`200` hits observed in journal: `0`
- `2026-03-26` timeout-driven failures: `53`
- `2026-03-26` service starts: `73`
- `2026-03-27` so far:
  - journal lines: `711`
  - NVIDIA endpoint `200 OK`: `0`
  - service starts: `10`
  - timeout-driven failures: `0`

## Bottom Line

The exact current-day answer is simple: as of `2026-03-27 04:22 KST`, there is no new NVIDIA trading result yet. The latest completed real runtime day is `2026-03-26`, and that day closed at `0 BUY / 51 SKIP` overall, with the NVIDIA-backed LLM path at `0 BUY / 30 SKIP`. Inline BUY appetite existed, but every candidate died in guardrails before execution.
