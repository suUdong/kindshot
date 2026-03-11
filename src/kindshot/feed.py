"""KIND RSS adaptive polling with ETag, jitter, and backoff."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Optional

import aiohttp
import feedparser

from kindshot.config import Config
from kindshot.kis_client import KisClient

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
        kst = timezone(timedelta(hours=9))
        now_kst = datetime.now(kst)
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
        self._last_poll_at = datetime.now(timezone(timedelta(hours=9)))
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
        _KST = timezone(timedelta(hours=9))
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
                pass


class KisFeed:
    """KIS API-based disclosure feed. AWS-compatible alternative to KindFeed."""

    def __init__(self, config: Config, kis: KisClient) -> None:
        self._config = config
        self._kis = kis
        self._seen_ids: OrderedDict[str, None] = OrderedDict()
        self._last_time: str = ""  # HHMMSS for incremental polling
        self._consecutive_failures = 0
        self._stop_event = asyncio.Event()
        self._last_poll_at: Optional[datetime] = None

    @property
    def last_poll_at(self) -> Optional[datetime]:
        return self._last_poll_at

    def stop(self) -> None:
        self._stop_event.set()

    def _is_market_hours(self) -> bool:
        from datetime import time as dt_time
        kst = timezone(timedelta(hours=9))
        now_kst = datetime.now(kst)
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

    async def poll_once(self) -> list[RawDisclosure]:
        """Single poll via KIS news-title API."""
        self._last_poll_at = datetime.now(timezone(timedelta(hours=9)))
        try:
            items = await self._kis.get_news_disclosures(
                from_time=self._last_time,
            )
        except Exception:
            self._consecutive_failures += 1
            logger.exception("KIS disclosure fetch error")
            return []

        if not items:
            # Empty response is normal during quiet hours, not a failure
            self._consecutive_failures = 0
            return []

        self._consecutive_failures = 0
        _KST = timezone(timedelta(hours=9))
        now = datetime.now(_KST)
        results: list[RawDisclosure] = []

        # Noise patterns: general news, price alerts, rankings
        _NOISE_PATTERNS = (
            "상승세", "하락세", "상승폭", "하락폭", "급등", "급락",
            "연속 상승", "연속 하락", "거래일 연속",
            "매수체결 상위", "매도체결 상위", "거래량 상위",
            "1억원 이상", "5억원 이상",
            "소폭 ", "대폭 ",
            "52주 신고가", "52주 신저가",
        )
        # Disclosure keywords that indicate real corporate events
        _DISCLOSURE_KEYWORDS = (
            "수주", "공급계약", "계약 체결", "계약체결",
            "유상증자", "유증", "CB발행", "전환사채",
            "자사주", "자기주식",
            "합병", "분할", "인수", "합작",
            "소송", "규제", "해지", "철회", "취소",
            "실적", "매출", "영업이익",
            "신규사업", "대형계약",
            "주주총회", "이사회", "배당",
            "공시", "정정", "블록딜", "대주주",
            "임상", "승인", "허가", "특허",
        )

        for item in items:
            news_id = item.get("cntt_usiq_srno", "")
            if not news_id or news_id in self._seen_ids:
                continue

            # Update last_time BEFORE filtering so next poll starts from latest
            data_tm = item.get("data_tm", "")
            if data_tm and data_tm > self._last_time:
                self._last_time = data_tm

            self._seen_ids[news_id] = None

            title = item.get("hts_pbnt_titl_cntt", "")

            # Skip noise: price alerts, rankings, general news
            if any(p in title for p in _NOISE_PATTERNS):
                continue

            # Must contain disclosure-relevant keyword or be from disclosure source
            dorg = item.get("dorg", "")
            is_disclosure_source = any(dorg.startswith(p) for p in ("거래소", "금감원"))
            has_disclosure_keyword = any(kw in title for kw in _DISCLOSURE_KEYWORDS)

            if not is_disclosure_source and not has_disclosure_keyword:
                continue

            # Extract first non-empty ticker from iscd1~iscd5
            ticker = ""
            for i in range(1, 6):
                t = item.get(f"iscd{i}", "").strip()
                if t and len(t) == 6 and t.isdigit():
                    ticker = t
                    break

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
                    published=f"{item.get('data_dt', '')} {data_tm}",
                    ticker=ticker,
                    corp_name=corp_name,
                    detected_at=now,
                )
            )

        # Cap seen_ids to prevent unbounded growth (FIFO order preserved)
        while len(self._seen_ids) > 5000:
            self._seen_ids.popitem(last=False)

        return results

    async def stream(self) -> AsyncIterator[list[RawDisclosure]]:
        """Polling loop yielding batches of disclosures until stopped."""
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
