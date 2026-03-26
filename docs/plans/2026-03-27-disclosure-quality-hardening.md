# 2026-03-27 Disclosure Quality Hardening

## Goal

KIS/KIND 병행 수집 이후 실제 공시와 기사형 노이즈가 함께 유입되면서, 같은 이벤트의 이중 처리와 저품질 헤드라인 BUY가 수익성/운영 가독성을 동시에 해치고 있다. 이번 slice의 목표는 새 데이터 소스를 추가하지 않고, 기존 뉴스 파이프라인에서 **중복과 저신호 이벤트를 더 일찍 제거**하는 것이다.

## Hypothesis

아래 세 가지를 함께 적용하면 진짜 공시를 유지하면서 저품질 진입 후보를 줄일 수 있다.

- KIS/KIND 간 동일 공시는 cross-source content hash로 한 번만 처리한다.
- KIS disclosure poller는 기관/외인 수급, 차트 패턴, 시황/테마 기사 같은 비공시성 제목을 더 적극적으로 제외한다.
- LLM이 BUY를 준 뒤에도 헤드라인 자체가 짧거나 추측성이고, 계약/수주 기사에 금액이 없으면 confidence를 추가 감점한다.

## Scope

- `src/kindshot/event_registry.py`
  - 기존 `event_id` dedup을 유지한 채 cross-source 전용 보조 dedup 추가
- `src/kindshot/feed.py`
  - KIS disclosure keyword/noise pattern 세트 확장
- `src/kindshot/guardrails.py`
  - 헤드라인 품질 감점 helper 추가
- `src/kindshot/pipeline.py`
  - post-LLM confidence adjustment 단계에 헤드라인 품질 감점 연결

이번 run은 주문 실행, 배포 경로, 비밀정보 처리, `deploy/` 경로를 건드리지 않는다.

## Design

### 1. Cross-source dedup

- 기존 `event_id` dedup은 그대로 유지한다.
- 추가 dedup은 `ticker + normalized title` 기반 content hash를 써서 KIS와 KIND 사이에서만 적용한다.
- 같은 소스 안에서는 다른 `rcpNo`/`news_id`를 가진 이벤트를 content hash로 제거하지 않는다.
- 일자 TTL이 바뀌면 seen-id/history와 함께 content-hash state도 초기화한다.

### 2. KIS noise filter hardening

- 비공시성 수급 기사, 차트 패턴 기사, 시황/테마 기사 패턴을 noise set에 추가한다.
- 반대로 MOU, 무상증자, 관리종목, 자금조달 같은 corporate-action 성격 키워드는 disclosure set에 추가한다.
- 거래소/금감원 source bypass는 유지해서 source-level disclosure는 계속 통과시킨다.

### 3. Headline quality penalty

- BUY decision에 대해서만 적용한다.
- 짧은 제목, 물음표 포함 제목, 금액 없는 계약/수주 제목에 대해 deterministic penalty를 준다.
- 기존 article-pattern penalty 및 downstream confidence floor와 공존하게 하여 과도한 누적 감점은 막는다.

## Observability

- pipeline log에 `Headline quality adj [...]`를 남겨 후속 로그 분석에서 penalty cohort를 분리할 수 있게 한다.
- cross-source dedup은 debug log만 남기고 동작 자체는 기존 duplicate skip과 같은 형태로 처리한다.
- KIS noise filter 확장은 기존 poll stats(`noise_filtered`)에 자연스럽게 포함시킨다.

## Validation

- `tests/test_event_registry.py`
  - cross-source dedup
  - same-source title preservation
  - new-day TTL reset
- `tests/test_feed.py`
  - 기관/외인 수급 noise
  - 차트 패턴 noise
  - 테마/관련주 noise
  - 신규 disclosure keyword pass-through
- `tests/test_guardrails.py`
  - short/question/amount-missing headline penalties

실행 검증:

- `.venv/bin/python -m pytest tests/test_event_registry.py tests/test_feed.py tests/test_guardrails.py -q`
- `.venv/bin/python -m pytest -q`

## Rollback

- `event_registry.py`, `feed.py`, `guardrails.py`, `pipeline.py`와 해당 테스트만 되돌리면 된다.
- 변경은 read/evaluate path에만 걸려 있으므로 주문 실행 및 배포 경로 영향은 없다.
