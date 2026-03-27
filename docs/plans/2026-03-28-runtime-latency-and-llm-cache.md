# 2026-03-28 Runtime Latency And LLM Cache

## Intent

배포된 Kindshot 런타임에서 뉴스 수신부터 분석, 리스크 체크, 주문 시도까지의 지연을 계측하고, 실제 병목을 식별할 수 있는 운영 surface 를 만들며, 재시작 후에도 살아남는 LLM 캐시로 반복 비용을 줄인다.

## Problem

- 현재 런타임은 `Pipeline total` 로그와 평균 LLM 지연 정도만 보여줘 stage별 병목을 판단하기 어렵다.
- `DecisionEngine` 의 캐시는 프로세스 메모리 안에서만 유지되어 재시작 뒤에는 반복 프롬프트가 다시 비용을 쓴다.
- 주문 직전까지는 대부분 sequential path 인데, 어떤 구간이 느린지 구조화된 증거가 없다.

## Scope

1. 뉴스 수신→파이프라인 완료까지의 end-to-end latency 를 계측한다.
2. `context_card`, LLM, guardrail, order-attempt 단계별 latency 를 구조화해 recent summary 로 expose 한다.
3. 최근 런타임 로그에서 stage별 병목과 캐시 효율을 요약하는 로컬 리포트 명령을 추가한다.
4. 동일한 의사결정 프롬프트는 bounded TTL 안에서 persistent cache 로 재사용한다.
5. 테스트, commit/push, remote deploy/검증까지 수행한다.

## Design

### Latency Model

- 파이프라인 내부에 per-event latency collector 를 둔다.
- 최소 측정 대상:
  - `news_to_pipeline_ms`: `detected_at` 기준 event ingest 이후 pipeline 완료 시점
  - `context_card_ms`
  - `decision_total_ms`
  - `guardrail_ms`
  - `order_attempt_ms`
  - `pipeline_total_ms`
- BUY/ORDER path 가 아니면 `order_attempt_ms` 는 비워 두고, aggregate 는 available samples 기준으로 계산한다.

### Runtime Surface

- `HealthState` 에 recent latency/caching snapshot 을 연결한다.
- health payload 는 평균만이 아니라 최근 표본 수, avg, max, p95 수준의 compact summary 를 노출한다.
- cache hit/miss/persisted hit 수를 함께 노출해 비용 절감 효과를 바로 확인할 수 있게 한다.

### Profiling Report

- `scripts/runtime_latency_report.py` 를 추가한다.
- 최근 runtime JSONL 또는 profiling artifact 를 읽어:
  - total/stage avg,
  - p95,
  - slowest stage ranking,
  - cache summary,
  - no-data/coverage warning
  를 출력한다.

### LLM Cache

- 기존 memory cache 와 in-flight dedup 은 유지한다.
- 추가로 disk-backed cache store 를 `data/runtime/` 아래에 둔다.
- key 는 prompt semantic input 을 기준으로 정규화해 생성하고, value 는 parsed `DecisionRecord` 에 필요한 최소 필드만 저장한다.
- TTL 초과, 파일 손상, parse 실패는 fail-open 으로 처리한다.
- persistent hit 은 `decision_source="CACHE"` 를 유지하되 stats 로 memory/disk hit 를 분리한다.

### Narrow Optimizations

- 이미 있는 coarse warning 을 recent structured profiling 으로 대체/보강한다.
- context-card stage 에서 캐시 hit 경로와 miss 경로를 분리해 실제 병목을 확인한다.
- order latency 측정은 `OrderExecutor.buy_market()` 결과를 바꾸지 않고 elapsed time 만 추가로 캡처한다.
- broad refactor 대신 stage-local helper 추가로 변경 범위를 좁힌다.

## Validation

- `python3 -m compileall src scripts tests`
- `.venv/bin/python -m pytest tests/test_decision.py tests/test_health.py tests/test_pipeline.py -q`
- `.venv/bin/python -m pytest -q`
- `.venv/bin/python scripts/runtime_latency_report.py`
- affected file diagnostics 0
- remote deploy 후:
  - `systemctl is-active kindshot`
  - `systemctl is-active kindshot-dashboard`
  - `curl -sf http://127.0.0.1:8080/health`

## Rollback

- 이전 known-good commit 을 clean export/rsync 하여 `/opt/kindshot` 에 덮어쓴 뒤 `.venv` 재설치와 서비스 restart 를 수행한다.
