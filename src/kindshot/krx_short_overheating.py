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
    market: str = "0",
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
    """해제 레코드만 필터링."""
    result = [r for r in records if r.designation_type == "해제"]
    if released_after:
        result = [r for r in result if r.release_date >= released_after]
    return result


# ── D+2 영업일 계산 ──────────────────────────────────────


def calc_entry_date(release_date: date, offset: int = 2) -> date:
    """해제일로부터 D+N 영업일 계산 (주말 제외, 공휴일은 미반영)."""
    current = release_date
    days_added = 0
    while days_added < offset:
        current += timedelta(days=1)
        if current.weekday() < 5:  # 월~금
            days_added += 1
    return current


# ── Confidence Scoring ──────────────────────────────────

_BASE_CONFIDENCE = 60

_OVERHEATING_DAYS_THRESHOLDS = [
    (5, 10),   # 5일+ → +10
    (3, 5),    # 3일+ → +5
]

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

    for threshold, bonus in _OVERHEATING_DAYS_THRESHOLDS:
        if overheating_days >= threshold:
            score += bonus
            break

    for threshold, bonus in _DROP_PCT_THRESHOLDS:
        if drop_pct <= threshold:
            score += bonus
            break

    return min(score, 100)
