# 공매도 과열 해제 전략 (Short Overheating Release) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 공매도 과열 종목 해제일 D+2에 평균회귀 매수 시그널을 생성하는 전략

**Architecture:** KRX data.krx.co.kr에서 공매도 과열종목 지정/해제 데이터를 폴링하고, 해제된 종목의 D+2 영업일에 BUY 시그널을 생성한다. Polling 패턴(TechnicalStrategy와 유사)으로 매일 장전 1회 + 장중 주기적 스캔.

**Tech Stack:** aiohttp (KRX HTTP), pykrx (영업일 계산), Strategy protocol

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/kindshot/krx_short_overheating.py` | KRX 공매도 과열종목 API 스크래퍼 (데이터 fetch + 파싱) |
| `src/kindshot/short_overheating_strategy.py` | Strategy 프로토콜 구현 (폴링, D+2 판단, 시그널 생성) |
| `src/kindshot/config.py` | 전략 설정 필드 추가 |
| `src/kindshot/main.py` | 전략 등록 |
| `tests/test_krx_short_overheating.py` | KRX 스크래퍼 단위 테스트 |
| `tests/test_short_overheating_strategy.py` | 전략 단위 테스트 |

---

### Task 1: KRX 공매도 과열종목 스크래퍼 — 데이터 모델 + fetch

**Files:**
- Create: `src/kindshot/krx_short_overheating.py`
- Create: `tests/test_krx_short_overheating.py`

- [ ] **Step 1: Write failing tests for data model and parsing**

```python
# tests/test_krx_short_overheating.py
"""Tests for KRX 공매도 과열종목 스크래퍼."""

from __future__ import annotations

from datetime import date

import pytest

from kindshot.krx_short_overheating import (
    OverheatingRecord,
    parse_overheating_response,
)


# ── Data Model ──────────────────────────────────────────


class TestOverheatingRecord:
    def test_fields(self):
        rec = OverheatingRecord(
            ticker="005930",
            corp_name="삼성전자",
            market="STK",
            designation_date=date(2026, 3, 20),
            release_date=date(2026, 3, 25),
            designation_type="해제",
            overheating_days=3,
        )
        assert rec.ticker == "005930"
        assert rec.release_date == date(2026, 3, 25)
        assert rec.overheating_days == 3


# ── parse_overheating_response ──────────────────────────


KRX_SAMPLE_RESPONSE = {
    "OutBlock_1": [
        {
            "ISU_SRT_CD": "005930",
            "ISU_ABBRV": "삼성전자",
            "MKT_NM": "KOSPI",
            "OVRHT_TP_NM": "지정",
            "OVRHT_DD_CNT": "3",
            "OVRHT_STRT_DD": "2026/03/20",
            "OVRHT_END_DD": "2026/03/24",
        },
        {
            "ISU_SRT_CD": "005930",
            "ISU_ABBRV": "삼성전자",
            "MKT_NM": "KOSPI",
            "OVRHT_TP_NM": "해제",
            "OVRHT_DD_CNT": "3",
            "OVRHT_STRT_DD": "2026/03/20",
            "OVRHT_END_DD": "2026/03/25",
        },
        {
            "ISU_SRT_CD": "000660",
            "ISU_ABBRV": "SK하이닉스",
            "MKT_NM": "KOSPI",
            "OVRHT_TP_NM": "지정",
            "OVRHT_DD_CNT": "5",
            "OVRHT_STRT_DD": "2026/03/18",
            "OVRHT_END_DD": "2026/03/24",
        },
    ]
}


class TestParseOverheatingResponse:
    def test_parse_all_records(self):
        records = parse_overheating_response(KRX_SAMPLE_RESPONSE)
        assert len(records) == 3

    def test_parse_release_record(self):
        records = parse_overheating_response(KRX_SAMPLE_RESPONSE)
        releases = [r for r in records if r.designation_type == "해제"]
        assert len(releases) == 1
        assert releases[0].ticker == "005930"
        assert releases[0].release_date == date(2026, 3, 25)
        assert releases[0].overheating_days == 3

    def test_empty_response(self):
        assert parse_overheating_response({}) == []
        assert parse_overheating_response({"OutBlock_1": []}) == []

    def test_malformed_record_skipped(self):
        resp = {"OutBlock_1": [{"ISU_SRT_CD": "005930"}]}  # 필수 필드 누락
        records = parse_overheating_response(resp)
        assert records == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_krx_short_overheating.py -v`
Expected: FAIL with ImportError (module not found)

- [ ] **Step 3: Implement data model and parser**

```python
# src/kindshot/krx_short_overheating.py
"""KRX 공매도 과열종목 지정/해제 데이터 스크래퍼.

data.krx.co.kr API (bld=dbms/MDC/STAT/srt/MDCSTAT30901)에서
공매도 과열종목 지정/해제 내역을 조회한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional, Sequence

import aiohttp

logger = logging.getLogger(__name__)

KRX_DATA_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
KRX_PAGE_URL = "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0203"
KRX_BLD = "dbms/MDC/STAT/srt/MDCSTAT30901"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": KRX_PAGE_URL,
    "X-Requested-With": "XMLHttpRequest",
}


@dataclass(frozen=True)
class OverheatingRecord:
    """공매도 과열종목 지정/해제 레코드."""
    ticker: str
    corp_name: str
    market: str  # STK(KOSPI), KSQ(KOSDAQ)
    designation_date: date  # 지정 시작일
    release_date: date  # 지정 종료일 (해제일)
    designation_type: str  # "지정" or "해제"
    overheating_days: int  # 과열 지정 기간 (거래일)


def _parse_date(s: str) -> date:
    """'2026/03/20' 또는 '20260320' 형식 파싱."""
    s = s.strip().replace("/", "").replace("-", "")
    return datetime.strptime(s, "%Y%m%d").date()


def _market_code(name: str) -> str:
    """KRX 시장명 → 코드."""
    if "KOSDAQ" in name or "코스닥" in name:
        return "KSQ"
    return "STK"


def parse_overheating_response(data: dict) -> list[OverheatingRecord]:
    """KRX JSON 응답을 OverheatingRecord 리스트로 파싱."""
    items = data.get("OutBlock_1", [])
    records: list[OverheatingRecord] = []
    for item in items:
        try:
            ticker = item["ISU_SRT_CD"]
            corp_name = item["ISU_ABBRV"]
            market = _market_code(item.get("MKT_NM", ""))
            designation_type = item["OVRHT_TP_NM"]
            overheating_days = int(item.get("OVRHT_DD_CNT", "0"))
            designation_date = _parse_date(item["OVRHT_STRT_DD"])
            release_date = _parse_date(item["OVRHT_END_DD"])
            records.append(OverheatingRecord(
                ticker=ticker,
                corp_name=corp_name,
                market=market,
                designation_date=designation_date,
                release_date=release_date,
                designation_type=designation_type,
                overheating_days=overheating_days,
            ))
        except (KeyError, ValueError) as e:
            logger.debug("Skipping malformed overheating record: %s", e)
    return records


async def fetch_overheating_records(
    session: aiohttp.ClientSession,
    start_date: date,
    end_date: date,
    market: str = "0",  # 0=전체, 1=KOSPI, 2=KOSDAQ
) -> list[OverheatingRecord]:
    """KRX에서 공매도 과열종목 지정/해제 내역 조회.

    Args:
        session: aiohttp 세션
        start_date: 조회 시작일
        end_date: 조회 종료일
        market: "0"=전체, "1"=KOSPI, "2"=KOSDAQ
    """
    # 세션 쿠키 획득을 위해 페이지 먼저 방문
    try:
        async with session.get(KRX_PAGE_URL, headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            await resp.read()
    except Exception:
        logger.debug("KRX page prefetch failed (continuing anyway)")

    payload = {
        "bld": KRX_BLD,
        "searchType": "1",
        "mktTpCd": market,
        "strtDd": start_date.strftime("%Y%m%d"),
        "endDd": end_date.strftime("%Y%m%d"),
    }

    try:
        async with session.post(
            KRX_DATA_URL,
            data=payload,
            headers=_HEADERS,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                logger.warning("KRX overheating API returned %d", resp.status)
                return []
            text = await resp.text()
            if not text or text.strip() == "LOGOUT":
                logger.warning("KRX overheating API returned empty/LOGOUT")
                return []
            data = await resp.json(content_type=None)
            return parse_overheating_response(data)
    except Exception:
        logger.warning("KRX overheating fetch failed", exc_info=True)
        return []


def filter_released(
    records: Sequence[OverheatingRecord],
    *,
    released_after: Optional[date] = None,
) -> list[OverheatingRecord]:
    """해제 레코드만 필터링.

    Args:
        records: 전체 레코드
        released_after: 이 날짜 이후 해제된 것만 (inclusive)
    """
    result = [r for r in records if r.designation_type == "해제"]
    if released_after:
        result = [r for r in result if r.release_date >= released_after]
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_krx_short_overheating.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/kindshot/krx_short_overheating.py tests/test_krx_short_overheating.py
git commit -m "feat: KRX 공매도 과열종목 스크래퍼 — 데이터 모델, 파서, fetch"
```

---

### Task 2: D+2 영업일 계산 + Confidence 스코어링

**Files:**
- Modify: `src/kindshot/krx_short_overheating.py`
- Modify: `tests/test_krx_short_overheating.py`

- [ ] **Step 1: Write failing tests for D+2 calculation and scoring**

```python
# tests/test_krx_short_overheating.py 에 추가

from kindshot.krx_short_overheating import (
    calc_entry_date,
    score_overheating_confidence,
)


# ── calc_entry_date (D+2 영업일) ────────────────────────


class TestCalcEntryDate:
    def test_d2_normal_weekday(self):
        # 수요일 해제 → 금요일 진입
        assert calc_entry_date(date(2026, 3, 25)) == date(2026, 3, 27)

    def test_d2_thursday_release(self):
        # 목요일 해제 → 월요일 진입 (주말 건너뜀)
        assert calc_entry_date(date(2026, 3, 26)) == date(2026, 3, 30)

    def test_d2_friday_release(self):
        # 금요일 해제 → 화요일 진입
        assert calc_entry_date(date(2026, 3, 27)) == date(2026, 3, 31)


# ── score_overheating_confidence ────────────────────────


class TestScoreOverheatingConfidence:
    def test_base_confidence(self):
        score = score_overheating_confidence(overheating_days=1, drop_pct=0.0)
        assert score == 60  # base only

    def test_long_overheating_bonus(self):
        # 5일+ 과열 → +10 보너스
        score = score_overheating_confidence(overheating_days=5, drop_pct=0.0)
        assert score == 70

    def test_deep_drop_bonus(self):
        # 10%+ 하락 → +15 보너스
        score = score_overheating_confidence(overheating_days=1, drop_pct=-12.0)
        assert score == 75

    def test_combined_max(self):
        # 5일+ 과열 + 10%+ 하락 → 60 + 10 + 15 = 85
        score = score_overheating_confidence(overheating_days=7, drop_pct=-15.0)
        assert score == 85

    def test_cap_at_100(self):
        score = score_overheating_confidence(overheating_days=20, drop_pct=-30.0)
        assert score <= 100

    def test_moderate_drop_bonus(self):
        # 5%+ 하락 → +8 보너스
        score = score_overheating_confidence(overheating_days=1, drop_pct=-6.0)
        assert score == 68

    def test_moderate_overheating_bonus(self):
        # 3일 과열 → +5 보너스
        score = score_overheating_confidence(overheating_days=3, drop_pct=0.0)
        assert score == 65
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_krx_short_overheating.py::TestCalcEntryDate -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Implement D+2 calculation and scoring**

`src/kindshot/krx_short_overheating.py`에 아래 함수 추가:

```python
def calc_entry_date(release_date: date, offset: int = 2) -> date:
    """해제일로부터 D+N 영업일 계산 (주말 제외, 공휴일은 미반영).

    Args:
        release_date: 과열 해제일
        offset: 영업일 오프셋 (기본 2)
    """
    current = release_date
    days_added = 0
    while days_added < offset:
        current += timedelta(days=1)
        if current.weekday() < 5:  # 월~금
            days_added += 1
    return current


# ── Confidence Scoring ──────────────────────────────────

# 기본 confidence
_BASE_CONFIDENCE = 60

# 과열 기간 보너스
_OVERHEATING_DAYS_THRESHOLDS = [
    (5, 10),   # 5일+ → +10
    (3, 5),    # 3일+ → +5
]

# 하락폭 보너스 (음수값 기준)
_DROP_PCT_THRESHOLDS = [
    (-10.0, 15),  # -10%+ → +15
    (-5.0, 8),    # -5%+ → +8
    (-3.0, 3),    # -3%+ → +3
]


def score_overheating_confidence(
    overheating_days: int,
    drop_pct: float,
    base: int = _BASE_CONFIDENCE,
) -> int:
    """공매도 과열 해제 confidence 스코어링.

    Args:
        overheating_days: 과열 지정 기간 (거래일)
        drop_pct: 과열 기간 중 주가 수익률 (%, 음수=하락)
        base: 기본 confidence
    """
    score = base

    # 과열 기간 보너스 (높은 threshold부터 매칭)
    for threshold, bonus in _OVERHEATING_DAYS_THRESHOLDS:
        if overheating_days >= threshold:
            score += bonus
            break

    # 하락폭 보너스 (더 큰 하락부터 매칭)
    for threshold, bonus in _DROP_PCT_THRESHOLDS:
        if drop_pct <= threshold:
            score += bonus
            break

    return min(score, 100)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_krx_short_overheating.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/kindshot/krx_short_overheating.py tests/test_krx_short_overheating.py
git commit -m "feat: 공매도 과열 D+2 영업일 계산 + confidence 스코어링"
```

---

### Task 3: Config 설정 추가

**Files:**
- Modify: `src/kindshot/config.py`

- [ ] **Step 1: Add config fields after dart_earnings section (line ~162)**

`config.py`의 `dart_earnings_negative_skip` 라인 뒤에 추가:

```python
    # --- Short Overheating (공매도 과열 해제) Strategy ---
    short_overheating_enabled: bool = field(default_factory=lambda: _env_bool("SHORT_OVERHEATING_ENABLED", False))
    short_overheating_base_confidence: int = field(default_factory=lambda: _env_int("SHORT_OVERHEATING_BASE_CONFIDENCE", 60))
    short_overheating_poll_interval_s: float = field(default_factory=lambda: _env_float("SHORT_OVERHEATING_POLL_INTERVAL_S", 3600.0))  # 1시간
    short_overheating_lookback_days: int = field(default_factory=lambda: _env_int("SHORT_OVERHEATING_LOOKBACK_DAYS", 7))  # 7일 내 해제 종목 스캔
    short_overheating_d_offset: int = field(default_factory=lambda: _env_int("SHORT_OVERHEATING_D_OFFSET", 2))  # D+2
    short_overheating_min_overheating_days: int = field(default_factory=lambda: _env_int("SHORT_OVERHEATING_MIN_DAYS", 1))  # 최소 과열 일수
```

- [ ] **Step 2: Run existing tests to verify no regression**

Run: `pytest tests/test_config.py -v` (if exists) or `pytest -x -q --timeout=10 -k "config" 2>/dev/null; pytest -x -q --timeout=10 tests/test_short_overheating_strategy.py 2>/dev/null || true`
Expected: No regressions

- [ ] **Step 3: Commit**

```bash
git add src/kindshot/config.py
git commit -m "feat: 공매도 과열 해제 전략 config 필드 추가"
```

---

### Task 4: ShortOverheatingStrategy 구현

**Files:**
- Create: `src/kindshot/short_overheating_strategy.py`
- Create: `tests/test_short_overheating_strategy.py`

- [ ] **Step 1: Write failing tests for strategy**

```python
# tests/test_short_overheating_strategy.py
"""Tests for short_overheating_strategy: 폴링, D+2 시그널 생성."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kindshot.krx_short_overheating import OverheatingRecord
from kindshot.models import Action, SizeHint
from kindshot.short_overheating_strategy import ShortOverheatingStrategy
from kindshot.strategy import SignalSource


def _make_config(**overrides):
    config = MagicMock()
    config.short_overheating_enabled = overrides.get("enabled", True)
    config.short_overheating_base_confidence = overrides.get("base_confidence", 60)
    config.short_overheating_poll_interval_s = overrides.get("poll_interval", 1.0)
    config.short_overheating_lookback_days = overrides.get("lookback_days", 7)
    config.short_overheating_d_offset = overrides.get("d_offset", 2)
    config.short_overheating_min_overheating_days = overrides.get("min_days", 1)
    return config


def _make_record(
    ticker="005930",
    corp_name="삼성전자",
    release_date=date(2026, 3, 25),
    overheating_days=3,
) -> OverheatingRecord:
    return OverheatingRecord(
        ticker=ticker,
        corp_name=corp_name,
        market="STK",
        designation_date=date(2026, 3, 20),
        release_date=release_date,
        designation_type="해제",
        overheating_days=overheating_days,
    )


class TestProperties:
    def test_name(self):
        config = _make_config()
        strategy = ShortOverheatingStrategy(config, session=MagicMock())
        assert strategy.name == "short_overheating"

    def test_source(self):
        config = _make_config()
        strategy = ShortOverheatingStrategy(config, session=MagicMock())
        assert strategy.source == SignalSource.TECHNICAL

    def test_enabled(self):
        config = _make_config(enabled=True)
        strategy = ShortOverheatingStrategy(config, session=MagicMock())
        assert strategy.enabled is True

    def test_disabled(self):
        config = _make_config(enabled=False)
        strategy = ShortOverheatingStrategy(config, session=MagicMock())
        assert strategy.enabled is False


class TestBuildSignal:
    def test_generates_buy_signal(self):
        config = _make_config()
        strategy = ShortOverheatingStrategy(config, session=MagicMock())
        record = _make_record(overheating_days=5)
        signal = strategy._build_signal(record, drop_pct=-8.0)
        assert signal.action == Action.BUY
        assert signal.ticker == "005930"
        assert signal.strategy_name == "short_overheating"
        assert signal.confidence >= 60

    def test_confidence_increases_with_overheating_days(self):
        config = _make_config()
        strategy = ShortOverheatingStrategy(config, session=MagicMock())
        sig_short = strategy._build_signal(_make_record(overheating_days=1), drop_pct=0.0)
        sig_long = strategy._build_signal(_make_record(overheating_days=5), drop_pct=0.0)
        assert sig_long.confidence > sig_short.confidence

    def test_size_hint_from_confidence(self):
        config = _make_config()
        strategy = ShortOverheatingStrategy(config, session=MagicMock())
        sig = strategy._build_signal(_make_record(overheating_days=7), drop_pct=-15.0)
        # High confidence → larger size
        assert sig.size_hint in (SizeHint.M, SizeHint.L)


class TestFilterForToday:
    def test_matches_d2_entry_date(self):
        config = _make_config(d_offset=2)
        strategy = ShortOverheatingStrategy(config, session=MagicMock())
        # 수요일 해제 → 금요일 진입
        record = _make_record(release_date=date(2026, 3, 25))  # Wed
        today = date(2026, 3, 27)  # Fri = D+2
        result = strategy._is_entry_today(record, today)
        assert result is True

    def test_rejects_wrong_date(self):
        config = _make_config(d_offset=2)
        strategy = ShortOverheatingStrategy(config, session=MagicMock())
        record = _make_record(release_date=date(2026, 3, 25))
        today = date(2026, 3, 26)  # Thu = D+1
        result = strategy._is_entry_today(record, today)
        assert result is False

    def test_skips_already_signaled(self):
        config = _make_config(d_offset=2)
        strategy = ShortOverheatingStrategy(config, session=MagicMock())
        record = _make_record(release_date=date(2026, 3, 25))
        strategy._signaled.add("005930_20260325")
        today = date(2026, 3, 27)
        result = strategy._is_entry_today(record, today)
        assert result is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_short_overheating_strategy.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Implement ShortOverheatingStrategy**

```python
# src/kindshot/short_overheating_strategy.py
"""공매도 과열 해제 D+2 평균회귀 매매 전략.

KRX에서 공매도 과열종목 해제 데이터를 폴링하고,
해제일로부터 D+2 영업일에 BUY 시그널을 생성한다.

평균회귀 논리: 과열 지정 기간 동안 공매도 금지 → 해제 후
숏 포지션 재진입 압력이 완화되며 가격이 회복되는 패턴.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import AsyncIterator, Optional

import aiohttp

from kindshot.config import Config
from kindshot.krx_short_overheating import (
    OverheatingRecord,
    calc_entry_date,
    fetch_overheating_records,
    filter_released,
    score_overheating_confidence,
)
from kindshot.models import Action, SizeHint
from kindshot.strategy import SignalSource, TradeSignal
from kindshot.tz import KST as _KST

logger = logging.getLogger(__name__)


def _size_hint(confidence: int) -> SizeHint:
    if confidence >= 85:
        return SizeHint.L
    if confidence >= 75:
        return SizeHint.M
    return SizeHint.S


class ShortOverheatingStrategy:
    """공매도 과열 해제 D+2 매수 전략.

    Strategy 프로토콜 구현. 폴링 패턴으로 KRX를 주기적으로 조회하고,
    오늘이 D+2 진입일인 종목에 대해 BUY 시그널을 생성한다.
    """

    def __init__(
        self,
        config: Config,
        session: aiohttp.ClientSession,
        *,
        stop_event: Optional[asyncio.Event] = None,
    ) -> None:
        self._config = config
        self._session = session
        self._stop_event = stop_event or asyncio.Event()
        self._enabled = config.short_overheating_enabled
        self._signaled: set[str] = set()  # "ticker_releasedate" 중복 방지

    @property
    def name(self) -> str:
        return "short_overheating"

    @property
    def source(self) -> SignalSource:
        return SignalSource.TECHNICAL

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def start(self) -> None:
        logger.info("ShortOverheatingStrategy started (enabled=%s)", self._enabled)

    async def stop(self) -> None:
        logger.info("ShortOverheatingStrategy stopping (signaled=%d)", len(self._signaled))

    async def stream_signals(self) -> AsyncIterator[TradeSignal]:
        """주기적으로 KRX를 폴링하고 D+2 진입 시그널 생성."""
        poll_interval = self._config.short_overheating_poll_interval_s

        while not self._stop_event.is_set():
            try:
                signals = await self._poll_once()
                for signal in signals:
                    yield signal
            except asyncio.CancelledError:
                return
            except Exception:
                logger.warning("ShortOverheating poll failed", exc_info=True)

            # 다음 폴링까지 대기
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=poll_interval)
                return  # stop_event set
            except asyncio.TimeoutError:
                pass

    async def _poll_once(self) -> list[TradeSignal]:
        """1회 폴링: KRX 조회 → D+2 필터 → 시그널 생성."""
        today = datetime.now(_KST).date()
        lookback = self._config.short_overheating_lookback_days
        start_date = today - timedelta(days=lookback)

        records = await fetch_overheating_records(
            self._session, start_date, today,
        )
        if not records:
            logger.debug("ShortOverheating: no records from KRX")
            return []

        released = filter_released(records)
        logger.info("ShortOverheating: %d released records in lookback", len(released))

        signals: list[TradeSignal] = []
        for record in released:
            if not self._is_entry_today(record, today):
                continue
            if record.overheating_days < self._config.short_overheating_min_overheating_days:
                continue
            # TODO: 하락폭은 pykrx로 조회 가능하지만, 초기 버전에서는 0.0으로 처리
            signal = self._build_signal(record, drop_pct=0.0)
            signals.append(signal)
            self._signaled.add(self._signal_key(record))
            logger.info(
                "ShortOverheating signal: %s %s (confidence=%d, days=%d)",
                record.ticker, record.corp_name, signal.confidence, record.overheating_days,
            )

        return signals

    def _is_entry_today(self, record: OverheatingRecord, today: date) -> bool:
        """오늘이 해당 레코드의 D+N 진입일인지 판별."""
        if self._signal_key(record) in self._signaled:
            return False
        entry = calc_entry_date(record.release_date, self._config.short_overheating_d_offset)
        return entry == today

    def _build_signal(self, record: OverheatingRecord, drop_pct: float = 0.0) -> TradeSignal:
        """OverheatingRecord → TradeSignal 변환."""
        confidence = score_overheating_confidence(
            overheating_days=record.overheating_days,
            drop_pct=drop_pct,
            base=self._config.short_overheating_base_confidence,
        )
        reason = f"공매도 과열 해제 D+{self._config.short_overheating_d_offset} ({record.overheating_days}일 지정)"
        return TradeSignal(
            strategy_name="short_overheating",
            source=SignalSource.TECHNICAL,
            ticker=record.ticker,
            corp_name=record.corp_name,
            action=Action.BUY,
            confidence=confidence,
            size_hint=_size_hint(confidence),
            reason=reason,
            headline=f"공매도 과열 해제: {record.corp_name}",
            event_id=f"soh_{record.ticker}_{record.release_date.strftime('%Y%m%d')}",
            detected_at=datetime.now(_KST),
            metadata={
                "overheating_days": record.overheating_days,
                "designation_date": record.designation_date.isoformat(),
                "release_date": record.release_date.isoformat(),
                "drop_pct": drop_pct,
            },
        )

    @staticmethod
    def _signal_key(record: OverheatingRecord) -> str:
        return f"{record.ticker}_{record.release_date.strftime('%Y%m%d')}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_short_overheating_strategy.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/kindshot/short_overheating_strategy.py tests/test_short_overheating_strategy.py
git commit -m "feat: ShortOverheatingStrategy — 공매도 과열 해제 D+2 매수 전략"
```

---

### Task 5: main.py 전략 등록

**Files:**
- Modify: `src/kindshot/main.py`

- [ ] **Step 1: Add import at top of main.py**

```python
from kindshot.short_overheating_strategy import ShortOverheatingStrategy
```

- [ ] **Step 2: Add strategy registration in `_build_strategy_registry()` after DART earnings block (after line ~152)**

```python
    # 공매도 과열 해제 전략
    if config.short_overheating_enabled and session:
        overheating_strategy = ShortOverheatingStrategy(
            config, session, stop_event=stop_event,
        )
        strategy_registry.register(overheating_strategy)
        if overheating_strategy.enabled:
            has_signal_strategies = True
        logger.info("ShortOverheatingStrategy registered (enabled=%s)", overheating_strategy.enabled)
    elif config.short_overheating_enabled:
        logger.warning("ShortOverheatingStrategy requested but session unavailable")
```

- [ ] **Step 3: Run full test suite**

Run: `pytest -x -q --timeout=30`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add src/kindshot/main.py
git commit -m "feat: ShortOverheatingStrategy main.py 등록"
```

---

### Task 6: 통합 테스트 + 최종 검증

**Files:**
- Modify: `tests/test_short_overheating_strategy.py`

- [ ] **Step 1: Add integration test for poll_once with mocked KRX**

```python
# tests/test_short_overheating_strategy.py 에 추가

class TestPollOnce:
    @pytest.mark.asyncio
    async def test_poll_generates_signal_on_entry_day(self):
        """D+2 진입일에 시그널이 생성되는지 확인."""
        config = _make_config(d_offset=2, lookback_days=7)
        session = MagicMock()
        strategy = ShortOverheatingStrategy(config, session=session)

        # 수요일 해제 → 금요일(27일)이 D+2
        record = _make_record(release_date=date(2026, 3, 25), overheating_days=3)
        mock_today = date(2026, 3, 27)

        with patch("kindshot.short_overheating_strategy.fetch_overheating_records") as mock_fetch, \
             patch("kindshot.short_overheating_strategy.datetime") as mock_dt:
            mock_fetch.return_value = [record]
            mock_now = MagicMock()
            mock_now.date.return_value = mock_today
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            signals = await strategy._poll_once()

        assert len(signals) == 1
        assert signals[0].ticker == "005930"
        assert signals[0].action == Action.BUY

    @pytest.mark.asyncio
    async def test_poll_no_signal_on_wrong_day(self):
        """D+2가 아닌 날에는 시그널이 생성되지 않아야 함."""
        config = _make_config(d_offset=2, lookback_days=7)
        session = MagicMock()
        strategy = ShortOverheatingStrategy(config, session=session)

        record = _make_record(release_date=date(2026, 3, 25), overheating_days=3)
        mock_today = date(2026, 3, 26)  # D+1

        with patch("kindshot.short_overheating_strategy.fetch_overheating_records") as mock_fetch, \
             patch("kindshot.short_overheating_strategy.datetime") as mock_dt:
            mock_fetch.return_value = [record]
            mock_now = MagicMock()
            mock_now.date.return_value = mock_today
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            signals = await strategy._poll_once()

        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_no_duplicate_signals(self):
        """같은 종목에 대해 중복 시그널이 생성되지 않아야 함."""
        config = _make_config(d_offset=2, lookback_days=7)
        session = MagicMock()
        strategy = ShortOverheatingStrategy(config, session=session)

        record = _make_record(release_date=date(2026, 3, 25), overheating_days=3)
        mock_today = date(2026, 3, 27)

        with patch("kindshot.short_overheating_strategy.fetch_overheating_records") as mock_fetch, \
             patch("kindshot.short_overheating_strategy.datetime") as mock_dt:
            mock_fetch.return_value = [record]
            mock_now = MagicMock()
            mock_now.date.return_value = mock_today
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            signals1 = await strategy._poll_once()
            signals2 = await strategy._poll_once()

        assert len(signals1) == 1
        assert len(signals2) == 0  # 중복 차단
```

- [ ] **Step 2: Run all tests**

Run: `pytest -x -q --timeout=30`
Expected: All PASS

- [ ] **Step 3: Final commit**

```bash
git add tests/test_short_overheating_strategy.py
git commit -m "test: 공매도 과열 해제 전략 통합 테스트"
```

- [ ] **Step 4: Push**

```bash
git push origin main
```
