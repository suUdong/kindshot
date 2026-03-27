# Kindshot 장중 로그 분석 — 2026-03-27 (오전 세션)

## 1. 요약

| 항목 | 값 |
|------|-----|
| 분석 구간 | 08:00 ~ 09:25 KST |
| 서비스 상태 | active (running), PID 95787 |
| 이벤트 수신 | 148건 (heartbeat 기준) |
| LLM 엔진 | NVIDIA NIM (100% 성공, 28/28 요청) |
| Anthropic | 미사용 (크레딧 소진 상태) |
| PAPER BUY | 4건 |
| PAPER SKIP | 7건 |
| PAPER STALE EXIT | 4건 (전원 0.00%) |
| 에러 | 0건 |

## 2. BUY 시그널 목록

| # | 시각 | 종목코드 | 최종 conf | hint | 소스 | 결과 |
|---|------|----------|-----------|------|------|------|
| 1 | 08:31 | 002990 | 78 | M: 단일판매ㆍ공급계약체결, 중형주, 미반영 | LLM | STALE EXIT 0.00% (t+5m) |
| 2 | 08:36 | 070300 | 80 | L: 흑자전환, 실적 반등 | LLM | STALE EXIT 0.00% (t+5m) |
| 3 | 08:36 | 001680 | 78 | M: 기보, 우리은행과 고성장 기술기업 금융지원 업무협약 체결 | LLM | STALE EXIT 0.00% (t+5m) |
| 4 | 08:38 | 068270 | 78 | M: rule_fallback:품목허가 승인 | rule_fallback | STALE EXIT 0.00% (t+5m) |

**결과: 4건 전원 STALE EXIT (0.00%) — 모멘텀 부재로 5분 내 무변동 퇴장.**

## 3. Confidence 분포

### 3.1 LLM 원본 confidence → 최종 confidence

| 종목 | LLM원본 | Dorg | ADV | Tech | Trend | Vol | Floor | 최종 |
|------|---------|------|-----|------|-------|-----|-------|------|
| 456010 | 85 | -5 | — | -5 | — | — | — | 75 (SKIP: LOW_CONFIDENCE) |
| 002990 | 80 | — | — | -2 | — | — | floor→78 | **78** |
| 070300 | 85 | -5 | — | -2 | — | — | — | **80** |
| 001680 | 80 | -5 | — | — | — | — | floor→78 | **78** |
| 068270 | 82→50(LLM)→82(fallback) | -5 | +3 | -3 | — | — | floor→78 | **78** |
| 298380 | 92→82(article) | -5 | +3 | -5 | — | -3 | floor→82 | **82** (SKIP처리 불명) |
| 068760 | 82(fallback) | -5 | — | -3 | -5 | — | floor→78 | **78** (SKIP처리 불명) |
| 001260 | 82 | — | — | -5 | — | — | floor→78 | **78** (SKIP처리 불명) |

### 3.2 최종 confidence 분포

- **78**: 5건 (62.5%) — min_buy_confidence floor 발동
- **80**: 1건 (12.5%)
- **82**: 1건 (12.5%)
- **75**: 1건 (12.5%) — SKIP됨

**핵심 관찰: BUY 4건 중 3건이 conf=78 (min_buy_confidence floor). 자연 도달이 아닌 floor 보정에 의한 진입.**

## 4. min_buy_confidence Floor 작동 분석

### Floor 발동 패턴

| 종목 | 발동 유형 | 감점 전 | floor 적용 후 | 사유 |
|------|-----------|---------|---------------|------|
| 002990 | Min-confidence floor | 73 → 78 | 78 | llm_original=80, preserving min_buy_confidence |
| 001680 | Min-confidence floor | 73 → 78 | 78 | llm_original=80, preserving min_buy_confidence |
| 068270 | Min-confidence floor | 77 → 78 | 78 | llm_original=82, preserving min_buy_confidence |
| 298380 | Confidence adj floor | 72 → 82 | 82 | total_delta=-20 exceeded -10 cap, llm=92 |
| 068760 | Confidence adj floor | 69 → 78 | 78 | total_delta=-13 exceeded -10 cap, llm=82 |
| 001260 | Confidence adj floor | 69 → 78 | 78 | total_delta=-13 exceeded -10 cap, llm=82 |

**8건 중 6건(75%)에서 floor 발동. 두 가지 floor 메커니즘 확인:**
1. **Min-confidence floor**: 감점 후 min_buy_confidence(78) 아래로 내려가면 78로 올림
2. **Confidence adj floor**: 총 감점이 -10 cap 초과 시 llm_original - 10으로 복원

## 5. 감점 빈도 분석

### 5.1 감점 유형별 빈도

| 감점 유형 | 발동 횟수 | 평균 감점 | 감점 범위 |
|-----------|-----------|-----------|-----------|
| **Dorg (통신사)** | 7/8건 (87.5%) | -5.0 | -5 고정 |
| **Technical (MACD/BB/ATR)** | 6/8건 (75.0%) | -3.5 | -2 ~ -5 |
| **Trend (3일수익/20일양봉)** | 1/8건 (12.5%) | -5.0 | -5 |
| **ADV (거래대금 보정)** | 2/8건 (25.0%) | +3.0 | +3 (상향) |
| **Volume (거래량)** | 1/8건 (12.5%) | -3.0 | -3 |

### 5.2 Technical indicator 상세

| 종목 | RSI | MACD | BB% | ATR% | 감점 |
|------|-----|------|-----|------|------|
| 456010 | 42.6 | -59.55 | 32.5 | 6.97% | -5 |
| 002990 | 56.6 | 12.81 | 60.3 | 6.79% | -2 |
| 070300 | 70.5 | 32.79 | 80.7 | 10.27% | -2 |
| 001680 | — | — | — | — | 0 (미발동) |
| 068270 | 43.1 | -622.54 | 39.3 | 4.21% | -3 |
| 298380 | 42.0 | -716.06 | 42.0 | 6.41% | -5 |
| 068760 | 33.3 | -340.96 | 22.8 | 3.53% | -3 |
| 001260 | 60.6 | -5.55 | 57.0 | 5.35% | -5 |

**관찰: MACD 음수 + BB <50% 조합에서 감점 -3~-5. RSI <45 구간 집중.**

## 6. SKIP 사유 분석

### Pipeline 단계별 필터링

| SKIP 단계 | 건수 | 사유 |
|-----------|------|------|
| BUCKET (사전필터) | ~13건 | IGNORE_BUCKET(7), NEG_BUCKET(2), UNKNOWN_BUCKET(3), CORRECTION_EVENT(3) |
| FEED (피드필터) | ~8건 | EMPTY_TICKER(8) |
| QUANT | 1건 | ADV_TOO_LOW |
| GUARDRAIL | 1건 | LOW_CONFIDENCE |
| LLM SKIP | 7건 | conf=50 (기사/미확정/이미반영) |

### LLM SKIP 상세

| 시각 | 종목 | 사유 |
|------|------|------|
| 08:07 | 005180 | 기사, 미확정 |
| 08:09 | 011780 | 기사, 미확정 |
| 08:17 | 139130 | 기사 아님 공시, 자본준비금 이익잉여금 전환 |
| 08:31 | 089860 | 대형주, 이미 반영 |
| 08:33 | 032640 | 기사, 미확정 |
| 08:37 | 068270 | 기사, 미확정 공시 아님 (→ rule_fallback로 BUY 전환) |
| 08:48 | 017810 | 기사 아님 공시, 금액 미상 |

## 7. LLM-fallback Hybrid 작동

| 종목 | LLM 판단 | Fallback 사유 | Fallback conf |
|------|----------|---------------|---------------|
| 068270 | conf=50 (SKIP) | rule_fallback:품목허가 승인 | 82 → 최종 78 |
| 068760 | conf=50 (SKIP) | rule_fallback:허가 획득 | 82 → 최종 78 |

**2건에서 LLM이 SKIP했으나 rule_fallback이 오버라이드하여 BUY 판단.**
068270은 실제 BUY 진입, 068760은 후속 처리 불명.

## 8. 핵심 인사이트 & 개선 제안

### 긍정적
1. **NVIDIA API 100% 성공** (28/28) — 3/26 Anthropic 소진 이후 안정적 대체
2. **Floor 메커니즘 정상 작동** — 과도한 감점 방지 (6건 발동)
3. **Rule-fallback 하이브리드** 작동 — LLM SKIP을 rule이 보완 (2건)
4. **에러 0건** — 파이프라인 안정

### 우려사항
1. **전원 STALE EXIT (0.00%)** — BUY 4건 모두 5분 내 가격 변동 없음. 모멘텀 없는 장세
2. **conf=78 쏠림** — BUY 4건 중 3건이 floor 최저치. 진정한 고확신 시그널 부재
3. **Dorg 감점 87.5%** — 거의 모든 이벤트가 비1차 통신사에서 유입, -5 고정 감점
4. **기술적 약세장** — RSI <50, MACD 음수, BB <50% 다수 → 시장 전반 약세

### 개선 제안
1. **STALE EXIT 임계값 재검토**: 0.00% 전원 무변동은 장세 문제이나, stale 기준(5분)이 너무 짧을 수 있음
2. **conf=78 floor 진입 재고**: floor에 의한 기계적 진입이 승률을 낮출 가능성 → floor 진입 시 추가 조건(volume spike 등) 검토
3. **약세장 글로벌 필터**: RSI/BB/MACD 다수 약세 시 전체 BUY 억제 모드 고려
