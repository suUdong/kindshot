"""KIND RSS adaptive polling with ETag, jitter, and backoff."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncIterator, Optional

import aiohttp
import feedparser

from kindshot.config import Config
from kindshot.kis_client import KisClient, NewsDisclosure
from kindshot.poll_trace import get_tracer

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

    def _state_file(self) -> Optional[Path]:
        if not self._state_dir or not self._current_date:
            return None
        return self._state_dir / f"kis_feed_{self._current_date}.json"

    def _load_state(self) -> None:
        kst = timezone(timedelta(hours=9))
        self._current_date = datetime.now(kst).strftime("%Y%m%d")
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
        self._last_poll_at = datetime.now(timezone(timedelta(hours=9)))
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
        _KST = timezone(timedelta(hours=9))
        now = datetime.now(_KST)
        results: list[RawDisclosure] = []
        seen_dup = 0
        noise_filtered = 0

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
