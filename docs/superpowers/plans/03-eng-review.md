# Engineering Review: Kindshot 아키텍처 및 코드 검토

**날짜:** 2026-03-23
**프로젝트:** Kindshot v0.1.3
**브랜치:** main
**코드베이스:** ~9,500 LOC, 28 Python 모듈

---

## System Audit

### 코드베이스 건강도

| 지표 | 값 | 판정 |
|------|-----|------|
| 총 LOC | 9,553 | 1인 프로젝트 치고 큼 |
| 모듈 수 | 28 (src) + 27 (tests) | 적절 |
| 가장 큰 파일 | collector.py (1,235), replay.py (1,227), main.py (1,194) | main.py 분리 필요 |
| 테스트 수집 | 2/27 파일 성공 (25 collection errors) | **CRITICAL** |
| 최근 30일 커밋 | 30+ | 활발 |
| Python 버전 | >=3.11 | 적절 |
| 의존성 수 | 7 (runtime) + 3 (dev) | 경량, 좋음 |

### 아키텍처 다이어그램 (현재)

```
                    ┌─────────────┐
                    │  __main__.py │ CLI entrypoint
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │   main.py   │ Supervisor (1,194 LOC!)
                    │  - run()    │
                    │  - process()│
                    │  - watchdog │
                    │  - shutdown │
                    └──────┬──────┘
                           │
          ┌────────────────┼────────────────┐
          │                │                │
    ┌─────▼────┐    ┌─────▼─────┐   ┌──────▼──────┐
    │ feed.py  │    │ bucket.py │   │ decision.py │
    │ KIS/KIND │    │ 6-bucket  │   │ LLM 1-shot  │
    │ polling  │    │ classify  │   │ BUY/SKIP    │
    └──────────┘    └───────────┘   └─────────────┘
          │                                │
          │         ┌───────────┐          │
          │         │ quant.py  │          │
          │         │ ADV/spread│          │
          │         └───────────┘          │
          │                                │
    ┌─────▼────────────────────────────────▼───┐
    │           guardrails.py                   │
    │  daily loss / position / chase buy / time │
    └──────────────────┬───────────────────────┘
                       │
              ┌────────▼────────┐
              │    price.py     │
              │  TP/SL/Trailing │
              └─────────────────┘

    Side systems:
    ┌──────────────┐  ┌───────────────┐  ┌──────────────┐
    │ collector.py │  │ replay.py     │  │unknown_review│
    │ backfill     │  │ simulation    │  │ batch LLM    │
    └──────────────┘  └───────────────┘  └──────────────┘
```

---

## 1. Architecture Review

### 1.1 main.py God Object 문제 (HIGH)

`main.py`가 1,194 LOC로, 다음 책임을 모두 담당:
- CLI argument parsing
- asyncio supervisor loop
- 이벤트 처리 파이프라인 (process_one)
- Watchdog 태스크
- Graceful shutdown
- RuntimeCounters 관리
- UNKNOWN headline logging

**문제:** 단일 파일 변경 시 전체 파이프라인에 영향. 테스트 격리 불가능.

**권장:** 3개로 분리
```
main.py      → CLI + supervisor (200 LOC)
pipeline.py  → process_one + 이벤트 처리 (400 LOC)
runtime.py   → counters, watchdog, shutdown (300 LOC)
```

### 1.2 Config God Object (MEDIUM)

`config.py`의 Config 클래스가 137개 필드를 가진 frozen dataclass. 모든 모듈이 전체 Config를 받아야 하므로 의존성이 모호.

**권장:** 당장 분리할 필요는 없으나, 새 모듈 추가 시 필요한 config subset만 받도록 인터페이스 설계 고려.

### 1.3 LLM 의존성 관리 (GOOD)

`llm_client.py` 추출이 잘 되었다:
- Semaphore 기반 동시성 제어
- Exponential backoff with rate limit 감지
- 타임아웃 이중 안전망 (asyncio.wait_for + SDK timeout)

### 1.4 에러 처리 계층 (GOOD)

`errors.py`에 도메인별 예외 계층이 잘 정의됨:
- FeedError, KisApiError, KisTokenError, LlmError, QuotaError 등

### 1.5 데이터 흐름 단방향성 (GOOD)

파이프라인이 깔끔한 단방향: Feed → Bucket → Quant → Decision → Guardrail → Price
사이클이나 양방향 의존성 없음.

---

## 2. Code Quality Review

### 2.1 테스트 Collection Errors (CRITICAL)

```
pytest: 25 errors during collection, 2 tests collected
```

이것은 **가장 시급한 문제**. 테스트가 실행조차 안 되므로:
- 리팩토링 시 회귀 감지 불가
- 새 기능 추가 시 기존 동작 보장 불가
- CI/CD가 무의미 (통과=검증이 아님)

**원인 추정:** 모듈 임포트 실패 (의존성 미설치, 환경 차이)

**권장:** 즉시 수정. `pytest --tb=short` 로 에러 원인 파악 후 1시간 내 해결 가능.

### 2.2 bucket.py의 키워드 목록 (MEDIUM)

498 LOC 중 대부분이 하드코딩된 한국어 키워드 리스트. 이것 자체는 문제가 아니나:
- 키워드 추가/삭제가 코드 변경 + 커밋 필요
- 테스트에서 키워드 커버리지 검증 어려움

**권장:** 키워드를 별도 YAML/JSON 파일로 외부화. 프롬프트 외부화(prompts/)와 동일한 패턴.

### 2.3 collector.py / replay.py 복잡도 (MEDIUM)

각각 1,200+ LOC. 핵심 파이프라인이 아닌 보조 도구인데 코드베이스의 26%를 차지.

**권장:** 현재 상태 동결. 새 기능 추가하지 말 것.

### 2.4 에러 처리의 일관성 (IMPROVED)

최근 커밋 `17ad024`에서 silent except 블록에 로깅을 추가한 것은 좋은 개선. 하지만 `main.py:80`에 여전히 `logger.debug("Failed to write unknown headline")`로 exception을 삼키는 패턴이 있다.

---

## 3. Test Review

### 현재 상태

| 항목 | 값 |
|------|-----|
| 테스트 파일 | 27개 |
| 수집 성공 | 2개 |
| 수집 실패 | 25개 |
| 커버리지 | **측정 불가** (collection error) |

### 우선순위별 테스트 복구

1. **P0 — 핵심 파이프라인:** test_bucket, test_decision, test_guardrails, test_quant
2. **P1 — 데이터 계층:** test_feed, test_kis_client, test_price
3. **P2 — 보조 시스템:** test_replay, test_collector, test_unknown_review
4. **P3 — 인프라:** test_health, test_config, test_models

### 누락된 테스트 시나리오

- **통합 테스트:** Feed → Bucket → Decision 전체 파이프라인 end-to-end
- **에러 경로:** LLM timeout 시 graceful degradation
- **경계값:** confidence 정확히 72 (MIN_BUY_CONFIDENCE 경계)
- **시간 기반:** no_buy_after_kst_hour 경계

---

## 4. Performance Review

### 병목 분석

```
Feed polling (3s interval)
    → Bucket classify: ~0ms (키워드 매칭)
    → Quant check: ~100ms (KIS API 호출)
    → LLM call: 2,000~12,000ms (가장 느림)
    → Guardrails: ~0ms (로컬 연산)
    → Price fetch: ~100ms (KIS API)
```

**병목:** LLM 호출 (2~12초). Semaphore가 max_concurrency=2로 제한.

**권장:**
- 현재 Haiku 모델 사용은 적절 (가장 빠르고 저렴)
- LLM 캐싱 (llm_cache_ttl_s=60) 있으나, 동일 뉴스 재처리 방지용. 실질적 캐시 히트는 낮을 것
- 동시성 2 → 3~4로 올려 처리량 개선 가능 (Anthropic rate limit 범위 내)

---

## 5. 의존성 건강도

| 패키지 | 버전 제약 | 상태 |
|--------|----------|------|
| aiohttp | >=3.9 | OK |
| feedparser | >=6.0 | OK |
| anthropic | >=0.40 | OK (pinned in requirements.lock) |
| pykrx | >=1.0 | OK |
| pydantic | >=2.0 | OK |
| python-dotenv | >=1.0 | OK |
| python-dateutil | >=2.8 | OK |

**보안 이슈:** `pip audit` 미실행. CI에 의존성 보안 스캔 추가 필요.

---

## 엔지니어링 권고 요약

| # | 우선순위 | 항목 | 노력 (CC) |
|---|---------|------|-----------|
| 1 | **P0** | 테스트 collection error 수정 | 30분 |
| 2 | **P0** | .env 파일에 실제 API 키 노출 확인 (보안) | 즉시 |
| 3 | **P1** | main.py 분리 (pipeline/runtime) | 1시간 |
| 4 | **P1** | 키워드 외부화 (YAML) | 30분 |
| 5 | **P2** | CI에 pip audit 추가 | 15분 |
| 6 | **P2** | E2E 통합 테스트 추가 | 1시간 |
| 7 | **P3** | Collector/Replay 코드 동결 | 0분 (규칙) |

---

**STATUS: DONE**
