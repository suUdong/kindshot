"""KIND RSS adaptive polling with ETag, jitter, and backoff."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

import aiohttp
import feedparser

from kindshot.config import Config

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

    def stop(self) -> None:
        """Signal the feed to stop polling."""
        self._stop_event.set()

    def _is_market_hours(self) -> bool:
        from datetime import time as dt_time, timezone as tz, timedelta
        kst = tz(timedelta(hours=9))
        now = datetime.now(kst).time()
        return dt_time(9, 0) <= now <= dt_time(15, 30)

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
        now = datetime.now(timezone.utc)
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
