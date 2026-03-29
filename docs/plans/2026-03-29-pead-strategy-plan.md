# PEAD (실적 서프라이즈) 전략 구현 계획서

> 작성일: 2026-03-29 | 상태: Draft | 우선순위: P0

## 1. 배경

### PEAD (Post-Earnings Announcement Drift)란?

실적 서프라이즈 발표 후 주가가 서프라이즈 방향으로 수일~수주간 추가 드리프트하는 현상.
한국 시장에서 특히 KOSDAQ 소형주, 저관심 종목에서 효과가 강하다.

### 현재 kindshot의 실적 처리

| 항목 | 현황 |
|------|------|
| 뉴스 키워드 감지 | ✅ `news_category.py` — "실적", "흑자전환", "영업이익" 등 |
| 키워드 confidence | ✅ `decision.py` — "사상최대 실적" 88, "깜짝 실적" 86 |
| 구조화 데이터 | ❌ 전기대비 증감률 미계산 |
| DART 잠정실적 감지 | ❌ 미구현 |
| DS003 재무데이터 | ❌ 미구현 |

**한계**: 순수 텍스트 키워드 매칭만 사용 중. 실적의 규모·방향·서프라이즈 크기를 정량화하지 못함.

---

## 2. DART API 분석

### 2.1 잠정실적 감지: `list.json` (DS001)

DART에는 잠정실적 전용 API가 **없다**. `list.json` 공시검색 API에서 `report_nm` 텍스트 매칭으로 감지해야 한다.

```
GET https://opendart.fss.or.kr/api/list.json
  ?crtfc_key={key}
  &pblntf_ty=I             # 거래소공시 (수시공시 포함)
  &pblntf_detail_ty=I001   # 수시공시
  &page_count=100
```

**잠정실적 report_nm 패턴:**

```python
_EARNINGS_PATTERNS = [
    "영업(잠정)실적",        # 가장 흔한 형태
    "잠정실적",
    "영업실적(잠정)",
    "잠정손익",
    "연결재무제표기준영업(잠정)실적",
    "매출액또는손익구조30%이상변경",  # 실적 변동 공시 (30% 룰)
]
```

> 참고: `DartFeed.poll_once()`가 이미 `list.json`을 폴링 중. 자사주 큐 라우팅과 동일한 패턴으로 `earnings_queue` 추가하면 됨.

### 2.2 전기 실적 기준선: `fnlttSinglAcnt.json` (DS003)

```
GET https://opendart.fss.or.kr/api/fnlttSinglAcnt.json
  ?crtfc_key={key}
  &corp_code={8자리}
  &bsns_year={연도}
  &reprt_code={분기코드}
```

| reprt_code | 보고서 |
|---|---|
| `11013` | 1분기 |
| `11012` | 반기 |
| `11014` | 3분기 |
| `11011` | 사업보고서 (연간) |

**응답 핵심 필드:**

| 필드 | 설명 |
|---|---|
| `account_nm` | `매출액`, `영업이익`, `당기순이익` |
| `fs_div` | `CFS` (연결) / `OFS` (별도) |
| `sj_div` | `IS` (손익계산서) |
| `thstrm_amount` | 당기 금액 |
| `frmtrm_amount` | 전기 금액 |

**용도**: 전기 영업이익/매출액을 기준선으로 사용하여 서프라이즈 크기를 계산.

### 2.3 잠정실적 원문 수치: `document.xml`

```
GET https://opendart.fss.or.kr/api/document.xml
  ?crtfc_key={key}
  &rcept_no={접수번호}
```

잠정실적 공시 원문에서 매출액/영업이익 수치를 정규식 파싱.
**주의**: 표준 XML 스키마 없음 — 회사마다 포맷이 다름. 다중 정규식 패턴 필요.

### 2.4 API 호출 흐름 (2단계)

```
┌────────────────────────────────────────────────────────────────┐
│  Phase 1: 실시간 감지 (DartFeed → earnings_queue)              │
│                                                                │
│  list.json 폴링 → report_nm 패턴 매칭 → earnings_queue 라우팅   │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────┐
│  Phase 2: 서프라이즈 분석 (DartEarningsStrategy)                │
│                                                                │
│  2a. DS003 fnlttSinglAcnt → 전기 영업이익 기준선 조회            │
│  2b. document.xml → 당기 잠정 수치 파싱 (선택적, v2)            │
│  2c. report_nm 텍스트에서 증감률 추출 (v1 즉시 활용)             │
│  2d. 서프라이즈 스코어링 → TradeSignal 생성                     │
└────────────────────────────────────────────────────────────────┘
```

---

## 3. 전략 설계

### 3.1 데이터 모델

```python
@dataclass(frozen=True)
class EarningsInfo:
    """잠정실적 구조화 데이터."""
    corp_code: str
    corp_name: str
    ticker: str
    rcept_no: str
    period: str              # "2026Q1", "2025FY" 등
    revenue_current: int     # 당기 매출액 (원)
    revenue_prior: int       # 전기 매출액 (원)
    op_profit_current: int   # 당기 영업이익 (원)
    op_profit_prior: int     # 전기 영업이익 (원)
    revenue_yoy_pct: float   # 매출 전기대비 증감률 (%)
    op_profit_yoy_pct: float # 영업이익 전기대비 증감률 (%)
    is_turnaround: bool      # 적자→흑자 전환 여부
    source: str              # "report_nm" | "ds003" | "document_xml"
```

### 3.2 서프라이즈 스코어링

```python
def score_earnings(info: EarningsInfo, config: Config) -> tuple[int, Action]:
    """실적 서프라이즈 confidence 스코어링.

    Returns:
        (confidence, action) — action은 BUY 또는 SKIP
    """
    score = config.dart_earnings_base_confidence  # 기본 60

    op_yoy = info.op_profit_yoy_pct

    # ── 부정 서프라이즈 → SKIP (블랙리스트) ──
    if op_yoy < -20:
        return (0, Action.SKIP)   # 영업이익 -20% 이상 감소
    if op_yoy < 0:
        return (30, Action.SKIP)  # 소폭 감소도 진입하지 않음

    # ── 긍정 서프라이즈 → BUY ──
    # 적자→흑자 전환: 최대 보너스
    if info.is_turnaround:
        score += config.dart_earnings_turnaround_bonus  # +25

    # 영업이익 증감률 보너스
    if op_yoy >= 100:        # 영업이익 2배 이상
        score += 20
    elif op_yoy >= 50:       # 50%~100%
        score += 15
    elif op_yoy >= 30:       # 30%~50%
        score += 10
    elif op_yoy >= 10:       # 10%~30%
        score += 5
    # 10% 미만: 보너스 없음

    # 매출 성장 동반 보너스 (질적 성장)
    if info.revenue_yoy_pct >= 20:
        score += 5

    return (min(score, 100), Action.BUY)
```

### 3.3 Size Hint 매핑

```python
def size_hint_from_earnings(confidence: int, op_yoy: float) -> SizeHint:
    """서프라이즈 크기와 confidence에 따른 포지션 크기."""
    if confidence >= 85 and op_yoy >= 50:
        return SizeHint.L
    if confidence >= 75:
        return SizeHint.M
    return SizeHint.S
```

### 3.4 Hold Profile

실적 서프라이즈는 PEAD 효과로 30분~1시간 추가 상승 기대:

| 서프라이즈 크기 | Hold 시간 | TP | SL |
|---|---|---|---|
| 영업이익 +100% 이상 | 45분 | +4.0% | -1.5% |
| 영업이익 +50~100% | 35분 | +3.0% | -1.5% |
| 영업이익 +30~50% | 25분 | +2.5% | -1.5% |
| 적자→흑자 전환 | 40분 | +3.5% | -1.5% |
| 영업이익 +10~30% | 20분 | +2.0% | -1.5% |

### 3.5 필터 및 가드레일

| 필터 | 기준 | 이유 |
|---|---|---|
| 최소 서프라이즈 | 영업이익 YoY >= +10% | 노이즈 필터 |
| 최소 confidence | 65 | 기존 가드레일 통과 |
| 시간 필터 | 장중 (09:00~15:00) | PEAD는 장중 반응 |
| ADV 필터 | 기존 가드레일 적용 | 유동성 확보 |
| 중복 방지 | rcept_no 기반 dedup | 동일 공시 재처리 차단 |
| 뉴스 파이프라인 분리 | earnings_queue 라우팅 시 news pipeline skip | 중복 시그널 방지 |

---

## 4. 구현 계획

### Phase 1: 기본 PEAD (report_nm 텍스트 기반)

> 목표: 잠정실적 공시 감지 → report_nm에서 증감률 추출 → 시그널 생성

#### 4.1 파일 생성/수정 목록

| 파일 | 작업 | 설명 |
|------|------|------|
| `src/kindshot/dart_earnings_strategy.py` | **신규** | EarningsStrategy 전체 구현 |
| `src/kindshot/dart_enricher.py` | 수정 | `fetch_prior_earnings()` DS003 메서드 추가 |
| `src/kindshot/feed.py` | 수정 | `earnings_queue` 라우팅 추가 |
| `src/kindshot/config.py` | 수정 | PEAD 설정 파라미터 추가 |
| `src/kindshot/main.py` | 수정 | DartEarningsStrategy 등록 |
| `tests/test_dart_earnings_strategy.py` | **신규** | 유닛 테스트 |
| `tests/test_dart_enricher.py` | 수정 | DS003 테스트 추가 |

#### 4.2 구현 순서

**Step 1: Config 추가** (`config.py`)

```python
# --- DART Earnings (PEAD) Strategy ---
dart_earnings_enabled: bool = _env_bool("DART_EARNINGS_ENABLED", True)
dart_earnings_base_confidence: int = _env_int("DART_EARNINGS_BASE_CONFIDENCE", 60)
dart_earnings_turnaround_bonus: int = 25    # 적자→흑자 전환 보너스
dart_earnings_min_op_yoy_pct: float = 10.0  # 최소 영업이익 증감률 (%)
```

**Step 2: DartEnricher 확장** (`dart_enricher.py`)

```python
@dataclass(frozen=True)
class PriorEarnings:
    """전기 실적 기준선 (DS003)."""
    corp_code: str
    bsns_year: str
    reprt_code: str
    revenue: int        # 매출액 (원)
    op_profit: int      # 영업이익 (원)
    net_income: int     # 당기순이익 (원)

class DartEnricher:
    async def fetch_prior_earnings(
        self, ticker: str, bsns_year: str, reprt_code: str
    ) -> Optional[PriorEarnings]:
        """DS003 fnlttSinglAcnt로 전기 실적 조회."""
        ...
```

**Step 3: DartFeed 라우팅** (`feed.py`)

```python
# DartFeed.__init__에 earnings_queue 파라미터 추가
# poll_once()에서 잠정실적 패턴 매칭 → earnings_queue 라우팅

if self._earnings_queue is not None and _is_earnings_report(report_nm):
    self._earnings_queue.put_nowait(disc)
    logger.info("DART earnings routed to strategy queue: %s %s", stock_code, report_nm)
    continue
```

**Step 4: DartEarningsStrategy** (`dart_earnings_strategy.py`)

```python
class DartEarningsStrategy:
    """DART 잠정실적 기반 PEAD 전략.

    Strategy 프로토콜 구현. earnings_queue로 잠정실적 공시를 수신,
    전기대비 서프라이즈를 계산하여 TradeSignal을 생성.
    """

    async def stream_signals(self) -> AsyncIterator[TradeSignal]:
        while not self._stop_event.is_set():
            disc = await self._queue.get()
            signal = await self._process_disclosure(disc)
            if signal:
                yield signal

    async def _process_disclosure(self, disc: RawDisclosure) -> Optional[TradeSignal]:
        # 1. report_nm에서 증감률 정규식 추출 시도
        earnings_text = extract_earnings_from_report_nm(disc.title)

        # 2. DS003으로 전기 기준선 조회
        prior = await self._enricher.fetch_prior_earnings(
            disc.ticker, bsns_year, reprt_code
        )

        # 3. 서프라이즈 스코어링
        confidence, action = score_earnings(info, self._config)

        # 4. TradeSignal 생성
        return TradeSignal(
            strategy_name="dart_earnings",
            source=SignalSource.NEWS,
            ...
        )
```

**Step 5: main.py 등록**

```python
# DART 잠정실적 (PEAD) 전략
if config.dart_earnings_enabled and config.dart_api_key and session and earnings_queue:
    earnings_strategy = DartEarningsStrategy(
        config, session, earnings_queue, stop_event=stop_event,
    )
    strategy_registry.register(earnings_strategy)
```

**Step 6: 테스트**

```python
# test_dart_earnings_strategy.py
- test_earnings_detection_patterns()      # 잠정실적 report_nm 매칭
- test_score_earnings_positive()          # 긍정 서프라이즈 스코어링
- test_score_earnings_negative()          # 부정 서프라이즈 → SKIP
- test_score_earnings_turnaround()        # 적자→흑자 전환
- test_size_hint_mapping()               # confidence → size hint
- test_signal_generation()               # 전체 파이프라인
- test_deduplication()                   # rcept_no 중복 방지
- test_min_surprise_filter()             # 최소 증감률 필터
```

### Phase 2: document.xml 파싱 (v2, 후속)

> 목표: 공시 원문에서 정확한 매출/영업이익 수치 추출

- `document.xml` 정규식 파싱 모듈 구현
- 파싱 실패 시 Phase 1 (report_nm + DS003) 폴백
- 정확도 높은 서프라이즈 계산
- **별도 태스크로 분리** (표준 XML 스키마 없어 복잡도 높음)

---

## 5. report_nm 텍스트 파싱 전략

잠정실적 공시의 `report_nm`에는 증감률 정보가 포함되는 경우가 많다:

```
"매출액또는손익구조30%(대규모법인은15%)이상변경"
"영업(잠정)실적(공정공시)"
"[기재정정]매출액또는손익구조30%이상변경"
```

**report_nm 파싱 정규식:**

```python
# 30% 룰 공시에서 변경 비율 추출
_CHANGE_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)%")

# 잠정실적 여부 판별
_EARNINGS_RE = re.compile(
    r"영업\(잠정\)실적|잠정실적|잠정손익|매출액또는손익구조"
)
```

**핵심**: report_nm만으로는 증감률을 정확히 알 수 없는 경우가 많다.
→ DS003 전기 기준선을 기본으로, report_nm은 감지 트리거로만 사용.
→ 30% 룰 공시는 최소 30% 이상 변동이므로 서프라이즈 확정으로 취급.

---

## 6. 분기 자동 판별

잠정실적 공시 시점에서 어떤 분기 실적인지 자동 판별:

```python
def infer_report_period(rcept_dt: str) -> tuple[str, str]:
    """공시 접수일 기준으로 보고 대상 분기 추정.

    Args:
        rcept_dt: "20260329" 형태 접수일

    Returns:
        (bsns_year, reprt_code) — DS003 조회용

    실적 시즌 패턴:
    - 1~2월: 전년 사업보고서 (연간, 11011) → 전전년 비교
    - 4~5월: 1분기 (11013) → 전년 동분기 비교
    - 7~8월: 반기 (11012)
    - 10~11월: 3분기 (11014)
    """
    month = int(rcept_dt[4:6])
    year = int(rcept_dt[:4])

    if month <= 3:
        return (str(year - 1), "11011")  # 전년 사업보고서
    elif month <= 6:
        return (str(year), "11013")       # 1분기
    elif month <= 9:
        return (str(year), "11012")       # 반기
    else:
        return (str(year), "11014")       # 3분기
```

**DS003 전기 비교 기준선:**

| 대상 분기 | DS003 조회 | 비교 기준 |
|---|---|---|
| 2026 Q1 | bsns_year=2025, reprt_code=11013 | 전년 동분기 |
| 2025 FY | bsns_year=2024, reprt_code=11011 | 전전년 연간 |
| 2026 H1 | bsns_year=2025, reprt_code=11012 | 전년 반기 |
| 2026 Q3 | bsns_year=2025, reprt_code=11014 | 전년 3분기 |

---

## 7. 아키텍처 다이어그램

```
DartFeed.poll_once()
  │
  ├─ report_nm에 "자기주식취득결정" → buyback_queue → DartBuybackStrategy
  │
  ├─ report_nm에 "잠정실적" / "30%이상변경" → earnings_queue → DartEarningsStrategy ← NEW
  │                                                │
  │                                                ├─ DS003 전기 기준선 조회
  │                                                ├─ 서프라이즈 스코어링
  │                                                └─ TradeSignal 생성
  │
  └─ 그 외 공시 → 뉴스 파이프라인 (bucket → LLM → guardrails)


StrategyRegistry.stream_all()
  ├─ NewsStrategy signals
  ├─ TechnicalStrategy signals
  ├─ DartBuybackStrategy signals
  └─ DartEarningsStrategy signals   ← NEW
        │
        ▼
strategy_runtime.consume_strategy_signals()
  → Guardrails → Order Executor → Price Tracking
```

---

## 8. Config 파라미터 전체

```python
# --- DART Earnings (PEAD) Strategy ---
dart_earnings_enabled: bool         # env: DART_EARNINGS_ENABLED, default: True
dart_earnings_base_confidence: int  # env: DART_EARNINGS_BASE_CONFIDENCE, default: 60
dart_earnings_turnaround_bonus: int # 적자→흑자 보너스, default: 25
dart_earnings_min_op_yoy_pct: float # 최소 영업이익 증감률 %, default: 10.0
dart_earnings_hold_minutes: int     # 기본 hold 시간 (분), default: 30
dart_earnings_tp_pct: float         # 기본 TP, default: 3.0
dart_earnings_sl_pct: float         # 기본 SL, default: 1.5
```

---

## 9. 리스크 및 제약

| 리스크 | 대응 |
|--------|------|
| DS003에 최신 분기 데이터 미등록 | report_nm 텍스트 기반 폴백, 30% 룰 공시는 자체로 서프라이즈 확정 |
| document.xml 비표준 포맷 | Phase 1에서는 document.xml 파싱 생략, DS003 + report_nm 조합 |
| 실적 시즌 외 잠정실적 공시 | 분기 추정 로직이 커버, 예외 시 DS003 다중 조회 |
| API 한도 (일 10,000건) | 잠정실적은 하루 수십건 수준, 자사주 + 잠정실적 합쳐도 여유 |
| DART_API_KEY 서버 미설정 | 서버 .env에 추가 필요 (배포 시 체크) |
| KIND-only 공시 누락 | DartFeed가 KIND RSS도 병행 폴링 중 — 커버됨 |
| 장 마감 후 공시 | 다음 장 시작 시 처리 (기존 DartFeed 패턴) |

---

## 10. 성과 기대치

### PEAD 학술 근거 (한국 시장)

- 긍정 서프라이즈 후 5일 CAR: +2~4% (KOSDAQ)
- 부정 서프라이즈 후 5일 CAR: -3~5%
- 효과는 KOSDAQ, 소형주, 저관심 종목에서 더 강함
- 장중(당일) 반응의 70% 이상이 공시 후 1시간 내 발생

### kindshot 데이트레이딩 적용

- **타겟**: 공시 후 30분~1시간 내 PEAD 드리프트 포착
- **기대 수익률**: 건당 +1.5~3.0% (서프라이즈 크기에 비례)
- **기대 승률**: 65~75% (방향성 높은 이벤트)
- **빈도**: 실적 시즌(1/4/7/10월) 주 5~15건

---

## 11. 다음 단계

1. ✅ PEAD 전략 설계 (이 문서)
2. ⬜ Phase 1 구현 (TDD)
   - config → enricher → feed routing → strategy → main 등록 → 테스트
3. ⬜ 서버 배포 및 DART_API_KEY 설정
4. ⬜ Paper 모드 1주 관찰
5. ⬜ Phase 2: document.xml 파싱 (정밀 수치 추출)
