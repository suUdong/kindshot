# 2026-03-29 v78 Guardrail Profitability Validation

## Intent

이번 slice는 `910a331` 커밋의 `v78 가드레일 완화` 백테스트 산출물을 재검증하고, 차단률 전후 비교와 기대 수익률 시뮬레이션을 하나의 재현 가능한 리포트로 정리하는 것이다. 전략 로직을 바꾸지 않고, 분석 산출물의 신뢰도와 설명 가능성을 높이는 데 집중한다.

## Current Evidence

- `reports/signal-backtest-result.md` 는 `87` BUY 시그널, `42`개 deduped 통과 시그널, `32` 차단, `T+1 -4.25%`, `T+5 +2.39%`를 보고한다.
- 같은 리포트의 상세 테이블은 `42`개 행이며, 각 행에 `date / ticker / entry_px / T+1 / T+5 / 원래가드레일`이 남아 있다.
- `reports/guardrail_sim.json` 는 로그 기반 `229`개 guardrail-eligible 이벤트에서 `v77 132 pass / 97 block`, `v78 136 pass / 93 block`를 보고한다.
- 현재 워크트리에는 `data/trade_history.db` 가 없어서 `scripts/backtest_signals.py` 를 그대로 재실행할 수 없다.

## Decision

분석을 두 데이터면으로 분리한다.

1. `throughput validation`
   `reports/guardrail_sim.json` 를 사용해 `v77 ↔ v78` 차단률/통과율 변화를 검증한다.
2. `profitability validation`
   `reports/signal-backtest-result.md` 의 상세 테이블을 파싱하고 `pykrx` 종가로 `T+1/T+5` 수익률을 재계산해 `910a331` 수익성 수치를 독립 검증한다.

그 위에 `원래가드레일=PASSED` 와 `완화로 새로 편입된 시그널`을 분리해 기대 수익률 시뮬레이션을 제시한다.

## What Changes

### 1. Validation script

새 분석 스크립트는 아래를 수행해야 한다.

- `reports/signal-backtest-result.md` 파싱
- `pykrx` 재조회로 T+1/T+5 mismatch 검증
- `reports/guardrail_sim.json` 기반 전후 차단률 비교
- baseline pass vs newly admitted 분해 통계
- bootstrap mean interval 기반 기대 수익률 시뮬레이션
- 최종 markdown 리포트 생성

### 2. Report framing

리포트는 반드시 아래 caveat를 명시한다.

- `87 total / 42 passed / 32 blocked` 는 분모가 일치하지 않는다.
- `42`는 deduped pass count이고, `32`는 raw blocked count다.
- 따라서 raw throughput 비교는 `guardrail_sim.json` 을 source of truth 로 사용하고, profitability 비교는 `42`개 상세 행을 source of truth 로 사용한다.

### 3. Tests

로직 변경에 대해 최소 단위 테스트를 추가한다.

- markdown row parsing
- dedupe mismatch inference
- simulation stats / bootstrap helper의 구조적 검증

## Rollout

1. add validation script
2. add targeted unit tests
3. generate `reports/` validation report
4. run targeted pytest + compileall
5. architect review
6. update loop memory
7. commit + push

## Rollback

- remove the added validation script, tests, and report
- leave runtime strategy, deployment, secrets, and live-order behavior untouched
