# kindshot — KRX 뉴스 드리븐 데이 트레이딩 MVP 설계서

> **프로젝트**: `kindshot` (KIND + 1-shot)
> **버전**: v0.1.2 (MVP 최종 — 구현 레디)
> **날짜**: 2026-03-05
> **핵심 철학**: 헤비 엔진이 아니라, **이벤트 파이프 + 하드 가드레일 + 1샷 LLM**

---

## 1. 시스템 개요

### 1-1. 기존 설계와의 차이

기존 "5차원 스코어링 → 신중한 진입" 엔진을 **뉴스 트리거 → 퀀트 3초 체크 → 즉시 진입 → 기계적 청산** 구조로 전환한다. 데이 트레이딩에서 뉴스(공시)라는 명확한 촉매를 기반으로, 최소한의 퀀트 필터만 적용해 빠르게 판단하고 빠지는 것이 목표다.

| 항목 | 기존 설계 | MVP |
|------|----------|-----|
| 진입 근거 | 5차원 스코어링 | 뉴스 트리거 + 퀀트 3초 체크 |
| 시장 환경 판단 | 6개 지표 점수 합산 | KOSPI -1% 이상 급락 시 매매 중단 |
| 종목 진입 | 스코어 65~75점 임계값 | LLM 1샷 BUY/SKIP + 가드레일 |
| LLM 역할 | 뉴스 해석 + 패턴 예외 + 종합 판단 + 리뷰 | Decision Engine 1회 호출만 |
| 진입 전략 | 3가지 (즉시/지지선대기/분할) | 시장가 즉시 진입 |
| 청산 | 트레일링 스탑 + 조건변경 청산 | 고정 목표가/손절가 + 15:20 강제 청산 |

### 1-2. MVP 스코프

```
KIND RSS 폴링 → 키워드 버킷팅 → 퀀트 3초 체크 → (POS_STRONG만) LLM 1샷 → 로그 저장 → 가격 스냅샷 큐
```

**MVP에서 하는 것:**
- KIND RSS 실시간 폴링 (장중 2~5초, 적응형 + jitter)
- 키워드 기반 5버킷 분류 (NEG 우선 override)
- 퀀트 3초 체크 (유동성/마찰비용/극단과열)
- POS_STRONG + 퀀트 통과 공시에 대해 LLM 1샷 호출 (BUY/SKIP)
- JSONL 로그 저장 (이벤트/결정/가격스냅샷)
- 사후 시뮬레이션용 가격 스냅샷 수집 (t0, t+1m, t+5m, t+30m, close)

**MVP에서 안 하는 것:**
- 실제 주문 실행 (시뮬레이션만)
- KIS WebSocket 실시간 체결 스트림
- 가격 확인 트리거 (Nmin/Vmin 증거 기반 윈도우)
- 트레일링 스탑 / 분할 매도

### 1-3. MVP KPI (2~3주 후 판정)

| KPI | 계산 방법 | 의미 |
|-----|----------|------|
| coverage | POS_STRONG 이벤트 중 LLM 호출 비율 | 퀀트 필터 적정성 |
| latency p95 | detected_at → decided_at | 시스템이 "수초"를 지키는지 |
| outcome mean/median | BUY 신호의 ret_1m~ret_close | 신호 품질 |
| noise_flip_ratio | ret_1m > 0인데 ret_5m < 0인 비율 | 진입 타이밍 문제 |
| bucket_accuracy | 수동 샘플 검증 (50~100건) | 키워드 버킷 정확도 |

---

## 2. 아키텍처

```
┌──────────────────────────────────────────────────────────────────┐
│                       kindshot MVP 파이프라인                       │
│                                                                    │
│  ┌────────────┐   ┌────────────┐   ┌────────────┐   ┌─────────┐ │
│  │ News Feed  │──▶│  Trigger   │──▶│ Quant 3s   │──▶│Decision │ │
│  │ (KIND RSS) │   │  Engine    │   │  Check     │   │ Engine  │ │
│  │ 적응형 폴링 │   │ 키워드분류  │   │ 유동성/    │   │ LLM 1샷 │ │
│  │ +jitter    │   │ NEG우선    │   │ 마찰/과열  │   │         │ │
│  └────────────┘   └────────────┘   └────────────┘   └─────────┘ │
│                                                                    │
│               ┌─────────────────────────────────────────┐         │
│               │              Logger (JSONL)              │         │
│               │  event + decision + price_snapshot       │         │
│               └─────────────────────────────────────────┘         │
│                              ▲                                     │
│  ┌────────────┐              │                                     │
│  │Price Fetch │──────────────┘                                     │
│  │(KIS REST)  │ t0, t+1m, t+5m, t+30m, close                      │
│  └────────────┘                                                    │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. News Feed

- KIND RSS HTTP 폴링 + ETag/If-Modified-Since
- 장중 2~5초, 장외 10~30초, jitter ±20%
- 304 응답 → 파싱 스킵
- disclosed_at 누락 시 detected_at으로 대체 (delay_ms = null)

### 중복 제거

```
event_id 생성:
  1순위: RSS item link 내 고유 키(UID) → hash(kind_uid)
  2순위: fallback → hash(source + disclosed_at + ticker + normalized_title + link)
  disclosed_at_missing 시: rss_guid 또는 detected_at으로 대체
```

운영 보강:
- 기본 dedup은 여전히 `event_id` 기준이다.
- 다만 같은 ticker에서 짧은 시간창 안에 비슷한 제목이 연속 유입되는 경우가 많아, 운영 로그 검토용 1차 보강으로 `ticker + short time window + high title overlap` 보조 dedup을 허용한다.
- 이 보조 dedup은 correction/withdrawal 매칭과 분리해서 ORIGINAL 기사 burst에만 적용한다.
- 목적은 KIS/KIND 동시 유입이나 증권사 리포트 재가공 다발 기사로 인한 review/LLM 중복 부담을 줄이는 것이지, 서로 다른 실질 이벤트를 넓게 합치는 것이 아니다.
- 첫 슬라이스에서는 보수적으로 같은 종목, 같은 당일, 10분 내, 높은 토큰 중첩일 때만 duplicate로 처리하고 event-group 통합은 후속 단계로 남긴다.

### 정정공시 처리

- title에 "정정" / "[정정]" → CORRECTION
- title에 "철회" / "취소" / "정정(취소)" → WITHDRAWAL
- parent_id: 같은 ticker + 당일 내 + base_title 유사도 최고 (MVP: 당일 TTL)
- event_group_id: parent_id가 있으면 parent_id, 없으면 self

---

## 4. Trigger Engine — 키워드 버킷팅 + 퀀트 3초 체크

### 5버킷 분류

| 버킷 | LLM 호출 | 가격 기록 |
|------|----------|----------|
| POS_STRONG | O | O |
| POS_WEAK | X | X (MVP) |
| NEG_STRONG | X | O (analysis_tag="SHORT_WATCH") |
| NEG_WEAK | X | X (MVP) |
| UNKNOWN | X | X (MVP) |

**NEG 우선 override**: NEG_STRONG 키워드가 하나라도 있으면 무조건 NEG_STRONG

참고:
- 위 표는 현재 MVP 동작 기준이다.
- `UNKNOWN`에 대한 LLM 보조분류, 별도 저장소, 실시간 승격, 사후 검증 로그 설계는 [docs/plans/2026-03-15-unknown-llm-review-loop.md](/home/wdsr88/workspace/kindshot/docs/plans/2026-03-15-unknown-llm-review-loop.md)에 분리해 정리한다.

### 퀀트 3초 체크 (POS_STRONG만)

1. 유동성: adv_value_20d >= 50억
2. 마찰비용: spread_bps <= 25 (SPREAD_CHECK_ENABLED=false면 skip)
3. 극단과열: abs(ret_today_vs_prev_close) <= 20%

Quant fail 중 10% 랜덤 샘플링 → price_snapshot 기록 (QUANT_FAIL_SAMPLE)

운영 메모:
- `spread_bps`는 KIS 현재가 + 호가 스냅샷 조합에서 계산한다.
- 장중 연속매매 시간(09:00~15:30 KST) 밖에서 호가가 비어 있으면 매매는 계속 fail-close로 막되, 일반 결측과 섞지 않고 `SPREAD_DATA_MISSING_OFF_HOURS`로 분리 기록한다.
- 목적은 규칙 완화가 아니라, 장전/장후 호가 부재와 실제 장중 데이터 품질 문제를 운영 로그에서 구분하는 것이다.

---

## 5. Decision Engine — LLM 1샷

12줄 고정 템플릿, JSON 출력 강제, SDK timeout 3s + asyncio.wait_for 2s

### Context Card

```
ctx_price: ret_today, ret_1d, ret_3d, pos_20d_range, gap_today
ctx_micro: adv_value_20d, spread_bps, vol_pct_20d
```

- pykrx로 히스토리 피처 배치 로드 (장 시작 전)
- KIS REST로 실시간 피처 (있으면)

### LLM 출력

```json
{"action": "BUY|SKIP", "confidence": 0-100, "size_hint": "S|M|L", "reason": "<=15 words"}
```

### 캐시

key=(ticker, headline_hash, bucket), dict+expire_ts, 5분 sweep, decision_source 로그

---

## 6. Guardrails (MVP: 인터페이스만)

절대 금지 조건 8개 (spread>25bp, adv<50억, VI/상한가, 일일손실한도, 동일종목재매수, 동일섹터2개, 포지션10%, 관리종목)

MVP에서는 인터페이스만 정의. 실매매 전 구현.

---

## 7. 로그 스키마 (JSONL)

3가지 레코드 타입: event, decision, price_snapshot
- schema_version: "0.1.2"
- run_id: 프로세스별 고유
- event_id로 조인

### price_snapshot

- t0_basis: "DECIDED_AT" | "DETECTED_AT"
- horizon: "t0" | "t+1m" | "t+5m" | "t+30m" | "close"
- ret_long_vs_t0, ret_short_vs_t0
- KIS 없으면 px=null (UNAVAILABLE)

운영 보강:
- `close`는 기본적으로 `15:30 KST + close_snapshot_delay_s`에 예약한다.
- 다만 런타임가 그 시각 직전이나 직후에 종료되면 pending `close`가 유실될 수 있으므로, 종료 시점이 이미 close fetch 가능 구간이면 pending `close`를 종료 전에 flush한다.
- 이 종료 flush는 `close` horizon에만 적용하고, 아직 만기되지 않은 `t+1m/t+5m/t+30m`를 억지로 당겨 실행하지는 않는다.

---

## 8. 시장 환경

KOSPI 장중 -1% 이상 하락 → 매매 중단. KIS 없으면 disabled.

---

## 구현 실무 참고

- KIND RSS: 실제로는 KRX 공시 페이지 스크래핑 또는 API 엔드포인트 필요 (표준 RSS 아닐 수 있음)
- pykrx: KRX 스크래핑 기반이라 불안정할 수 있음 → context_card.py에서 실패 시 graceful degradation
- KIS API 키 미보유 → 가격 조회 graceful skip (px=null)
- Anthropic API 키 보유 → LLM 호출 가능

## 프로젝트 구조

```
kindshot/
├── pyproject.toml
├── .env.example
├── .gitignore
├── src/kindshot/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── models.py
│   ├── feed.py
│   ├── event_registry.py
│   ├── bucket.py
│   ├── quant.py
│   ├── context_card.py
│   ├── decision.py
│   ├── guardrails.py
│   ├── price.py
│   ├── market.py
│   ├── logger.py
│   └── kis_client.py
├── tests/
│   ├── test_bucket.py
│   ├── test_event_registry.py
│   ├── test_quant.py
│   ├── test_guardrails.py
│   ├── test_decision.py
│   └── test_logger.py
└── logs/
```
