"""News/disclosure feed sources: KIND RSS, KIS API, DART OpenAPI, MultiFeed compositor."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncIterator, Optional, Protocol

import aiohttp
import feedparser

from kindshot.config import Config
from kindshot.kis_client import KisClient, NewsDisclosure
from kindshot.poll_trace import get_tracer
from kindshot.tz import KST as _KST

logger = logging.getLogger(__name__)


@dataclass
class RawDisclosure:
    """Parsed RSS item before event_id generation."""

    title: str
    link: str
    rss_guid: Optional[str]
    published: Optional[str]  # raw pubDate string
    ticker: str
    corp_name: str
    detected_at: datetime
    dorg: str = ""  # 공시/뉴스 제공기관 (KIS: dorg field)


def _extract_ticker_corp(title: str) -> tuple[str, str]:
    """Extract ticker and corp name from KIND title format.

    KIND titles often look like: "삼성전자(005930) - 공급계약 체결"
    or the ticker/corp may be in other fields. This is a best-effort parse.
    """
    # Pattern: 회사명(종목코드)
    m = re.search(r"(.+?)\((\d{6})\)", title)
    if m:
        return m.group(2), m.group(1).strip()
    return "", ""


def _extract_kind_uid(link: str) -> Optional[str]:
    """Extract unique ID from KIND link URL."""
    # e.g. rcpNo=20260305000123 or similar param
    m = re.search(r"rcpNo=(\d+)", link)
    if m:
        return m.group(1)
    # Try generic unique-ish path segment
    m = re.search(r"/(\d{14,20})", link)
    if m:
        return m.group(1)
    return None


class KindFeed:
    """Adaptive KIND RSS poller."""

    def __init__(self, config: Config, session: aiohttp.ClientSession) -> None:
        self._config = config
        self._session = session
        self._etag: Optional[str] = None
        self._last_modified: Optional[str] = None
        self._consecutive_failures = 0
        self._stop_event = asyncio.Event()
        self._last_poll_at: Optional[datetime] = None

    @property
    def last_poll_at(self) -> Optional[datetime]:
        return self._last_poll_at

    def stop(self) -> None:
        """Signal the feed to stop polling."""
        self._stop_event.set()

    def _is_market_hours(self) -> bool:
        from datetime import time as dt_time
        now_kst = datetime.now(_KST)
        # Weekend: always off-market
        if now_kst.weekday() >= 5:
            return False
        return dt_time(9, 0) <= now_kst.time() <= dt_time(15, 30)

    def _base_interval(self) -> float:
        if self._is_market_hours():
            return self._config.feed_interval_market_s
        return self._config.feed_interval_off_s

    def _interval_with_backoff(self) -> float:
        base = self._base_interval()
        if self._consecutive_failures >= self._config.feed_backoff_threshold:
            multiplier = 2 ** (self._consecutive_failures - self._config.feed_backoff_threshold + 1)
            base = min(base * multiplier, self._config.feed_backoff_max_s)
        # jitter ±20%
        jitter = base * self._config.feed_jitter_pct
        return base + random.uniform(-jitter, jitter)

    async def poll_once(self) -> list[RawDisclosure]:
        """Single poll. Returns list of new disclosures (may be empty on 304)."""
        self._last_poll_at = datetime.now(_KST)
        headers: dict[str, str] = {}
        if self._etag:
            headers["If-None-Match"] = self._etag
        if self._last_modified:
            headers["If-Modified-Since"] = self._last_modified

        try:
            async with self._session.get(
                self._config.kind_rss_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 304:
                    self._consecutive_failures = 0
                    return []

                if resp.status != 200:
                    self._consecutive_failures += 1
                    logger.warning("KIND RSS status=%d", resp.status)
                    return []

                self._etag = resp.headers.get("ETag")
                self._last_modified = resp.headers.get("Last-Modified")
                self._consecutive_failures = 0

                body = await resp.text()

        except Exception:
            self._consecutive_failures += 1
            logger.exception("KIND RSS fetch error")
            return []

        feed = feedparser.parse(body)
        now = datetime.now(_KST)
        results: list[RawDisclosure] = []

        for entry in feed.entries:
            title = getattr(entry, "title", "")
            link = getattr(entry, "link", "")
            guid = getattr(entry, "id", None)
            published = getattr(entry, "published", None)
            ticker, corp = _extract_ticker_corp(title)

            results.append(
                RawDisclosure(
                    title=title,
                    link=link,
                    rss_guid=guid,
                    published=published,
                    ticker=ticker,
                    corp_name=corp,
                    detected_at=now,
                )
            )

        return results

    async def stream(self) -> AsyncIterator[list[RawDisclosure]]:
        """Polling loop yielding batches of disclosures until stopped."""
        while not self._stop_event.is_set():
            items = await self.poll_once()
            if items:
                yield items
            if self._stop_event.is_set():
                break

            # Interruptible sleep for fast shutdown.
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._interval_with_backoff(),
                )
            except asyncio.TimeoutError:
                pass  # Normal wakeup — interruptible sleep via stop_event


class KisFeed:
    """KIS API-based disclosure feed. AWS-compatible alternative to KindFeed."""

    def __init__(self, config: Config, kis: KisClient, *, state_dir: Optional[Path] = None) -> None:
        self._config = config
        self._kis = kis
        self._seen_ids: OrderedDict[str, None] = OrderedDict()
        self._last_time: str = ""  # HHMMSS for incremental polling
        self._consecutive_failures = 0
        self._stop_event = asyncio.Event()
        self._last_poll_at: Optional[datetime] = None
        self._state_dir = state_dir
        self._current_date: Optional[str] = None
        if state_dir:
            state_dir.mkdir(parents=True, exist_ok=True)
            self._load_state()

    @property
    def last_poll_at(self) -> Optional[datetime]:
        return self._last_poll_at

    def stop(self) -> None:
        self._stop_event.set()

    def _is_market_hours(self) -> bool:
        from datetime import time as dt_time
        now_kst = datetime.now(_KST)
        if now_kst.weekday() >= 5:
            return False
        return dt_time(9, 0) <= now_kst.time() <= dt_time(15, 30)

    def _base_interval(self) -> float:
        if self._is_market_hours():
            return self._config.feed_interval_market_s
        return self._config.feed_interval_off_s

    def _interval_with_backoff(self) -> float:
        base = self._base_interval()
        if self._consecutive_failures >= self._config.feed_backoff_threshold:
            multiplier = 2 ** (self._consecutive_failures - self._config.feed_backoff_threshold + 1)
            base = min(base * multiplier, self._config.feed_backoff_max_s)
        jitter = base * self._config.feed_jitter_pct
        return base + random.uniform(-jitter, jitter)

    def _state_file(self) -> Optional[Path]:
        if not self._state_dir or not self._current_date:
            return None
        return self._state_dir / f"kis_feed_{self._current_date}.json"

    def _load_state(self) -> None:
        self._current_date = datetime.now(_KST).strftime("%Y%m%d")
        state_file = self._state_file()
        if not state_file or not state_file.exists():
            return
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            self._last_time = data.get("last_time", "")
            seen_ids = data.get("seen_ids", [])
            if isinstance(seen_ids, list):
                self._seen_ids = OrderedDict((str(news_id), None) for news_id in seen_ids if news_id)
            logger.info(
                "Loaded KIS feed state from %s (last_time=%s, seen_ids=%d)",
                state_file.name,
                self._last_time or "-",
                len(self._seen_ids),
            )
        except Exception:
            logger.exception("Failed to load KIS feed state from %s", state_file)

    def _persist_state(self) -> None:
        state_file = self._state_file()
        if not state_file:
            return
        try:
            payload = {
                "last_time": self._last_time,
                "seen_ids": list(self._seen_ids.keys()),
            }
            state_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception:
            logger.exception("Failed to persist KIS feed state to %s", state_file)

    def _prune_if_new_day(self, now: datetime) -> None:
        today = now.strftime("%Y%m%d")
        if self._current_date is None:
            self._current_date = today
            return
        if today == self._current_date:
            return
        self._current_date = today
        self._last_time = ""
        self._seen_ids.clear()
        self._persist_state()

    def _query_from_time(self) -> str:
        """Use a small overlap window to avoid missing edge-of-second items."""
        if not self._last_time:
            return ""
        try:
            base = datetime.strptime(self._last_time, "%H%M%S")
        except ValueError:
            logger.debug("Unparseable last_time for overlap: %s", self._last_time)
            return self._last_time

        overlapped = base - timedelta(seconds=self._config.feed_overlap_s)
        if overlapped.date() < base.date():
            return ""
        return overlapped.strftime("%H%M%S")

    def _sorted_news_items(self, items: list[NewsDisclosure]) -> list[NewsDisclosure]:
        return sorted(
            items,
            key=lambda item: (item.data_dt, item.data_tm, item.news_id),
        )

    async def poll_once(self) -> list[RawDisclosure]:
        """Single poll via KIS news-title API."""
        self._last_poll_at = datetime.now(_KST)
        self._prune_if_new_day(self._last_poll_at)
        tracer = get_tracer()
        last_time_before = self._last_time
        # KIS API: FID_INPUT_HOUR_1 returns items BEFORE that time, not after.
        # Always send empty string to get the latest news; rely on seen_ids for dedup.
        t_poll = tracer.poll_start(from_time="") if tracer else None
        try:
            items = await self._kis.get_news_disclosure_items(
                from_time="",
            )
        except Exception:
            self._consecutive_failures += 1
            logger.exception("KIS disclosure fetch error")
            if tracer and t_poll is not None:
                tracer.poll_end(
                    t_poll,
                    0,
                    error="exception",
                    last_time_before=last_time_before,
                    last_time_after=self._last_time,
                )
            return []

        items = self._sorted_news_items(items)
        raw_count = len(items) if items else 0
        raw_times = sorted(item.data_tm for item in items if item.data_tm)
        raw_min_time = raw_times[0] if raw_times else ""
        raw_max_time = raw_times[-1] if raw_times else ""

        if not items:
            # Empty response is normal during quiet hours, not a failure
            self._consecutive_failures = 0
            if tracer and t_poll is not None:
                tracer.poll_end(
                    t_poll,
                    0,
                    raw_count=0,
                    last_time_before=last_time_before,
                    last_time_after=self._last_time,
                )
            return []

        self._consecutive_failures = 0
        now = datetime.now(_KST)
        results: list[RawDisclosure] = []
        seen_dup = 0
        noise_filtered = 0

        # Noise patterns: general news, price alerts, rankings, market commentary
        _NOISE_PATTERNS = (
            "상승세", "하락세", "상승폭", "하락폭", "급등", "급락",
            "연속 상승", "연속 하락", "거래일 연속",
            "매수체결 상위", "매도체결 상위", "거래량 상위",
            "1억원 이상", "5억원 이상",
            "소폭 ", "대폭 ",
            "52주 신고가", "52주 신저가",
            # 기관/외인 수급 뉴스 (공시 아님)
            "기관 순매수", "기관 순매도", "외인 순매수", "외인 순매도",
            "외국인 순매수", "외국인 순매도",
            "프로그램 매매", "프로그램매매",
            # 시세/차트 패턴 기사
            "돌파 시도", "저항선", "지지선", "이동평균",
            "골든크로스", "데드크로스",
            "갭 상승", "갭 하락", "갭상승", "갭하락",
            # 증권가 시황/전망
            "증시 전망", "증시전망", "장 마감", "장마감",
            "개장 전", "개장전", "장 시작", "장시작",
            "마감시황", "마감 시황",
            # 테마/섹터 단순 분류
            "테마주", "관련주", "수혜주", "대장주",
        )
        # Disclosure keywords that indicate real corporate events
        _DISCLOSURE_KEYWORDS = (
            "수주", "공급계약", "계약 체결", "계약체결",
            "유상증자", "유증", "CB발행", "전환사채",
            "자사주", "자기주식",
            "합병", "분할", "인수", "합작",
            "소송", "규제",
            "공급계약 해지", "공급 계약 해지", "계약 해지",
            "신탁계약 해지", "신탁 계약 해지", "신탁 해지",
            "기술이전 계약 해지",
            "철회", "취소",
            "실적", "매출", "영업이익",
            "신규사업", "대형계약",
            "주주총회", "이사회", "배당",
            "공시", "정정", "블록딜", "대주주",
            "임상", "승인", "허가", "특허",
            # 추가 공시 키워드
            "MOU", "양해각서", "업무협약",
            "상장폐지", "관리종목",
            "무상증자", "감자", "무상감자", "유상감자",
            "최대주주 변경", "경영권",
            "자금조달", "사채", "신주인수권",
            "분기보고서", "반기보고서", "사업보고서",
            "영업양수", "영업양도", "사업양수", "사업양도",
            "투자판단 관련", "조회공시",
            "풍력", "태양광", "2차전지", "반도체",  # 산업 촉매 키워드
        )

        for item in items:
            # Update last_time BEFORE dup check so polling window always advances
            data_tm = item.data_tm
            if data_tm and data_tm > self._last_time:
                self._last_time = data_tm

            news_id = item.news_id
            if not news_id or news_id in self._seen_ids:
                seen_dup += 1
                continue

            self._seen_ids[news_id] = None

            title = item.title

            # Skip noise: price alerts, rankings, general news
            if any(p in title for p in _NOISE_PATTERNS):
                noise_filtered += 1
                continue

            # Must contain disclosure-relevant keyword or be from disclosure source
            dorg = item.dorg
            is_disclosure_source = any(dorg.startswith(p) for p in ("거래소", "금감원"))
            has_disclosure_keyword = any(kw in title for kw in _DISCLOSURE_KEYWORDS)

            if not is_disclosure_source and not has_disclosure_keyword:
                noise_filtered += 1
                continue

            # Extract first non-empty ticker from iscd1~iscd5
            ticker = item.tickers[0] if item.tickers else ""

            # Extract corp name from title (회사명(종목코드) pattern)
            corp_name = ""
            m = re.search(r"(.+?)\((\d{6})\)", title)
            if m:
                corp_name = m.group(1).strip()

            results.append(
                RawDisclosure(
                    title=title,
                    link=f"kis://news/{news_id}",
                    rss_guid=news_id,
                    published=f"{item.data_dt} {data_tm}",
                    ticker=ticker,
                    corp_name=corp_name,
                    detected_at=now,
                    dorg=item.dorg,
                )
            )

        # Cap seen_ids to prevent unbounded growth (FIFO order preserved)
        while len(self._seen_ids) > 5000:
            self._seen_ids.popitem(last=False)

        self._persist_state()

        if tracer and t_poll is not None:
            tracer.poll_end(
                t_poll, len(results),
                raw_count=raw_count,
                seen_dup=seen_dup,
                noise_filtered=noise_filtered,
                last_time_before=last_time_before,
                last_time_after=self._last_time,
                raw_min_time=raw_min_time,
                raw_max_time=raw_max_time,
            )
        return results

    async def stream(self) -> AsyncIterator[list[RawDisclosure]]:
        """Polling loop yielding batches of disclosures until stopped."""
        while not self._stop_event.is_set():
            items = await self.poll_once()
            if items:
                yield items
            if self._stop_event.is_set():
                break
            interval = self._interval_with_backoff()
            tracer = get_tracer()
            t_sleep = tracer.sleep_start(interval) if tracer else None
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=interval,
                )
            except asyncio.TimeoutError:
                pass
            if tracer and t_sleep is not None:
                tracer.sleep_end(t_sleep)


# ── Analyst Report Feed ────────────────────────────────────────────


class AnalystFeed:
    """증권사 리포트/애널리스트 의견 피드. KIS API dorg 기반 필터링.

    리포트는 실시간성이 낮으므로 기본 폴링 간격은 30초.
    장외 시간에는 폴링하지 않는다.
    """

    def __init__(self, config: Config, kis: KisClient) -> None:
        self._config = config
        self._kis = kis
        self._seen_ids: OrderedDict[str, None] = OrderedDict()
        self._stop_event = asyncio.Event()
        self._last_poll_at: Optional[datetime] = None

    @property
    def last_poll_at(self) -> Optional[datetime]:
        return self._last_poll_at

    def stop(self) -> None:
        self._stop_event.set()

    def _is_market_hours(self) -> bool:
        from datetime import time as dt_time
        now_kst = datetime.now(_KST)
        if now_kst.weekday() >= 5:
            return False
        return dt_time(9, 0) <= now_kst.time() <= dt_time(15, 30)

    async def poll_once(self) -> list[RawDisclosure]:
        """증권사 리포트 단일 폴링. 장외 시간에는 빈 목록 반환."""
        self._last_poll_at = datetime.now(_KST)

        if not self._is_market_hours():
            return []

        try:
            items = await self._kis.fetch_analyst_reports()
        except Exception:
            logger.exception("AnalystFeed fetch error")
            return []

        now = datetime.now(_KST)
        results: list[RawDisclosure] = []

        for item in items:
            news_id = item.news_id
            if not news_id or news_id in self._seen_ids:
                continue
            self._seen_ids[news_id] = None

            ticker = item.tickers[0] if item.tickers else ""
            corp_name = ""
            m = re.search(r"(.+?)\((\d{6})\)", item.title)
            if m:
                corp_name = m.group(1).strip()

            results.append(
                RawDisclosure(
                    title=item.title,
                    link=f"kis://news/{news_id}",
                    rss_guid=news_id,
                    published=f"{item.data_dt} {item.data_tm}",
                    ticker=ticker,
                    corp_name=corp_name,
                    detected_at=now,
                    dorg="analyst",
                )
            )

        # seen_ids 상한 (FIFO)
        while len(self._seen_ids) > 5000:
            self._seen_ids.popitem(last=False)

        if results:
            logger.info("AnalystFeed: %d new analyst reports", len(results))
        return results

    async def stream(self) -> AsyncIterator[list[RawDisclosure]]:
        """폴링 루프. stop() 호출 시 종료."""
        while not self._stop_event.is_set():
            items = await self.poll_once()
            if items:
                yield items
            if self._stop_event.is_set():
                break
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._config.analyst_feed_interval_s,
                )
            except asyncio.TimeoutError:
                pass


# ── DART OpenAPI Feed ──────────────────────────────────────────────


_BUYBACK_KEYWORDS = ("자기주식취득결정", "자사주취득", "자기주식 취득 결정")


def _is_buyback_report(report_nm: str) -> bool:
    """report_nm이 자사주 매입 공시인지 판별."""
    return any(kw in report_nm for kw in _BUYBACK_KEYWORDS)


class DartFeed:
    """DART 전자공시 OpenAPI 기반 실시간 공시 피드.

    https://opendart.fss.or.kr/api/list.json 엔드포인트를 폴링하여
    당일 공시를 RawDisclosure로 변환한다.
    """

    def __init__(
        self,
        config: Config,
        session: aiohttp.ClientSession,
        *,
        state_dir: Optional[Path] = None,
        buyback_queue: Optional[asyncio.Queue] = None,
    ) -> None:
        self._config = config
        self._session = session
        self._seen_rcept: OrderedDict[str, None] = OrderedDict()
        self._consecutive_failures = 0
        self._stop_event = asyncio.Event()
        self._last_poll_at: Optional[datetime] = None
        self._state_dir = state_dir
        self._current_date: Optional[str] = None
        self._buyback_queue = buyback_queue  # 자사주 매입 공시 분리 큐
        if state_dir:
            state_dir.mkdir(parents=True, exist_ok=True)
            self._load_state()

    @property
    def last_poll_at(self) -> Optional[datetime]:
        return self._last_poll_at

    def stop(self) -> None:
        self._stop_event.set()

    def _is_market_hours(self) -> bool:
        from datetime import time as dt_time
        now_kst = datetime.now(_KST)
        if now_kst.weekday() >= 5:
            return False
        return dt_time(9, 0) <= now_kst.time() <= dt_time(15, 30)

    def _base_interval(self) -> float:
        if self._is_market_hours():
            return self._config.feed_interval_market_s
        return self._config.feed_interval_off_s

    def _interval_with_backoff(self) -> float:
        base = self._base_interval()
        if self._consecutive_failures >= self._config.feed_backoff_threshold:
            multiplier = 2 ** (self._consecutive_failures - self._config.feed_backoff_threshold + 1)
            base = min(base * multiplier, self._config.feed_backoff_max_s)
        jitter = base * self._config.feed_jitter_pct
        return base + random.uniform(-jitter, jitter)

    # ── state persistence ──

    def _state_file(self) -> Optional[Path]:
        if not self._state_dir or not self._current_date:
            return None
        return self._state_dir / f"dart_feed_{self._current_date}.json"

    def _load_state(self) -> None:
        self._current_date = datetime.now(_KST).strftime("%Y%m%d")
        state_file = self._state_file()
        if not state_file or not state_file.exists():
            return
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            seen = data.get("seen_rcept", [])
            if isinstance(seen, list):
                self._seen_rcept = OrderedDict((str(r), None) for r in seen if r)
            logger.info("Loaded DART feed state (seen_rcept=%d)", len(self._seen_rcept))
        except Exception:
            logger.exception("Failed to load DART feed state")

    def _persist_state(self) -> None:
        state_file = self._state_file()
        if not state_file:
            return
        try:
            payload = {"seen_rcept": list(self._seen_rcept.keys())}
            state_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception:
            logger.exception("Failed to persist DART feed state")

    def _prune_if_new_day(self, now: datetime) -> None:
        today = now.strftime("%Y%m%d")
        if self._current_date is None:
            self._current_date = today
            return
        if today == self._current_date:
            return
        self._current_date = today
        self._seen_rcept.clear()
        self._persist_state()

    async def poll_once(self) -> list[RawDisclosure]:
        """DART list.json 단일 폴링."""
        self._last_poll_at = datetime.now(_KST)
        self._prune_if_new_day(self._last_poll_at)

        api_key = self._config.dart_api_key
        if not api_key:
            return []

        today = self._last_poll_at.strftime("%Y%m%d")
        params = {
            "crtfc_key": api_key,
            "bgn_de": today,
            "end_de": today,
            "page_no": "1",
            "page_count": str(self._config.dart_poll_page_count),
            "sort": "date",
            "sort_mth": "desc",
        }
        url = f"{self._config.dart_base_url}/list.json"

        try:
            async with self._session.get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    self._consecutive_failures += 1
                    logger.warning("DART API status=%d", resp.status)
                    return []
                data = await resp.json(content_type=None)
        except Exception:
            self._consecutive_failures += 1
            logger.exception("DART API fetch error")
            return []

        self._consecutive_failures = 0

        status = data.get("status", "")
        if status == "013":
            # 013 = 조회된 데이터가 없습니다 (정상, 공시 없는 시간대)
            return []
        if status != "000":
            logger.warning("DART API status_code=%s, message=%s", status, data.get("message", ""))
            return []

        items = data.get("list", [])
        now = datetime.now(_KST)
        results: list[RawDisclosure] = []

        for item in items:
            rcept_no = item.get("rcept_no", "")
            if not rcept_no or rcept_no in self._seen_rcept:
                continue
            self._seen_rcept[rcept_no] = None

            stock_code = item.get("stock_code", "").strip()
            if not stock_code:
                continue  # 비상장사 제외

            corp_name = item.get("corp_name", "").strip()
            report_nm = item.get("report_nm", "").strip()
            rcept_dt = item.get("rcept_dt", "")
            flr_nm = item.get("flr_nm", "")  # 공시 제출인

            # DART 제목 = report_nm (e.g. "주요사항보고서(수주공시)")
            # corp_name 포함 형태로 변환하여 기존 bucket 분류와 호환
            title = f"{corp_name}({stock_code}) {report_nm}"

            disc = RawDisclosure(
                title=title,
                link=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
                rss_guid=rcept_no,
                published=rcept_dt,
                ticker=stock_code,
                corp_name=corp_name,
                detected_at=now,
                dorg=f"DART/{flr_nm}" if flr_nm else "DART",
            )

            # 자사주 매입 공시는 별도 큐로 분리 (뉴스 파이프라인 중복 방지)
            if self._buyback_queue is not None and _is_buyback_report(report_nm):
                try:
                    self._buyback_queue.put_nowait(disc)
                    logger.info("DART buyback routed to strategy queue: %s %s", stock_code, report_nm)
                except asyncio.QueueFull:
                    logger.warning("DART buyback queue full, falling back to news pipeline")
                    results.append(disc)
                continue

            results.append(disc)

        # Cap seen set
        while len(self._seen_rcept) > 5000:
            self._seen_rcept.popitem(last=False)

        self._persist_state()
        if results:
            logger.info("DART poll: %d new disclosures", len(results))
        return results

    async def stream(self) -> AsyncIterator[list[RawDisclosure]]:
        """DART 폴링 루프."""
        while not self._stop_event.is_set():
            items = await self.poll_once()
            if items:
                yield items
            if self._stop_event.is_set():
                break
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._interval_with_backoff(),
                )
            except asyncio.TimeoutError:
                pass


# ── MultiFeed compositor ───────────────────────────────────────────


# ── Y2I Signal Feed ──────────────────────────────────────────────


# verdict 서열: 높을수록 강한 시그널
_Y2I_VERDICT_RANK = {"REJECT": 0, "WATCH": 1, "BUY": 2, "STRONG_BUY": 3}


class Y2iFeed:
    """y2i exported signal feed를 폴링하여 KRX 시그널을 RawDisclosure로 변환.

    - KRX 종목(.KS, .KQ)만 필터
    - `kindshot_feed.json`을 기본 계약으로 읽고, legacy `signal_tracker.json`도 호환 지원
    - score >= y2i_min_score && verdict >= y2i_min_verdict 통과한 시그널만 emit
    - 동일 (ticker, signal_date)는 가장 강한 시그널 1건만 emit
    - y2i_lookback_days 이내 시그널만 처리
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._signal_path = Path(config.y2i_signal_path)
        self._seen_keys: OrderedDict[str, None] = OrderedDict()
        self._stop_event = asyncio.Event()
        self._last_poll_at: Optional[datetime] = None
        self._min_verdict_rank = _Y2I_VERDICT_RANK.get(config.y2i_min_verdict.upper(), 1)

    @property
    def last_poll_at(self) -> Optional[datetime]:
        return self._last_poll_at

    def stop(self) -> None:
        self._stop_event.set()

    def _parse_signal_file(self) -> list[dict]:
        """Y2I signal file 파싱. 실패 시 빈 목록 반환."""
        try:
            raw = self._signal_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            return data.get("signals", []) if isinstance(data, dict) else []
        except (OSError, json.JSONDecodeError, KeyError):
            logger.warning("Y2iFeed: failed to read %s", self._signal_path)
            return []

    @staticmethod
    def _signal_channel(sig: dict) -> str:
        return str(sig.get("channel") or sig.get("channel_slug") or "").strip()

    @staticmethod
    def _signal_score(sig: dict) -> float:
        raw_score = sig.get("signal_score")
        if raw_score is not None:
            try:
                return float(raw_score)
            except (TypeError, ValueError):
                return 0.0
        raw_confidence = sig.get("confidence")
        try:
            return float(raw_confidence or 0.0) * 100.0
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _signal_confidence(sig: dict) -> float:
        raw_confidence = sig.get("confidence")
        if raw_confidence is not None:
            try:
                return float(raw_confidence)
            except (TypeError, ValueError):
                return 0.0
        return Y2iFeed._signal_score(sig) / 100.0

    @staticmethod
    def _signal_channel_weight(sig: dict) -> float:
        try:
            return float(sig.get("channel_weight") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _candidate_rank(sig: dict) -> tuple[int, float, float, float, int]:
        verdict = str(sig.get("verdict", "")).upper()
        return (
            1 if bool(sig.get("consensus_signal")) else 0,
            Y2iFeed._signal_score(sig),
            Y2iFeed._signal_confidence(sig),
            Y2iFeed._signal_channel_weight(sig),
            _Y2I_VERDICT_RANK.get(verdict, 0),
        )

    @staticmethod
    def _extract_krx_ticker(ticker_str: str) -> Optional[str]:
        """'005930.KS' → '005930', 비-KRX는 None."""
        if not ticker_str:
            return None
        if ticker_str.endswith((".KS", ".KQ")):
            code = ticker_str.split(".")[0]
            if re.fullmatch(r"\d{6}", code):
                return code
        return None

    def _qualifies(self, sig: dict) -> bool:
        """score 및 verdict 기준 통과 여부."""
        score = self._signal_score(sig)
        if score < self._config.y2i_min_score:
            return False
        verdict = str(sig.get("verdict", "")).upper()
        return _Y2I_VERDICT_RANK.get(verdict, 0) >= self._min_verdict_rank

    def poll_once(self) -> list[RawDisclosure]:
        """단일 폴링 사이클. 새 KRX 시그널을 RawDisclosure로 변환."""
        self._last_poll_at = datetime.now(_KST)
        signals = self._parse_signal_file()
        if not signals:
            return []

        now = datetime.now(_KST)
        cutoff = (now - timedelta(days=self._config.y2i_lookback_days)).date()
        best_by_key: dict[str, tuple[tuple[int, float, float, float, int], RawDisclosure]] = {}

        for sig in signals:
            ticker_raw = sig.get("ticker", "")
            krx_code = self._extract_krx_ticker(ticker_raw)
            if not krx_code:
                continue

            if not self._qualifies(sig):
                continue

            # 날짜 필터: lookback 이내
            signal_date_str = sig.get("signal_date", "")
            try:
                sig_date = datetime.strptime(signal_date_str, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue
            if sig_date < cutoff:
                continue

            # 중복 제거 키: ticker + signal_date
            dedup_key = f"{krx_code}:{signal_date_str}"
            if dedup_key in self._seen_keys:
                continue

            company = sig.get("company_name", "")
            score = self._signal_score(sig)
            verdict = str(sig.get("verdict", "")).upper()
            channel = self._signal_channel(sig)

            title = f"{company}({krx_code}) [Y2I:{channel}] {verdict} score={score:.0f}"

            raw_item = RawDisclosure(
                title=title,
                link=f"y2i://signal/{krx_code}/{signal_date_str}",
                rss_guid=dedup_key,
                published=signal_date_str,
                ticker=krx_code,
                corp_name=company,
                detected_at=now,
                dorg="y2i",
            )
            rank = self._candidate_rank(sig)
            existing = best_by_key.get(dedup_key)
            if existing is None or rank > existing[0]:
                best_by_key[dedup_key] = (rank, raw_item)

        results = [item for _, item in best_by_key.values()]
        results.sort(key=lambda item: ((item.published or ""), item.ticker, item.title), reverse=True)

        for item in results:
            if item.rss_guid:
                self._seen_keys[item.rss_guid] = None

        # seen_keys 상한
        while len(self._seen_keys) > 5000:
            self._seen_keys.popitem(last=False)

        if results:
            logger.info("Y2iFeed: %d new KRX signals (score≥%.0f)", len(results), self._config.y2i_min_score)
        return results

    async def stream(self) -> AsyncIterator[list[RawDisclosure]]:
        """폴링 루프. stop() 호출 시 종료."""
        while not self._stop_event.is_set():
            items = self.poll_once()
            if items:
                yield items
            if self._stop_event.is_set():
                break
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._config.y2i_poll_interval_s,
                )
            except asyncio.TimeoutError:
                pass


class MultiFeed:
    """여러 피드를 병렬 폴링하고 교차 중복제거하여 단일 스트림으로 합성.

    Usage::

        feeds = [KisFeed(...), DartFeed(...)]
        multi = MultiFeed(feeds, config)
        async for batch in multi.stream():
            ...  # merged, deduplicated RawDisclosure batch
    """

    def __init__(self, feeds: list, config: Config) -> None:
        self._feeds = feeds
        self._config = config
        self._stop_event = asyncio.Event()
        # 교차 중복제거: (ticker, title_prefix) → seen
        self._cross_seen: OrderedDict[str, None] = OrderedDict()

    @property
    def last_poll_at(self) -> Optional[datetime]:
        """가장 최근에 폴링한 피드의 시각."""
        times = [f.last_poll_at for f in self._feeds if f.last_poll_at]
        return max(times) if times else None

    def stop(self) -> None:
        self._stop_event.set()
        for feed in self._feeds:
            feed.stop()

    def _dedup_key(self, item: RawDisclosure) -> str:
        """교차 중복제거 키: ticker + 제목 앞 30자 정규화."""
        # 괄호/종목코드 제거 후 앞 30자
        title_norm = re.sub(r"\(?\d{6}\)?", "", item.title).strip()[:30]
        return f"{item.ticker}:{title_norm}"

    def _cross_dedup(self, items: list[RawDisclosure]) -> list[RawDisclosure]:
        """교차 소스 중복 제거."""
        result = []
        for item in items:
            key = self._dedup_key(item)
            if key in self._cross_seen:
                continue
            self._cross_seen[key] = None
            result.append(item)

        # Cap cross_seen
        while len(self._cross_seen) > 10000:
            self._cross_seen.popitem(last=False)

        return result

    async def stream(self) -> AsyncIterator[list[RawDisclosure]]:
        """모든 피드를 병렬로 폴링, 합산 배치를 yield."""
        queue: asyncio.Queue[list[RawDisclosure]] = asyncio.Queue()

        async def _feed_pump(feed: object) -> None:
            try:
                async for batch in feed.stream():  # type: ignore[union-attr]
                    await queue.put(batch)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("MultiFeed pump error for %s", type(feed).__name__)

        tasks = [asyncio.create_task(_feed_pump(f), name=f"multifeed-{type(f).__name__}") for f in self._feeds]

        try:
            while not self._stop_event.is_set():
                try:
                    batch = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                deduped = self._cross_dedup(batch)
                if deduped:
                    yield deduped
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
