# 2026-03-28 News Signal Observability

## Goal

배포된 `news_signal` semantic enrichment 가 실제로 잘 찍히는지 다음 시장 세션에서 운영자가 바로 확인할 수 있도록 dashboard reader surface 를 보강한다.

## Hypothesis

`event` / `context_card` 에 이미 기록되는 `news_signal` 필드를 dashboard/data-loader 에서 flatten 해서 요약 카드와 상세 테이블로 노출하면, 다음 live paper session 에서 numeric extraction / cluster / impact score 품질을 빠르게 검증할 수 있다.

## Scope

- `dashboard/data_loader.py` 에 `news_signal` flattening 추가
- 시그널 현황 탭에 semantic signal summary 추가
- 관련 단위 테스트 추가

이번 slice 는 runtime producer, deploy scripts, secrets, live-order behavior 를 바꾸지 않는다.

## Design

### 1. Flatten once in reader layer

- `load_events()` 와 `load_context_cards()` 에서 nested `news_signal` 을 평탄화한다.
- 예시 필드:
  - `impact_score`
  - `contract_amount_eok`
  - `revenue_eok`
  - `operating_profit_eok`
  - `sales_ratio_pct`
  - `cluster_size`
  - `cluster_id`
  - `direct_disclosure`
  - `commentary`

### 2. Dashboard tab update

- 기존 `시그널 현황` 탭 안에 semantic summary block 을 추가한다.
- 노출 항목:
  - semantic-enriched event count
  - average / max impact score
  - numeric extraction coverage
  - top-impact recent headlines table

### 3. Validation

- `tests/test_dashboard.py` reader tests
- dashboard import/compile validation

## Rollback

- dashboard reader/app 변경만 되돌리면 된다.
