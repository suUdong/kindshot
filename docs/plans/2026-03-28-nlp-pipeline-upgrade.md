# 2026-03-28 NLP Pipeline Upgrade

## Goal

Kindshot 뉴스 파이프라인에 구조화 semantic enrichment 를 추가해 headline 기반 해석을 강화한다. 이번 slice 는 다음 네 가지 요구를 하나의 가설로 묶는다.

- 뉴스 핵심 수치 자동 추출
- 종목 연관 뉴스 클러스터링
- 뉴스 임팩트 스코어 계산
- 검증 후 commit/push/deploy

## Hypothesis

headline 에서 계약금액/매출/영업이익 같은 핵심 수치와 동일 종목 연관 뉴스 클러스터 컨텍스트를 계산한 뒤, 이를 impact score 로 요약해 event log 와 decision prompt 에 주입하면 기사형 noise 는 더 낮게, 강한 직접공시 촉매는 더 높게 평가할 수 있다.

## Current State

- `headline_parser.py` 는 기사형/공시형 판별을 제공한다.
- `decision.py` 는 `contract_amount_eok` 만 별도 파싱해 prompt 에 넣고 있다.
- `pipeline.py` 는 `news_category` 기반 보정만 하고 있어, 숫자 magnitude 와 동일 종목 corroboration 은 거의 반영하지 못한다.
- runtime artifacts 와 trade DB 는 additive field 확장을 수용할 수 있다.

## Design

### 1. Shared semantic enrichment module

- 새 공용 모듈에서 headline 기반 semantic facts 를 계산한다.
- 출력 필드:
  - `contract_amount_eok`
  - `revenue_eok`
  - `operating_profit_eok`
  - `sales_ratio_pct`
  - `cluster_id`
  - `cluster_size`
  - `cluster_minutes_since_first`
  - `impact_score`
  - `impact_factors`
- 숫자 파싱은 외부 API 없이 `조`/`억` headline 패턴만 지원한다.

### 2. Per-ticker related-news clustering

- 외부 embedding 없이 결정적 휴리스틱을 사용한다.
- cluster key = `ticker + news_category + normalized semantic subject`.
- semantic subject 는 normalized headline 에서 회사명/티커/숫자/단순 불용구를 제거한 토큰으로 구성한다.
- runtime window 안에서 동일 key 가 반복되면 cluster size 와 corroboration flag 가 증가한다.
- 다른 ticker 는 절대 같은 cluster 에 넣지 않는다.

### 3. Impact score

- 0~100 bounded deterministic score.
- 기본 구성:
  - direct disclosure / commentary 여부
  - news category base weight
  - parsed numeric magnitude
  - sales ratio if present
  - cluster corroboration
  - broker/commentary/article penalty
- 목적은 독립적인 점수 surface 제공 + small confidence shaping.
- confidence adjustment 는 새 리스크를 만들지 않도록 소폭 범위만 허용한다.

### 4. Pipeline integration

- `decision.py` 의 개별 `contract_amount_eok` 파싱을 공용 enrichment 로 대체한다.
- `pipeline.py` 는 event record 와 runtime context-card 에 enrichment 결과를 남긴다.
- impact score 는 prompt `ctx_signal` 과 event record 에 추가한다.
- `trade_db.py` 는 additive columns 로 핵심 일부를 저장한다.

## Logging / Observability

- raw headline 는 유지한다.
- 새 구조화 필드는 additive 로만 기록한다.
- impact factors 는 compact list/string 로 남겨 분석 시 재사용 가능하게 한다.

## Validation

- unit tests for parsing, clustering, scoring
- pipeline regression tests
- full pytest
- compileall + diagnostics
- push + remote deploy + health/dashboard smoke

## Rollback

- 새 semantic enrichment module 과 관련 additive wiring revert
- 이전 known-good tree 를 `/opt/kindshot` 에 재동기화하고 remote reinstall/restart

## Risks

- headline 만으로는 매출/영업이익 문맥 오탐 가능성이 있다.
- overly aggressive impact boost 는 false positive 를 키울 수 있으므로 confidence adjustment 는 bounded 로 제한해야 한다.
- cluster state 는 runtime-local 이므로 cross-process durable clustering 은 이번 범위 밖이다.
