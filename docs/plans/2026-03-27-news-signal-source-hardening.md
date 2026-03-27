# 2026-03-27 News Signal Source Hardening

## Goal

현재 Kindshot 뉴스 파이프라인은 `KIS`/`KIND`/`DART` 소스를 모두 받을 수 있지만, 버킷팅과 시그널 결정이 거의 그대로 `raw.title`에 의존한다. 그 결과 KIS 기사형 제목이 `수주`/`공급계약` 같은 고신호 키워드를 포함하면 실제 공시형 이벤트처럼 해석될 수 있다. 이번 slice의 목표는 **새 소스를 붙이지 않고 기존 제목 파서를 source-aware 하게 개선해 news→signal 정확도를 높이는 것**이다.

## Hypothesis

`KIS` 기사형 계약/수주 제목에 대해 source-aware parser가 정규화된 analysis headline과 commentary/article flag를 만들고, decision/guardrail이 그 결과를 사용하면 weak contract false positive를 줄일 수 있다.

## Evidence

- `docs/2026-03-26-fire-performance-analysis.md`
  - `수주`와 article-style / mega-cap `공급계약`이 가장 손실이 큰 다음 가설로 지목됨.
- 실제 로그 예시:
  - `RF시스템즈, 글로벌 방산 수요 확대 속 '구조적 성장'…대규모 수주로 턴어라운드 '본격화'`
  - `KB증권 "삼성전자, 추가 상승 여력 충분…장기공급계약 요구 큰 폭 증가"`
- 둘 다 기사형/리포트형 framing인데 contract keywords 때문에 기존 파이프라인에서 positive path로 진입할 위험이 있다.

## Scope

- shared headline parser/helper 추가
- pipeline에서 raw headline과 analysis headline을 분리
- contract/article preflight, headline quality, hold-profile에 parser 결과 반영
- 테스트 및 run-summary 갱신

이번 run은 새 외부 소스 추가, 배포/주문 경로, 비밀정보, `deploy/`는 건드리지 않는다.

## Design

### 1. Analysis headline parsing

- raw headline은 그대로 보존하고 로깅도 raw 기준 유지
- analysis용 helper는 다음을 수행한다.
  - 대괄호 기사 태그 (`[클릭 e종목]`, `[카드]`, `[특징주]` 등) 제거
  - 과도한 따옴표/구두점 정리
  - 리포트/브로커리지/코멘터리 성격 플래그 추출
  - direct disclosure-like title은 손상 없이 유지

### 2. Decision tightening

- 버킷 분류는 raw title 기준을 유지한다.
- 이유:
  - 현재 raw title에는 `KB증권`, `[클릭 e종목]` 같은 IGNORE 보호막이 이미 걸려 있다.
  - 이 레이어까지 정규화해버리면 broker/article 제목이 오히려 POS bucket으로 되살아날 수 있다.
- 대신 contract preflight, article penalty, headline quality, hold-profile에만 analysis headline을 사용한다.
- 기사형/리포트형 contract headline은 parser flag를 이용해 더 일찍 `article-style contract`로 판정한다.
- headline quality penalty도 정규화된 analysis headline 기준으로 계산해 raw 포맷 노이즈에 덜 흔들리게 한다.

### 3. Hold-profile alignment

- `get_max_hold_minutes()` 입력도 analysis headline 기준으로 맞춰 downstream semantics를 일관화한다.
- raw headline 로깅은 유지해서 운영자가 원문을 그대로 볼 수 있게 한다.

## Observability

- normalization이 raw와 달라질 때만 debug/info 로그를 남긴다.
- event/decision log schema는 유지한다.

## Validation

- `tests/test_decision.py`
- `tests/test_guardrails.py`
- `tests/test_pipeline.py`
- `python3 -m compileall src/kindshot tests scripts`
- `.venv/bin/python -m pytest -q`

## Rollback

- parser helper, pipeline wiring, 해당 테스트를 되돌리면 된다.
- raw log schema와 deploy path는 유지되므로 rollback blast radius는 좁다.
