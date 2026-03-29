# DART 공시 기반 전략 구현 계획서

> 작성일: 2026-03-29 | 상태: Draft

## 1. 현황 분석

### DART API 접근 가능성

| 항목 | 상태 |
|------|------|
| `dart_api_key` config | ✅ `config.py:146` — `DART_API_KEY` env var |
| `dart_base_url` | ✅ `https://opendart.fss.or.kr/api` |
| `DartFeed` 클래스 | ✅ `feed.py:592-789` — `list.json` 폴링 구현 |
| 서버 DART_API_KEY | ❌ **미설정** — `.env`에 추가 필요 |
| DS005 구조화 API | ❌ **미구현** — 제목 텍스트만 사용 중 |
| API 일일 한도 | 10,000건 (개인) — 충분 |
| 분당 제한 | 1,000건 — 충분 |

### 기존 DART 코드

```
DartFeed.poll_once() → list.json 폴링
  → RawDisclosure(title=report_nm, ticker, corp_name, ...)
  → 기존 뉴스 파이프라인 (bucket 분류 → LLM 판단 → guardrails)
```

**한계**: report_nm 텍스트로만 분류. 자사주 매입 규모, 방식(직접/신탁) 등 구조화 정보 미활용.

### Strategy 프레임워크

```python
# strategy.py — 이미 구현된 프로토콜
class Strategy(Protocol):
    name: str
    source: SignalSource
    enabled: bool
    async def stream_signals(self) -> AsyncIterator[TradeSignal]: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...

# SignalSource enum
NEWS, TECHNICAL, Y2I, ALPHA, MACRO, COMPOSITE

# StrategyRegistry.stream_all() → 시그널 병합 → strategy_runtime.py 실행
```

---

## 2. 전략 선택: DART 자사주 매입

### 선택 근거

| 기준 | 평가 |
|------|------|
| 학술 근거 | 강 — CAR +1.25% (D+20), 직접매입 효과 더 강함 |
| 데이터 가용성 | 높 — DS005 `trsrStockAqDecsn.json` |
| 구현 난이도 | ★★☆☆☆ — DartFeed 확장 + 새 Strategy 클래스 |
| 기존 인프라 활용 | 높 — DartFeed, guardrails, order executor 재사용 |
| 예상 승률 | 60~65% |
| 시그널 빈도 | 낮음 (월 10~30건) — 품질 > 수량 |

### DS005 API: `trsrStockAqDecsn.json`

```
GET https://opendart.fss.or.kr/api/trsrStockAqDecsn.json
  ?crtfc_key={API_KEY}
  &corp_code={고유번호}
  &bgn_de={시작일}
  &end_de={종료일}

Response:
{
  "status": "000",
  "list": [{
    "rcept_no": "접수번호",
    "corp_cls": "Y(유가)/K(코스닥)",
    "corp_code": "고유번호",
    "corp_name": "회사명",
    "aqpln_stk_knd": "취득 주식 종류",
    "aqpln_stk_qy": "취득 예정 주식수",
    "aqpln_stk_prc": "취득 예정 금액",
    "aq_mth": "취득 방법",        // ← 핵심: "직접취득" vs "신탁취득"
    "aq_pp": "취득 목적",
    "aq_expd_bgd": "취득 예정기간 시작",
    "aq_expd_edd": "취득 예정기간 종료",
    ...
  }]
}
```

### 핵심 시그널 로직

```
자사주 취득 결정 공시 감지 (DartFeed report_nm 매칭)
  → DS005 API 후속 호출 (corp_code로 구조화 데이터 조회)
  → 시그널 스코어링:
    ├─ 직접매입: confidence +15 (신탁 대비 시장 반응 강함)
    ├─ 신탁매입: confidence +8
    ├─ 시총 대비 취득 규모:
    │   ├─ 3%+: confidence +10 (대규모)
    │   ├─ 1~3%: confidence +5 (보통)
    │   └─ <1%: confidence +0 (소규모)
    └─ 기본 confidence: 65 (자사주 매입 자체가 강한 시그널)
  → TradeSignal(source=NEWS, confidence=65~90)
  → 기존 guardrails → 주문
```

---

## 3. 구현 설계

### 3.1 아키텍처

```
DartFeed.poll_once()
  │
  ├─ (기존) RawDisclosure → 뉴스 파이프라인 (변경 없음)
  │
  └─ (신규) 자사주 매입 공시 감지
       → DartEnricher.enrich_buyback(rcept_no, corp_code)
       → DS005 API 호출
       → BuybackSignal 생성
       │
       ▼
DartBuybackStrategy.stream_signals()
  → TradeSignal yield
  → StrategyRegistry.stream_all()
  → strategy_runtime._execute_strategy_signal()
  → guardrails → order
```

### 3.2 신규/변경 파일

| 파일 | 변경 | 설명 |
|------|------|------|
| `src/kindshot/dart_enricher.py` | **신규** | DS005 API 호출 + 자사주 매입 데이터 파싱 |
| `src/kindshot/dart_buyback_strategy.py` | **신규** | Strategy 프로토콜 구현, 자사주 매입 시그널 생성 |
| `src/kindshot/feed.py` | 수정 | DartFeed에 자사주 매입 공시 감지 훅 추가 |
| `src/kindshot/config.py` | 수정 | 자사주 전략 관련 설정 추가 |
| `src/kindshot/main.py` | 수정 | DartBuybackStrategy 등록 |
| `tests/test_dart_enricher.py` | **신규** | DS005 API mock 테스트 |
| `tests/test_dart_buyback_strategy.py` | **신규** | 전략 시그널 생성 테스트 |

### 3.3 `dart_enricher.py` — DS005 API 클라이언트

```python
"""DART DS005 구조화 데이터 조회 모듈."""

@dataclass(frozen=True)
class BuybackInfo:
    corp_code: str
    corp_name: str
    ticker: str
    rcept_no: str
    method: str          # "직접취득" | "신탁계약체결" | etc.
    is_direct: bool      # 직접매입 여부
    planned_shares: int  # 취득 예정 주식수
    planned_amount: int  # 취득 예정 금액 (원)
    purpose: str         # 취득 목적
    period_start: str    # 취득 예정기간 시작
    period_end: str      # 취득 예정기간 종료

class DartEnricher:
    """DART DS005 구조화 데이터 조회."""

    async def fetch_buyback(self, corp_code: str, rcept_no: str) -> BuybackInfo | None:
        """자사주 취득 결정 구조화 데이터 조회."""
        # GET trsrStockAqDecsn.json
        ...

    async def get_corp_code(self, ticker: str) -> str | None:
        """종목코드 → DART 고유번호 변환 (corpCode.xml 캐시 활용)."""
        ...
```

**corp_code 매핑 이슈**: DART API는 `corp_code`(8자리 고유번호)를 사용하고, kindshot은 `ticker`(6자리 종목코드)를 사용. 매핑 필요.

해결: DART `corpCode.xml` (전체 기업 목록, ~3.5MB ZIP) 다운로드 → `{ticker: corp_code}` 딕셔너리 캐싱. 하루 1회 갱신.

### 3.4 `dart_buyback_strategy.py` — Strategy 구현

```python
class DartBuybackStrategy:
    """DART 자사주 매입 공시 기반 매매 전략."""

    @property
    def name(self) -> str: return "dart_buyback"

    @property
    def source(self) -> SignalSource: return SignalSource.NEWS

    @property
    def enabled(self) -> bool: return self._enabled

    async def stream_signals(self) -> AsyncIterator[TradeSignal]:
        """DartFeed에서 자사주 매입 공시를 감지하고 시그널을 생성."""
        async for disclosures in self._dart_feed.stream():
            for disc in disclosures:
                if not self._is_buyback(disc):
                    continue
                info = await self._enricher.fetch_buyback(corp_code, disc.rss_guid)
                if info is None:
                    continue
                confidence = self._score(info)
                yield TradeSignal(
                    strategy_name="dart_buyback",
                    source=SignalSource.NEWS,
                    ticker=disc.ticker,
                    corp_name=disc.corp_name,
                    action=Action.BUY,
                    confidence=confidence,
                    size_hint=self._size_hint(confidence),
                    reason=f"자사주 {'직접' if info.is_direct else '신탁'}매입 {info.planned_amount/1e8:.0f}억",
                    headline=disc.title,
                    event_id=f"buyback_{disc.rss_guid}",
                    detected_at=disc.detected_at,
                    metadata={"buyback": asdict(info)},
                )
```

### 3.5 자사주 매입 공시 감지 패턴

DartFeed의 `report_nm`에서 매칭할 키워드:

```python
_BUYBACK_PATTERNS = [
    "자기주식취득결정",
    "자사주취득",
    "주요사항보고서(자기주식취득결정)",
    "자기주식 취득 결정",
]
```

### 3.6 config.py 추가 설정

```python
# --- DART Buyback Strategy ---
dart_buyback_enabled: bool = field(
    default_factory=lambda: _env_bool("DART_BUYBACK_ENABLED", True))
dart_buyback_base_confidence: int = field(
    default_factory=lambda: _env_int("DART_BUYBACK_BASE_CONFIDENCE", 65))
dart_buyback_direct_bonus: int = 15      # 직접매입 보너스
dart_buyback_trust_bonus: int = 8        # 신탁매입 보너스
dart_buyback_large_scale_pct: float = 3.0  # 시총 대비 이 비율 이상이면 대규모
dart_buyback_min_amount: int = field(
    default_factory=lambda: _env_int("DART_BUYBACK_MIN_AMOUNT", 1_000_000_000))  # 최소 10억
```

---

## 4. 데이터 파이프라인 구조

```
┌──────────────────────────────────────────────────────┐
│                    DartFeed                           │
│  poll_once() → list.json (30초 간격)                  │
│  └─ RawDisclosure(report_nm, ticker, corp_name, ...) │
└──────────────┬───────────────────────────────────────┘
               │
    ┌──────────┴──────────┐
    │                     │
    ▼                     ▼
┌─────────────┐   ┌──────────────────────┐
│ 기존 뉴스     │   │ DartBuybackStrategy  │
│ 파이프라인    │   │                      │
│ (변경 없음)   │   │ 1. report_nm 매칭     │
│              │   │ 2. corp_code 조회     │
│              │   │ 3. DS005 API 호출     │
│              │   │ 4. 스코어링            │
│              │   │ 5. TradeSignal 생성   │
└─────────────┘   └──────────┬───────────┘
                             │
                             ▼
                  ┌──────────────────────┐
                  │ StrategyRegistry     │
                  │ stream_all()         │
                  └──────────┬───────────┘
                             │
                             ▼
                  ┌──────────────────────┐
                  │ strategy_runtime.py  │
                  │ _execute_strategy_   │
                  │ signal()             │
                  │ ├─ guardrails        │
                  │ ├─ order executor    │
                  │ └─ price tracking    │
                  └──────────────────────┘
```

### 중복 시그널 방지

자사주 매입 공시는 DartFeed → 뉴스 파이프라인에도 흘러간다. 중복 진입 방지:

1. `DartBuybackStrategy`가 처리한 공시의 `rcept_no`를 `consumed_set`에 기록
2. 뉴스 파이프라인의 `event_registry`에서 동일 `rcept_no` 기반 event_id로 중복 체크
3. 또는 DartFeed에서 자사주 매입 공시를 별도 채널로 분리 (권장)

**권장 방식**: DartFeed에 `buyback_callback` 훅 추가. 자사주 매입 감지 시 뉴스 파이프라인에는 넘기지 않고 Strategy로만 전달.

---

## 5. corp_code 매핑 전략

### 문제
- DART API는 `corp_code` (8자리, 예: `00126380`) 사용
- kindshot은 `ticker` (6자리 종목코드, 예: `005930`) 사용
- `list.json`은 `stock_code`(= ticker)를 반환하지만, DS005 API는 `corp_code` 필요

### 해결: corpCode.xml 로컬 캐싱

```python
class CorpCodeMapper:
    """DART corpCode.xml → {ticker: corp_code} 매핑."""

    _CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"

    async def load(self) -> None:
        """corpCode.xml ZIP 다운로드 → XML 파싱 → 딕셔너리 빌드."""
        # GET corpCode.xml?crtfc_key={key} → ZIP → XML
        # <list><corp_code>00126380</corp_code><stock_code>005930</stock_code>...</list>
        ...

    def get_corp_code(self, ticker: str) -> str | None:
        return self._map.get(ticker)
```

- 하루 1회 장 시작 전 갱신 (약 90,000개 법인)
- 메모리 캐시 + 디스크 캐시 (`data/dart_corp_codes.json`)
- API 호출 1회/일

---

## 6. 구현 순서

### Step 1: 인프라 (corp_code 매핑 + DS005 클라이언트)
1. `dart_enricher.py` — `CorpCodeMapper` + `DartEnricher` 구현
2. `test_dart_enricher.py` — mock 테스트

### Step 2: 전략 클래스
3. `dart_buyback_strategy.py` — Strategy 프로토콜 구현
4. `test_dart_buyback_strategy.py` — 시그널 생성 테스트

### Step 3: 통합
5. `config.py` — 자사주 전략 설정 추가
6. `feed.py` — DartFeed 자사주 공시 분리 훅
7. `main.py` — DartBuybackStrategy 등록

### Step 4: 배포
8. 서버 `.env`에 `DART_API_KEY` 설정
9. `DART_BUYBACK_ENABLED=true` 설정
10. 배포 + 로그 모니터링

---

## 7. 리스크 & 완화

| 리스크 | 완화 |
|--------|------|
| DART API 키 미발급 | opendart.fss.or.kr 에서 무료 발급 (개인/법인) |
| DS005 API 지연 | list.json 감지 후 DS005 호출 → 추가 2~5초 지연. 수용 가능 |
| corp_code 매핑 실패 | corpCode.xml에 없는 종목은 스킵. 상장사는 거의 100% 커버 |
| 중복 시그널 | buyback_callback으로 뉴스 파이프라인과 분리 |
| 시그널 빈도 낮음 | 월 10~30건 예상. 다른 P0 전략과 병행하여 시그널 다각화 |
| 자사주 매입 후 주가 미반응 | guardrails(ADV, spread, 시간대)로 필터링. confidence 기반 포지션 사이징 |

---

## 8. 향후 확장 (P0 나머지)

이 구현이 완료되면 동일 패턴으로 확장:

1. **DART 유증/CB 블랙리스트** — `DartEnricher`에 `fetch_capital_increase()`, `fetch_cb_issuance()` 추가 → NEG confidence penalty
2. **DART 실적 서프라이즈** — 잠정실적 감지 + DS003 재무 데이터 → PEAD 기반 시그널
3. **공매도 과열 해제** — KRX 스크래핑 + `short_over_yn` 활용 → D+2 평균회귀 시그널

`DartEnricher`를 범용 DS005/DS003/DS004 클라이언트로 설계하여 재사용.
