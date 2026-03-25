# Backtest Analysis

Date: 2026-03-26

## Scope

- Requested window: recent 7 days
- Local evidence window actually available in this workspace: `2026-03-11`, `2026-03-12`, `2026-03-13`, `2026-03-16`, `2026-03-17`, `2026-03-18`, `2026-03-19`
- Reason: local `logs/kindshot_*.jsonl` files after `2026-03-19` are absent, so the analysis uses the latest 7 logged trading days available locally.
- Primary reconstruction surface: `deploy/daily_report.py`
- Supplemental aggregation: local one-off analysis over the same JSONL logs using `deploy.daily_report._collect()` and `kindshot.strategy_observability`

## Daily Report Reconstruction

The 7-day window contains:

- `23` BUY decisions with reconstructable realized returns
- `11` wins, `12` losses
- aggregate realized return: `+0.150%`
- aggregate PnL at current `M` order size (`5,000,000 KRW`): about `+7,487 KRW`

This is effectively flat performance despite meaningful trade count, so the focus is removing the worst repeatable loser cohort without widening scope.

## Strategy Summary

### Exit behavior

| Strategy | Count | Win rate | Avg return | Sum return | Approx PnL |
|---|---:|---:|---:|---:|---:|
| `open` / no synthetic exit | 11 | 54.5% | `+0.144%` | `+1.581%` | `+79,057 KRW` |
| `max_hold` | 5 | 60.0% | `-0.045%` | `-0.225%` | `-11,234 KRW` |
| `stop_loss` | 3 | 0.0% | `-2.327%` | `-6.981%` | `-349,072 KRW` |
| `take_profit` | 2 | 100.0% | `+3.187%` | `+6.374%` | `+318,696 KRW` |
| `trailing_stop` | 2 | 0.0% | `-0.300%` | `-0.599%` | `-29,959 KRW` |

### Hold-profile behavior

| Profile | Count | Win rate | Avg return | Sum return | Approx PnL |
|---|---:|---:|---:|---:|---:|
| `15m` | 13 | 38.5% | `-0.113%` | `-1.469%` | `-73,446 KRW` |
| `30m` | 6 | 66.7% | `+0.576%` | `+3.453%` | `+172,663 KRW` |
| `EOD` | 4 | 50.0% | `-0.459%` | `-1.835%` | `-91,731 KRW` |

### Time-of-day behavior

| Time bucket | Count | Win rate | Avg return | Sum return | Approx PnL |
|---|---:|---:|---:|---:|---:|
| `09:00-10:59` open | 9 | 55.6% | `-0.077%` | `-0.691%` | `-34,562 KRW` |
| `11:00-13:59` midday | 8 | 75.0% | `+0.611%` | `+4.885%` | `+244,229 KRW` |
| `14:00+` late | 6 | 0.0% | `-0.674%` | `-4.044%` | `-202,181 KRW` |

## Improvement Point

The clearest loser cluster is the intersection of:

- hold profile `15m`
- detected at or after `14:00` KST

Observed 7-day result:

- `5` trades
- `0` wins / `5` losses
- average return `-0.796%`
- sum return `-3.979%`
- approximate PnL `-198,934 KRW`

Affected examples:

- `2026-03-17 14:xx` 서진시스템 ESS 공급계약: `-3.050%`
- `2026-03-17 14:xx` 대한조선 수주: `-0.562%`
- `2026-03-12 15:xx` 효성중공업 호주 ESS 수주: `-0.156%`
- `2026-03-12 16:xx` 삼성물산 단일판매/공급계약: `-0.089%`
- `2026-03-17 15:xx` 셀트리온 수주 기사: `-0.121%`

Interpretation:

- short-hold catalysts such as `공급계약`, `수주`, `납품계약` do not have enough high-quality intraday follow-through late in the session
- the existing generic closing confidence gate is too late for this fast-decay cohort
- a profile-aware late-entry guardrail is a bounded, reversible fix
- note: several logged losers were also low-confidence by today's stricter floor, but the historical cluster still exposes a separate semantic gap: a fast-decay profile can still look attractive late in the day if confidence is high, so the profile-aware guardrail remains additive rather than duplicative

## Selected Hypothesis

Block new BUY decisions for `15m` hold-profile headlines detected at or after `14:00` KST.

Why this hypothesis:

- It targets one repeatable underperformer without broadening risk.
- It leaves `30m` and `EOD` profiles untouched.
- It is easy to validate on replay/logged data and easy to roll back.

What-if impact on the 7-day window if those 5 trades were blocked:

- trade count: `23 -> 18`
- win rate: `47.8% -> 61.1%`
- sum return: `+0.150% -> +4.128%`
- approximate PnL: `+7,487 KRW -> +206,421 KRW`

## Implementation Plan

1. Add a config-backed cutoff for fast hold profiles (`15m`) at `14:00` KST.
2. Make time-based guardrails consume the event/decision timestamp instead of `datetime.now()` so runtime and replay analysis use the same rule.
3. Pass the detected/decision timestamp through pipeline and replay guardrail calls.
4. Add regression tests for:
   - `15m` profile BUY blocked at `14:00+`
   - `15m` profile BUY allowed before cutoff
   - `30m` / `EOD` profiles unaffected
   - replay/runtime time injection keeps existing generic time rules deterministic

## Implementation Result

Implemented:

- added config-backed fast-profile cutoff defaults: `15m` profiles blocked from `14:00` KST
- time-based guardrails now accept injected decision time instead of relying only on `datetime.now()`
- live pipeline passes actual decision time into the guardrail layer
- replay uses logged `detected_at` as the deterministic decision-time proxy because historical logs do not retain a separate guardrail decision timestamp

Changed code:

- `src/kindshot/config.py`
- `src/kindshot/guardrails.py`
- `src/kindshot/pipeline.py`
- `src/kindshot/replay.py`
- `tests/test_config.py`
- `tests/test_guardrails.py`
- `tests/test_pipeline.py`
- `tests/test_replay.py`

## Rollout, Observability, Validation, Rollback

- Rollout: direct code path change only; no deploy or secret handling changes.
- Observability: blocked trades should surface as a dedicated guardrail reason in runtime logs.
- Validation:
  - `pytest tests/test_config.py tests/test_guardrails.py tests/test_pipeline.py tests/test_replay.py -q` -> `143 passed`
  - `pytest -q` -> `554 passed, 1 warning`
  - LSP diagnostics on affected source/test files -> `0` errors
- Rollback:
  - revert the fast-profile cutoff config and guardrail branch
  - remove the new guardrail reason wiring if it proves too aggressive

## Follow-up Slice

After removing the late `15m` cohort, the residual trade set becomes:

- `18` trades
- `11` wins / `7` losses
- sum return `+4.128%`

In that reduced set, the weakest keyword-specific cohort is now M&A:

| Keyword | Count | Win rate | Avg return | Sum return |
|---|---:|---:|---:|---:|
| `인수` / `합병` | 2 | 50.0% | `-1.068%` | `-2.136%` |

Observed path:

- `2026-03-11` 엔씨 `인수` headline: `t+30m -0.698%`, `close -2.326%`
- `2026-03-19` 보스턴다이내믹스 `인수` headline: `t+30m +0.189%`, `close +0.189%`

Interpretation:

- current `EOD` mapping for M&A is too permissive
- the downside mostly comes from carrying the weaker case from `t+30m` into `close`
- unlike shareholder-return headlines, M&A headlines in this sample do not justify an all-day hold

Selected follow-up hypothesis:

- move `인수` / `합병` from `EOD` to `30m`
- keep shareholder-return keywords (`자사주 소각`, `자사주 취득`, `배당`, `주주환원`) at `EOD`

What-if impact after the late `15m` filter:

- trade count unchanged: `18`
- win rate unchanged: `61.1%`
- sum return: `+4.128% -> +5.756%`

Planned code touchpoints for this slice:

- `src/kindshot/hold_profile.py`
- `tests/test_hold_profile.py`
- `docs/backtest-analysis.md`

Implementation result:

- moved `인수` / `합병` hold profiles from `EOD` to `30m`
- left shareholder-return keywords on `EOD`

Validation for this slice:

- `pytest tests/test_hold_profile.py tests/test_strategy_observability.py tests/test_daily_report.py -q` -> `16 passed`
- `pytest -q` -> `569 passed, 1 warning`
- LSP diagnostics on affected files -> `0` errors

## Analysis Tooling Gap

The next bounded improvement is analysis correctness rather than another trading-rule change.

Current issue:

- `scripts/strategy_comparison.py` still hardcodes stale exit assumptions:
  - TP `1.5%`
  - SL `-1.0%`
  - trailing activation/drop `0.8%`
  - uniform `30m` max hold
- current runtime/backtest logic has already diverged from that:
  - runtime paper TP default `1.0%`
  - runtime paper SL default `-0.7%`
  - time-tiered trailing stop
  - keyword-based hold profiles (`15m`, `20m`, `30m`, `EOD`)

Why it matters:

- future improvement decisions would be made from a stale comparison report
- comparison output would disagree with `deploy/daily_report.py` and `strategy_observability`

Selected tooling hypothesis:

- make `scripts/strategy_comparison.py` reuse the same current exit reconstruction primitives as `strategy_observability` so comparison output stays aligned with the live/reporting strategy surface

Planned code touchpoints:

- `scripts/strategy_comparison.py`
- `tests/test_strategy_comparison.py`
- `docs/backtest-analysis.md`

Implementation result:

- made `scripts/strategy_comparison.py` reuse `classify_buy_exit()` and the full hold-profile-aware horizon set
- added script-level regression coverage for current SL and short-hold behavior
- kept the change bounded to the comparison layer; runtime strategy code stays untouched in this slice

Validation for this slice:

- `pytest tests/test_daily_report.py tests/test_strategy_comparison.py -q` -> `3 passed`
- `pytest -q` -> `572 passed, 1 warning`
- LSP diagnostics on affected files -> `0` errors
