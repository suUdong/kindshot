# Kindshot 신규 전략 딥 리서치

> 작성일: 2026-03-29 | 목적: kindshot 전략 다각화를 위한 후보 전략 리서치

## 목차
1. [현재 전략 구조 요약](#1-현재-전략-구조-요약)
2. [DART 공시 기반 전략 확장](#2-dart-공시-기반-전략-확장)
3. [수급 기반 전략](#3-수급-기반-전략)
4. [이벤트 드리븐 전략](#4-이벤트-드리븐-전략)
5. [페어 트레이딩](#5-페어-트레이딩)
6. [거시연동 전략](#6-거시연동-전략)
7. [전략별 비교 및 우선순위](#7-전략별-비교-및-우선순위)
8. [참고문헌](#8-참고문헌)

---

## 1. 현재 전략 구조 요약

### 아키텍처
```
Feed Sources → Strategy → TradeSignal → StrategyRegistry
  → consume_strategy_signals() → Guardrails → OrderExecutor → Price Tracking
```

### 활성 전략

| 전략 | 소스 | 방식 | 상태 |
|------|------|------|------|
| NewsStrategy | KIND/KIS/DART RSS | 공시/뉴스 → LLM 버킷 분류 → 매매 판단 | 주력 |
| TechnicalStrategy | KIS 시세 API | RSI/MACD/BB/MTF 폴링 | 보조 |
| Y2iFeed | y2i 시그널 파일 | YouTube 인사이트 → 종목 시그널 | 보조 |
| AnalystFeed | KIS 애널리스트 | 리포트 기반 보조 시그널 | 보조 |

### 플러그인 아키텍처
- **Feed 추가**: `Feed` 프로토콜 구현 → `_build_feed()`에 등록
- **Strategy 추가**: `Strategy` 프로토콜 구현 → `_build_strategy_registry()`에 등록
- **TradeSignal**: `SignalSource` enum에 `NEWS, TECHNICAL, Y2I, ALPHA, MACRO, COMPOSITE` 정의
- 새 전략은 기존 guardrails/confidence 조정 파이프라인을 그대로 활용 가능

---

## 2. DART 공시 기반 전략 확장

### 2.1 현재 구현 상태

kindshot의 `DartFeed`(`feed.py:592-769`)가 이미 DART `list.json` 폴링으로 당일 공시를 `RawDisclosure`로 변환 중. 그러나 현재는 **제목(report_nm) 텍스트 기반 버킷 분류만** 수행하며, DS005 구조화 데이터는 미활용.

### 2.2 DART OpenAPI 핵심 엔드포인트

**Base URL**: `https://opendart.fss.or.kr/api`

| 그룹 | API 수 | 설명 |
|------|--------|------|
| DS001 공시정보 | 4 | 공시검색, 기업개황, 원본파일, 고유번호 |
| DS002 정기보고서 | 28 | 배당, 자기주식, 최대주주, 임원보수 등 |
| DS003 재무정보 | N/A | 재무제표, 주요 재무지표 |
| DS004 지분공시 | 2 | 대량보유(5%+), 임원/주요주주 소유 |
| DS005 주요사항보고서 | 36 | 자기주식, M&A, 유증, 부도, 소송 등 |
| DS006 증권신고서 | 6 | 지분증권, 채무증권, 합병, 분할 |

### 2.3 전략별 활용 가능한 공시 이벤트

#### (A) 자사주 매입 — 강한 매수 시그널

**API**: DS005 `trsrStockAqDecsn.json` (자기주식 취득 결정)

**학술 근거**:
- 한용환·한문성(2015): 자사주 취득 공시일(D+0) 양의 비정상수익률(AR) 확인
- 직접 취득 방식이 신탁 대비 시장 반응 더 강함
- Lee, Jung, Thornton(2005): 공시 후 20일 CAR 평균 +1.25%
- Isa & Lee(2014): 국제 비교에서도 한국 시장 효과 유의

**구현 방안**:
```
DartFeed에서 "자기주식취득결정" 공시 감지
  → DS005 API 후속 호출 (취득 주식수, 취득 금액, 취득 방법)
  → 직접매입 여부, 시가총액 대비 취득 규모 계산
  → confidence 가산 (+10~+20, 규모/방법에 따라 차등)
```

**구현 난이도**: ★★☆☆☆ (기존 DartFeed 확장)

#### (B) 유상증자 / CB 발행 — 강한 매도 시그널

**API**: DS005 `piicDecsn.json` (유상증자), `crBondDecsn.json` (전환사채)

- 유상증자: 주식 희석 → 단기 강한 음(-) 반응
- CB/BW 발행: 오버행 리스크 → 중기 음(-) 반응
- 현재 kindshot 뉴스 파이프라인에서 NEG 버킷으로 분류되나, 구조화 데이터 미활용

**구현 방안**: NEG 버킷 confidence에 발행 규모 기반 penalty 적용

**구현 난이도**: ★★☆☆☆

#### (C) 실적 서프라이즈 — PEAD(Post-Earnings Announcement Drift)

**학술 근거 (한국 시장)**:
- Cheon & Park(2019): 2000~2012년 데이터, PEAD 경제적·통계적으로 유의미
- Kim & Byun(2020): 주목도 낮은 종목에서 drift 더 강함
- Kwon & Park(2019): 시장 심리 일관 시 drift 발생
- Lee(2021): 처분효과와 PEAD 관계 확인

**전략 설계**:
```
잠정실적 공시 감지 (DART pblntf_ty=A 또는 KIND "잠정실적")
  → 전기 대비 영업이익 증감률 추출
  → 서프라이즈 크기에 따라 confidence 조정
  → 양의 서프라이즈 + 저관심 소형주 = 최적 타겟
```

**핵심 포인트**:
- 잠정실적은 확정 공시 전 1~2주 먼저 발표 → 주가 반응 가장 강함
- DART DS003로 구조화 재무 데이터 후속 조회 가능
- 분기별 실적 시즌(1/4/7/10월)에 집중 모니터링

**구현 난이도**: ★★★☆☆ (컨센서스 데이터 확보가 관건)

#### (D) 대량보유 / 최대주주 변경

**API**: DS004 `majorstock.json` (대량보유 5%+), DS002 `hyslrChgSttus.json` (최대주주 변동)

- 5% 이상 지분 변동은 경영권 분쟁, M&A 시그널
- 최대주주 변경은 불확실성 증가 → 변동성 확대
- 방향성(매수/매도)보다는 **변동성 확대 시그널**로 활용

**구현 난이도**: ★★☆☆☆

#### (E) 대규모 수주/계약

현재 kindshot의 핵심 시그널 중 하나. DART에는 전용 구조화 엔드포인트가 **없으며**, `report_nm` 텍스트 매칭이 유일한 방법. 현재 DartFeed가 정확히 이 방식을 사용 중.

**개선 방안**: DS005 관련 엔드포인트(`영업양수도`, `자산양수도` 등)로 계약 금액 구조화 추출

**구현 난이도**: ★★★☆☆

### 2.4 API 제약 및 운영 고려사항

| 항목 | 내용 |
|------|------|
| 일일 한도 | 10,000건 (개인) |
| 분당 제한 | 1,000건 |
| 데이터 지연 | 접수 후 수 초~수 분 이내 반영 |
| 폴링 예산 | 장중 6.5h × 30초 간격 = ~780회/일 (여유) |
| DS005 후속 호출 | 추가 API 소모 → 일일 예산 관리 필요 |
| 실시간성 | KIND RSS/KIS가 약간 더 빠를 수 있음. DART는 정확성/구조화에 강점 |

### 2.5 Python 라이브러리

| 라이브러리 | 용도 | 권장 |
|-----------|------|------|
| OpenDartReader | pandas 기반, 29+ 보고서 유형 지원 | 백테스트/리서치용 |
| dart-fss | 재무제표 추출 특화 | 실적 분석용 |
| 직접 HTTP 호출 | 현재 DartFeed 방식 | 프로덕션 권장 (의존성 최소) |

---

## 3. 수급 기반 전략

### 3.1 전략 개요

외국인/기관 순매수 급증 종목을 포착하여 정보 기반 매매(informed trading)를 추종하는 전략.

### 3.2 데이터 소스

#### pykrx (현재 kindshot에 이미 사용 중)

```python
# 투자자별 순매수
pykrx.stock.get_market_trading_value_by_date(start, end, ticker)
# 투자자별 거래량
pykrx.stock.get_market_trading_volume_by_date(start, end, ticker)
# 특정 투자자 순매수 상위 종목
pykrx.stock.get_market_net_purchases_of_equities(start, end, market, investor)
```

**한계**: 스크래핑 기반으로 실시간 아님. 과도한 호출 시 IP 차단. 일별 데이터 최적화.

#### KIS API (추가 구현 필요)

| API명 | TR_ID | 용도 |
|--------|--------|------|
| 주식현재가 투자자 | `FHKST01010900` | 종목별 투자자 유형별 매매 현황 |
| 외인기관 매매종목가집계 | 시세분석 카테고리 | 순매수 상위 종목 |
| 종목별 외인기관 추정가집계 | 시세분석 카테고리 | 장중 추정 순매수 |

**데이터 지연이 핵심 이슈**:
- KIS 추정가집계: 09:30, 11:20, 13:20, 14:30에 업데이트 (30분~2시간 지연)
- KRX 투자자별 거래실적: 장 마감 후 ~18:00 확정
- 장중 정확한 실시간 수급 = KRX 유료 API 또는 HTS 데이터 피드만 가능

### 3.3 학술 근거

| 논문 | 핵심 발견 |
|------|----------|
| "Net arbitrage trading by foreign investors" (2026) | 외국인 NAT가 횡단면 수익률 유의미 예측. 5분위 스프레드 1.141%/분기 |
| "Institutional Investor Trading in Short Horizon" | 기관 순매수 → 1주 후 수익률 양의 관계. 단, 역추세 특성도 존재 |
| "Daily Stock Trading and Information Asymmetry" | 외국인 매매가 정보 비대칭 완화 역할 |
| "Do foreign short-sellers predict stock returns?" | 외국인 공매도가 향후 주가 하락 예측 |

### 3.4 kindshot 연동 설계

```
FlowStrategy (신규 Strategy 클래스)
  ├─ KIS API 폴링 (FHKST01010900) — 120초 간격
  ├─ 외인/기관 순매수 급증 감지 (전일 대비 200%+ or 5일 평균 대비 300%+)
  ├─ TradeSignal 발행 (source=FLOW, confidence=외인비중·규모에 비례)
  └─ 기존 guardrails 통과 후 주문
```

**실현 가능한 활용법**:
- 09:30 첫 업데이트 이후 외국인 추정 순매수 상위 종목을 **필터**로 사용
- 오후 세션(13:20 업데이트 이후)에서 확인된 수급 종목에 대해 매수
- 뉴스/공시 시그널과 수급 시그널이 겹치면 confidence 가산

### 3.5 예상 성과 및 한계

| 항목 | 평가 |
|------|------|
| 예상 승률 | 55~60% (수급 확인 후 진입 시) |
| 데이터 지연 | 30분~2시간 (최대 약점) |
| 알파 소스 | 정보 기반 매매 추종 |
| 리스크 | 지연으로 인한 late entry, 추격매수 위험 |

**구현 난이도**: ★★★☆☆ (KIS API 엔드포인트 추가 + 수급 판단 로직)

---

## 4. 이벤트 드리븐 전략

### 4.1 공매도 과열 해제 — 가장 유망

#### 제도 개요
- KRX가 2017.3.27부터 시행
- 공매도 비율 급등 + 주가 급락 종목 → "과열종목" 지정 → **다음 거래일 1일 공매도 금지**
- 해제일(D+2)에 숏커버링 반등 기대

#### 학술 근거
- 이우백(2020, 한국증권학회지): 2017.9~2019.10 데이터 분석
  - 지정 전 급락 종목: 지정 후 **하락세 유의미하게 둔화**
  - 10% 이상 급락 후 지정된 종목: **평균회귀(mean reversion) 효과** 관찰
  - 해제일(D+2): 숏커버링 + 추가 공매도 양방향 변동성

#### 데이터 소스
- KRX 정보데이터시스템(`data.krx.co.kr`): 과열종목 현황
- KIND(`kind.krx.co.kr`): 과열종목 지정 공시
- kindshot `QuoteRiskState`에 이미 `short_over_yn` 필드 존재

#### kindshot 연동 설계
```
ShortOverheatStrategy (신규)
  ├─ KRX/KIND에서 공매도 과열종목 지정 공시 수집 (장 마감 후)
  ├─ 해제일(D+2) 오전에 해당 종목 모니터링
  ├─ 시가 대비 하락 시 매수 (평균회귀 기대)
  ├─ TradeSignal 발행 (source=EVENT)
  └─ 기존 guardrails (ADV, spread 등) 통과 후 주문
```

**구현 난이도**: ★★☆☆☆ (short_over_yn 이미 존재, 스크래핑 추가만 필요)

### 4.2 실적 잠정공시 이벤트

#### 전략 설계
```
EarningsStrategy (신규 또는 NewsStrategy 확장)
  ├─ DART/KIND에서 "잠정실적" 관련 공시 감지
  ├─ 전기 대비 영업이익 증감률 추출 (텍스트 파싱 or DS003)
  ├─ 서프라이즈 크기 기반 confidence 조정:
  │   ├─ 영업이익 +30% 이상: confidence +15
  │   ├─ 영업이익 +10~30%: confidence +8
  │   └─ 영업이익 -10% 이하: NEG 버킷 전환
  └─ PEAD 기반: 공시 후 2~5일 양의 drift 기대 (소형주/저관심 종목에서 더 강함)
```

**핵심**: 컨센서스 데이터 없이도 전기 대비 증감만으로 유의미한 시그널 생성 가능

**구현 난이도**: ★★★☆☆

### 4.3 IPO 첫날 전략

#### 한국 IPO 통계 (2025년)
- 공모가 대비 시초가 평균 상승률: **89.2%** (2024년 64.4% 대비 상승)
- 전체 상장사 90%(69/77)가 공모가 상회 시초가
- 시초가 대비 종가는 **하락 경향** → "시초가 매도" 유효

#### 전략 설계
- 첫 30분 모멘텀 추종 후 탈출
- 변동성 극단적 → 리스크 관리 필수
- 별도 IPO 캘린더 연동 필요 (38커뮤니케이션, KRX KIND)

**구현 난이도**: ★★★★☆ (별도 모듈, IPO 캘린더 연동, 특수 리스크 관리)

### 4.4 배당락일 전략

- 한국 배당락 효과는 해외 대비 과도하게 큰 편
- 12월 결산법인 기준 배당기준일 후 첫 거래일에 배당락 발생
- **데이트레이딩보다 스윙(2~5일)에 적합**
- 데이터: 금융위원회 공공데이터 API, pykrx `get_market_fundamental()`

**구현 난이도**: ★★★☆☆
**우선순위**: 낮음 (스윙 전략이므로 kindshot 데이트레이딩 프레임과 불일치)

---

## 5. 페어 트레이딩

### 5.1 방법론

1. 동일 섹터 종목 중 상관계수 0.9+ 페어 선별
2. 코인테그레이션 검정 (ADF test, Johansen test, p-value ≤ 0.05)
3. 스프레드 z-score 모니터링
4. |z-score| > 2 진입, |z-score| < 0.5 청산

Python: `statsmodels.tsa.stattools.coint`, `statsmodels.tsa.vector_ar.vecm.coint_johansen`

### 5.2 한국 시장 유망 페어

| 섹터 | 페어 | 근거 |
|------|------|------|
| 반도체 | 삼성전자 / SK하이닉스 | 메모리 사이클 동조 |
| 자동차 | 현대차 / 기아 | 동일 그룹, 판매 동조 |
| 화학 | LG화학 / SK이노베이션 | 배터리/석유화학 동시 노출 |
| 금융 | KB금융 / 신한지주 | 금리 환경 동일 |
| 통신 | SK텔레콤 / KT | 과점 시장, 유사 구조 |
| 조선 | HD현대중공업 / 삼성중공업 | 수주 사이클 동조 |

### 5.3 데이트레이딩 적용 평가

**QuantConnect 연구**: 10분봉 인트라데이 적용 시 연 26.9%, 샤프 3.0 (미국 은행 섹터)

**한국 시장 제약**:
| 제약 | 영향 |
|------|------|
| 증권거래세 (매도 시 0.18~0.23%) | 페어트레이딩 수익 크게 잠식 |
| 개인 공매도 제한 | 롱-온리 페어 또는 ETF 인버스 활용 필요 |
| 인트라데이 노이즈 | 분~시간 수준에서 평균회귀 신호 불안정 |
| 스프레드 리스크 | KOSDAQ 중소형은 비드-애스크 스프레드 큼 |

### 5.4 권장

- **데이트레이딩 부적합** — 거래세가 수익을 잠식, 공매도 제약
- 일봉 기반 스윙(1~5일)이 비용 대비 효과적
- 초대형주(삼성전자/SK하이닉스) 페어만 인트라데이 가능성 있음

**구현 난이도**: ★★★★★ (완전 새 모듈, 통계 엔진, 공매도 대안)

---

## 6. 거시연동 전략

### 6.1 현재 kindshot 매크로 인프라

`market.py`에 이미 매크로 레짐 연동 구현:
```python
_MACRO_REGIME_MULTIPLIERS = {
    "expansionary": 1.2,   # risk-on → 포지션 확대
    "neutral": 1.0,
    "contractionary": 0.6, # risk-off → 포지션 축소
}
```

- `macro_api_base_url`로 macro-intelligence 서비스에서 레짐 수신
- `macro_overall_regime`, `macro_kr_regime`, `macro_crypto_regime` 3개 레이어
- 포지션 사이징에 매크로 멀티플라이어 적용 중
- VKOSPI: **비활성** (KRX가 AWS IP 차단)

### 6.2 핵심 매크로 지표 → KOSPI 인트라데이 영향

| 지표 | 한국 시장 영향 | 데이터 소스 | 지연 |
|------|--------------|-----------|------|
| 미국 S&P 500 선물 | 한국 장 오전 갭 결정 (가장 큰 단일 요인) | Yahoo Finance | 실시간 |
| USD/KRW 환율 | 원화 약세→수출주↑, 강세→내수주↑ | 한국은행, KIS | 실시간 |
| VIX | 글로벌 리스크 온/오프 게이지 | CBOE | 실시간 |
| VKOSPI | 한국 시장 내재변동성 | KRX (차단), Investing.com | 스크래핑 |
| 미국 10년물 국채 | 금리↑→성장주↓, 금융주↑ | FRED API | 실시간 |
| 중국 CSI 300 | 수출주/소재주 동조 | Yahoo Finance | 실시간 |

### 6.3 전략 설계 — 매크로 필터 강화

#### (A) 오전 세션 필터 (미국 야간 시장 반영)

```python
# S&P 500 전일 종가 변동률 기반
if sp500_overnight_return > +1.0:
    morning_bias = "BULLISH"      # 갭업 추종
    confidence_adj = +5
elif sp500_overnight_return < -1.0:
    morning_bias = "DEFENSIVE"    # 매수 신뢰도 상향
    confidence_adj = -10
    min_confidence_override = 85  # 더 엄격
else:
    morning_bias = "NEUTRAL"
    confidence_adj = 0
```

#### (B) 환율 필터

```python
if usdkrw_change_pct > +0.5:    # 원화 급락
    # 수출주 매수 유리, 내수주 회피
    sector_filter = "EXPORT_PREFER"
elif usdkrw_change_pct < -0.5:  # 원화 급등
    sector_filter = "DOMESTIC_PREFER"
```

#### (C) VIX 레벨 필터

```python
if vix < 15:
    macro_mode = "NORMAL"         # 정상 운영
elif vix < 25:
    macro_mode = "CAUTIOUS"       # 포지션 축소 (0.7x)
else:
    macro_mode = "DEFENSIVE"      # 매수 차단 또는 최소화 (0.3x)
```

#### (D) VKOSPI 대안

현재 KRX 스크래핑 차단으로 VKOSPI 비활성. 대안:
1. **Investing.com 스크래핑**: VKOSPI 실시간 데이터 제공
2. **macro-intelligence 확장**: 이미 연동된 서비스에 VKOSPI 포함
3. **VIX 프록시**: VIX를 VKOSPI 대용으로 사용 (상관계수 0.85+)

### 6.4 kindshot 연동 — 기존 인프라 확장

```
MacroFilterEnhancement (기존 market.py 확장)
  ├─ 오전 세션: S&P 500 야간 수익률 반영 → confidence 조정
  ├─ 종일: VIX 레벨 기반 매크로 모드 설정
  ├─ 종일: USD/KRW 변동 기반 섹터 선호도 설정
  └─ 기존 macro_regime_multiplier에 추가 레이어
```

**구현 난이도**: ★★☆☆☆ (기존 인프라 확장, 데이터 소스 추가만)

---

## 7. 전략별 비교 및 우선순위

### 종합 비교표

| # | 전략 | 이론적 근거 | 데이터 가용성 | 예상 승률 | kindshot 연동 | 구현 난이도 | 우선순위 |
|---|------|-----------|-------------|----------|-------------|-----------|---------|
| 1 | **DART 자사주 매입** | 강 (CAR +1.25%) | 높 (DS005 API) | 60~65% | DartFeed 확장 | ★★☆☆☆ | **P0** |
| 2 | **DART 실적 서프라이즈** | 강 (PEAD 검증) | 높 (DART+KIND) | 55~65% | 뉴스 파이프라인 | ★★★☆☆ | **P0** |
| 3 | **공매도 과열 해제** | 중상 (학술 검증) | 높 (KRX 공시) | 55~60% | short_over_yn 활용 | ★★☆☆☆ | **P0** |
| 4 | **매크로 필터 강화** | 강 (VIX/환율) | 높 (실시간) | N/A (필터) | market.py 확장 | ★★☆☆☆ | **P0** |
| 5 | **DART 유증/CB 블랙리스트** | 강 (희석 효과) | 높 (DS005) | N/A (방어) | NEG 버킷 강화 | ★★☆☆☆ | **P1** |
| 6 | **수급 (외인/기관)** | 중상 (정보매매) | 중 (30분 지연) | 55~60% | KIS API 추가 | ★★★☆☆ | **P1** |
| 7 | **대량보유 변동** | 중 (변동성 확대) | 높 (DS004) | 50~55% | DartFeed 확장 | ★★☆☆☆ | **P2** |
| 8 | **IPO 첫날** | 중상 (89% 시초가) | 중 (캘린더 필요) | 60~70% | 별도 모듈 | ★★★★☆ | **P2** |
| 9 | **배당락** | 약 (스윙 적합) | 높 (공공API) | 50~55% | 별도 모듈 | ★★★☆☆ | **P3** |
| 10 | **페어 트레이딩** | 중 (거래세 잠식) | 높 (pykrx) | 50~55% | 완전 신규 | ★★★★★ | **P3** |

### P0 구현 로드맵 (권장 순서)

1. **매크로 필터 강화** — 가장 빠른 ROI. `market.py` 확장으로 S&P 500 야간, VIX, 환율 필터 추가. 기존 모든 전략에 즉시 적용.

2. **DART 자사주 매입 시그널** — DartFeed에서 DS005 `trsrStockAqDecsn.json` 후속 호출 추가. 직접매입 + 시총 대비 규모로 confidence 가산.

3. **DART 실적 서프라이즈** — 잠정실적 공시 감지 + 전기 대비 증감률 추출. 실적 시즌(1/4/7/10월) 집중.

4. **공매도 과열 해제** — KRX에서 과열종목 지정 수집 + D+2 해제일 모니터링. `short_over_yn` 필드 활용.

### 예상 포트폴리오 효과

```
현재: 뉴스(POS_STRONG) + TA + Y2I + Alpha
  → 승률 25.8%, 수익률 -24.31% (9일 누적)

P0 적용 후 기대:
  + 매크로 필터: 불리한 매크로 환경에서 진입 차단 → 손실 축소
  + 자사주/실적 시그널: 학술 검증된 알파 소스 추가 → 승률 개선
  + 공매도 과열 해제: 평균회귀 기반 추가 시그널 → 다각화
  → 목표 승률 40%+, 월간 수익률 양전환
```

---

## 8. 참고문헌

### 학술 논문
- Cheon, Y. & Park, H. (2019). Individual investors and post-earnings-announcement drift: Evidence from Korea. *Pacific-Basin Finance Journal*, 53, 379-398.
- Kim, J. & Byun, S. (2020). Investor Attention from Internet Search Volume and Underreaction to Earnings Announcements in Korea. *Sustainability*, 12(22), 9358.
- Kwon, S. & Park, J. (2019). Market Sentiment Trend, Investor Inertia, and PEAD: Evidence from Korea. *Sustainability*, 11(18), 5137.
- Lee, S. (2021). V-Shaped Disposition Effect, Stock Prices, and PEAD: Evidence from Korea. *Journal of Behavioral Finance*, 24(3).
- 한용환, 한문성 (2015). 자기주식 취득 공시가 주가수익률에 미치는 영향. *상업경영연구*, 29(4), 75-99.
- Lee, J., Jung, S. & Thornton, J. (2005). Korean stock market buyback effects. (CAR analysis)
- Isa, M. & Lee, S. (2014). International comparison of buyback announcement returns.
- Park, S. et al. (2024). Market participants' trading behavior toward anomalies: Evidence from the Korean market. *Pacific-Basin Finance Journal*.
- 이우백 (2020). 공매도 과열종목 지정제도 실효성 분석. *한국증권학회지*.
- Net arbitrage trading by foreign investors and short sellers (2026). *ScienceDirect*.

### 데이터 소스
- DART OpenAPI: https://opendart.fss.or.kr
- KRX 정보데이터시스템: https://data.krx.co.kr
- KIND 한국거래소 공시: https://kind.krx.co.kr
- KIS Developers: https://apiportal.koreainvestment.com
- 금융위원회 공공데이터: https://data.go.kr
- OpenDartReader: https://github.com/FinanceData/OpenDartReader
- pykrx: https://github.com/sharebook-kr/pykrx

### 시장 데이터
- Investing.com VKOSPI: https://www.investing.com/indices/kospi-volatility
- 38커뮤니케이션 IPO: https://38.co.kr
