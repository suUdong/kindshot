# NVIDIA LLM Performance Analysis

Date: 2026-03-26

## Scope

- Snapshot time: `2026-03-26 15:53:39 KST`
- Snapshot source: `kindshot-server:/opt/kindshot/logs/kindshot_*.jsonl`
- Analyzed files: `kindshot_20260318.jsonl`, `kindshot_20260319.jsonl`, `kindshot_20260320.jsonl`, `kindshot_20260323.jsonl`, `kindshot_20260324.jsonl`, `kindshot_20260325.jsonl`, `kindshot_20260326.jsonl`
- Excluded from the headline decision window:
  - `2026-03-10` to `2026-03-17` for event-level BUY/SKIP stats because inline `decision_action` coverage is incomplete there
  - `2026-03-25` from all decision stats because the snapshot contains no `decision` rows on that date

## Measurement Caveat

This report is best read as a server-side LLM-path analysis, not a perfect per-call NVIDIA attribution audit.

- Server runtime config at snapshot time:
  - `LLM_PROVIDER=nvidia`
  - `NVIDIA_API_KEY` present
  - `ANTHROPIC_API_KEY` present
  - `LLM_MODEL=claude-haiku-4-5-20251001`
- Actual routing is controlled by `LLM_PROVIDER` and `NVIDIA_API_KEY` in `src/kindshot/llm_client.py`.
- But `DecisionRecord.llm_model` is populated from `self._config.llm_model` in `src/kindshot/decision.py`, not from `self._config.nvidia_model`.

Implication:

- `decision_source=LLM` means "provider-configured LLM path succeeded".
- It does not prove the exact upstream model from the structured log alone.
- `decision_source=RULE_FALLBACK` and `decision_source=RULE_PREFLIGHT` are authoritative and should be treated as non-LLM paths.

## Executive Summary

- Structured `decision` rows in the measured window: `107`
- `LLM`: `89` (`83.2%`)
- `RULE_FALLBACK`: `14` (`13.1%`)
- `RULE_PREFLIGHT`: `4` (`3.7%`)
- LLM BUY/SKIP split: `15 BUY`, `74 SKIP`
- LLM buy rate: `16.9%`

Key read:

- The clean `2026-03-18` to `2026-03-24` server window is dominated by the LLM path, but the model is highly conservative after `2026-03-18`.
- `2026-03-26` is not a clean NVIDIA-only sample. That day contains only `4` LLM decisions and `18` rule-based skips.
- On the executable decision path, the LLM skipped far more often than it bought, and the few BUYs that did pass were weak on downstream price follow-through.

## Source-Split BUY/SKIP Stats

Authoritative source split comes from `decision` rows.

| Date | LLM BUY | LLM SKIP | Rule fallback SKIP | Rule preflight SKIP | Total |
|---|---:|---:|---:|---:|---:|
| `2026-03-18` | 6 | 7 | 0 | 0 | 13 |
| `2026-03-19` | 2 | 23 | 0 | 0 | 25 |
| `2026-03-20` | 7 | 13 | 0 | 0 | 20 |
| `2026-03-23` | 0 | 21 | 0 | 0 | 21 |
| `2026-03-24` | 0 | 6 | 0 | 0 | 6 |
| `2026-03-26` | 0 | 4 | 14 | 4 | 22 |

Interpretation:

- `2026-03-18` was the only balanced LLM day in this sample (`6 BUY`, `7 SKIP`).
- `2026-03-19` immediately swung to a strong SKIP bias (`2 BUY`, `23 SKIP`).
- `2026-03-23` and `2026-03-24` were fully one-sided: `27` consecutive LLM SKIPs.
- `2026-03-26` shows runtime degradation or fail-open avoidance rather than normal LLM behavior, because rule-based paths replaced most structured decisions.

## Event-Level BUY Intent

`event` rows capture raw inline BUY/SKIP intent more broadly than `decision` rows, including many BUYs that never became executable because guardrails blocked them.

- Inline BUY/SKIP events: `152`
- BUY intent: `67` (`44.1%`)
- SKIP intent: `85` (`55.9%`)
- BUY intents that passed guardrails: `9`
- BUY intents blocked by guardrails: `58` (`86.6%` of BUY intents)

BUY-intent guardrail breakdown:

| Guardrail result | Count | Share of BUY intents |
|---|---:|---:|
| `MARKET_CLOSE_CUTOFF` | 22 | `32.8%` |
| `INTRADAY_VALUE_TOO_THIN` | 21 | `31.3%` |
| `LOW_CONFIDENCE` | 6 | `9.0%` |
| `CHASE_BUY_BLOCKED` | 5 | `7.5%` |
| `ORDERBOOK_TOP_LEVEL_LIQUIDITY` | 4 | `6.0%` |
| `PASSED` | 9 | `13.4%` |

Bucket split on inline event rows:

| Bucket | BUY | SKIP | Buy rate |
|---|---:|---:|---:|
| `POS_STRONG` | 63 | 40 | `61.2%` |
| `POS_WEAK` | 4 | 45 | `8.2%` |

Interpretation:

- The system still generates a lot of raw BUY appetite inside `POS_STRONG`.
- Most of that appetite is not executable.
- The dominant blockers are time-of-day and thin-trading filters, not outright negative model judgment.

## LLM Confidence Shape

LLM-path `decision` rows are sharply separated by confidence.

| Action | Confidence band | Count |
|---|---|---:|
| `BUY` | `70-79` | 11 |
| `BUY` | `80-89` | 4 |
| `SKIP` | `<50` | 40 |
| `SKIP` | `50-59` | 33 |
| `SKIP` | `60-69` | 1 |

Interpretation:

- The server LLM path is acting almost like a hard classifier, not a ranking model.
- BUYs cluster in `70+`.
- SKIPs cluster below `60`.
- That makes confidence operationally useful as a gate, but not very informative inside each side of the boundary.

## Outcome Check

This section uses the best available downstream horizon per decision:

- prefer `close`
- else `t+30m`
- else `t+15m`

So this is a directional quality check, not a clean realized-PnL study.

### LLM path only

| Action | Covered rows | Avg return | Median return | Wins | Win rate |
|---|---:|---:|---:|---:|---:|
| `BUY` | 15 | `-0.420%` | `-0.528%` | 5 | `33.3%` |
| `SKIP` | 45 | `+1.457%` | `0.000%` | 15 | `33.3%` |

Interpretation:

- LLM BUY quality was weak in the measured window.
- LLM SKIP average return looks positive, but the median is exactly `0.000%`.
- That means the SKIP average is being pulled up by a few large false negatives rather than by broad, systematic under-trading.

### Rule paths on `2026-03-26`

| Source | Action | Covered rows | Avg return | Median return | Wins |
|---|---|---:|---:|---:|---:|
| `RULE_FALLBACK` | `SKIP` | 5 | `-0.220%` | `-0.260%` | 2 |
| `RULE_PREFLIGHT` | `SKIP` | 2 | `-0.367%` | `-0.367%` | 0 |

The rule paths were not especially strong, but they were at least defensive on a day where the LLM path was no longer the dominant source of decisions.

## Notable Cases

### Largest LLM false negatives

These are the clearest missed-upside cases inside covered LLM SKIPs.

| Date | Ticker | Return | Headline |
|---|---|---:|---|
| `2026-03-20` | `053690` | `+17.495%` | 한미글로벌, 국내 실적 반등 본격화·원전사업 기대…목표가↑-현대차 |
| `2026-03-20` | `053690` | `+17.495%` | 현대차證 “한미글로벌, 국내외 법인 매출 증가 전망… 목표가 3만2000원” |
| `2026-03-19` | `001510` | `+15.957%` | SK증권 "CJ, 상장 자회사 실적 개선 기대…목표가↑" |
| `2026-03-20` | `053690` | `+13.333%` | 한미글로벌, 국내 실적 반등 본격 전망에 7% '강세'[특징주] |

Pattern:

- The largest misses were concentrated in research-note / 전망형 POS_WEAK headlines.
- That is a false-negative problem, but it is not evenly distributed across all SKIPs.

### Worst covered LLM BUYs

| Date | Ticker | Return | Headline |
|---|---|---:|---|
| `2026-03-20` | `358570` | `-2.313%` | 지아이이노베이션, J&J 자회사 얀센과 전립선암 병용 임상 착수 |
| `2026-03-19` | `005380` | `-1.137%` | 보스턴다이내믹스 기업가치 30조 평가...인수가 대비 24배 증가 |
| `2026-03-19` | `000660` | `-1.123%` | 마이크론 최대 매출…‘32만전자’ 보인다 |

The best covered LLM BUY in the whole sample was only `+0.937%`.

## Conclusions

### 1. The server LLM path is currently too defensive after `2026-03-18`

- LLM BUY rate across the measured window is only `16.9%`.
- `2026-03-23` and `2026-03-24` produced zero LLM BUYs.
- The system is not behaving like a balanced BUY/SKIP selector anymore.

### 2. Raw BUY appetite exists, but most of it dies in guardrails

- Inline event rows show `67` BUY intents.
- Only `9` survived guardrails.
- Most suppression came from market-close and thin-liquidity checks.

This means the bottleneck is not only model conservatism. Execution filters are also removing most candidate BUYs.

### 3. The biggest miss cluster is POS_WEAK research-note flow

- The most painful false negatives came from brokerage-note / 전망형 headlines.
- That cluster needs a narrower hypothesis than "make the LLM more aggressive overall".

### 4. `2026-03-26` should not be treated as a clean NVIDIA benchmark day

- Structured decisions on `2026-03-26` were `4` LLM, `14` rule fallback, `4` rule preflight.
- That day reflects fallback-heavy degraded operation, not a normal steady-state sample.

## Next Hypothesis

If only one bounded next step is taken from this report, the highest-signal candidate is:

`POS_WEAK` brokerage-note / 전망형 headline salvage pass

Reason:

- The worst false negatives were concentrated there.
- LLM BUY quality is already weak, so a global BUY-threshold relaxation would likely add more bad trades than good ones.
- A narrow rescue rule for repeated high-momentum research-note headlines is a safer next experiment than making the whole system less conservative.
