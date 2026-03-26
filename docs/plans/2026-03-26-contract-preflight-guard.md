# 2026-03-26 Contract Preflight Guard

## Goal

최근 주간 성과 분석에서 `수주` 버킷은 재구성 가능한 4건이 모두 음수였다. 이번 slice의 목표는 `수주`/`공급계약` 계열에서 **LLM까지 보낼 필요가 없는 명백한 손실 패턴**을 deterministic preflight로 먼저 차단해, 같은 유형의 BUY 재발을 줄이는 것이다.

## Evidence

최근 7개 실제 로그일(`20260311`, `20260312`, `20260313`, `20260316`, `20260317`, `20260318`, `20260319`) 기준 재구성:

- `수주`: 4 realized trades, 0 wins, cumulative return `-1.074%`
- 대표 손실 패턴:
  - 기사형 headline: `‘파죽지세’ ... 호주서 ESS 수주`, `‘시밀러’ 넘어 ‘CDMO’로… ... 수주 1조 돌파`, `[카드] ... 스팀터빈 수주`
  - 점진 물량 headline: `1척 추가 수주`
  - 하락 추세 역매수: `ret_3d=-9.03%` 상태에서 `1.01조` 수주 BUY
  - 대형주 계약/수주: `adv_20d > 2000억` 구간에서 계약 뉴스 BUY

반면 `공급계약`은 손실이 있었지만 일부 승리 사례와 대형 확정 공시 승리도 함께 존재했다. 따라서 이번 run은 `공급계약` 전체를 약화시키지 않고, **계약/수주 family에 공통으로 적용 가능한 명백한 no-trade 패턴만 차단**한다.

## Hypothesis

계약/수주 family headline에 대해 아래 조건을 LLM 이전에 deterministic하게 차단하면, 최근 손실을 만든 저품질 BUY cohort를 줄이면서 중형주 확정 대형 계약의 승리 패턴은 대부분 보존할 수 있다.

## Scope

적용 대상은 headline 또는 `keyword_hits`가 아래 contract family에 매칭되는 경우다.

- `수주`
- `공급계약`
- `공급 계약`
- `납품계약`
- `단일판매`

이번 run에서 추가할 preflight SKIP 조건:

1. 기사형/해설형 headline
2. 점진 물량/추가 수주 headline
3. `ret_today >= 3.0%` 추격 구간
4. `ret_3d <= -5.0%` 하락 추세 구간
5. `adv_20d > 2000억` 대형주 구간

## Design

`src/kindshot/decision.py` 에 contract preflight helper를 추가하고, `DecisionEngine.decide()` 가 LLM 호출 전에 이를 확인한다.

예상 흐름:

1. contract family 여부 확인
2. preflight rule hit 시 즉시 `SKIP` `DecisionRecord` 반환
3. `llm_model` 은 preflight 전용 태그 사용
4. `decision_source` 는 LLM과 구분되는 전용 source 사용
5. preflight에 걸리지 않은 이벤트만 기존 LLM path 유지

이 방식의 장점:

- 프롬프트 준수 실패를 코드에서 강제 차단
- `BUY(72)` 같은 과거 오류 패턴을 upstream에서 재발 방지
- deploy/runtime 외부 경로를 건드리지 않음
- 테스트 가능한 deterministic behavior 확보

## Logging And Observability

- `DecisionRecord.reason` 에 `rule_preflight:*` prefix를 남긴다.
- `decision_source` 에 preflight 전용 값을 사용해 이후 로그 집계에서 분리 가능하게 한다.
- event record inline decision도 기존 pipeline 경로를 그대로 타므로 operator가 별도 포맷 변경 없이 읽을 수 있다.

## Validation

추가할 테스트:

- 기사형 `수주` headline은 LLM 호출 없이 SKIP
- `1척 추가 수주` headline은 LLM 호출 없이 SKIP
- contract family + `ret_3d <= -5.0` 는 LLM 호출 없이 SKIP
- contract family + `adv_20d > 2000억` 는 LLM 호출 없이 SKIP
- 정상 대형 확정 공급계약은 기존 LLM path로 계속 전달

실행 검증:

- `tests/test_decision.py`
- `tests/test_pipeline.py`
- 관련 회귀가 넓게 걸리는 `tests/test_rule_fallback.py`, `tests/test_strategy_observability.py`, `tests/test_daily_report.py`

## Rollout

- 단일 reversible slice로 적용
- 실거래/배포 경로는 변경하지 않음
- 로그에서 `decision_source=RULE_PREFLIGHT` 빈도와 해당 cohort의 후속 수익률을 다음 리뷰에서 확인

## Rollback

- `decision.py` 의 preflight helper와 테스트만 되돌리면 된다.
- 프롬프트 기반 판단 경로는 그대로 남아 있으므로 기능 롤백 범위가 작다.
