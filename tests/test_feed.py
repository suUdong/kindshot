"""Tests for KIND RSS feed: ETag 304, backoff, recovery."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aioresponses import aioresponses
import aiohttp

from kindshot.config import Config
from kindshot.feed import KindFeed, _extract_ticker_corp, _extract_kind_uid


def test_extract_ticker_corp():
    ticker, corp = _extract_ticker_corp("삼성전자(005930) - 공급계약 체결")
    assert ticker == "005930"
    assert corp == "삼성전자"


def test_extract_ticker_corp_no_match():
    ticker, corp = _extract_ticker_corp("random text")
    assert ticker == ""
    assert corp == ""


def test_extract_kind_uid():
    assert _extract_kind_uid("https://kind.krx.co.kr/?rcpNo=20260305000123") == "20260305000123"
    assert _extract_kind_uid("https://example.com/no-uid") is None


SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<item>
  <title>삼성전자(005930) - 공급계약 체결</title>
  <link>https://kind.krx.co.kr/?rcpNo=20260305000001</link>
  <guid>guid1</guid>
  <pubDate>Wed, 05 Mar 2026 09:12:04 +0900</pubDate>
</item>
</channel>
</rss>"""


async def test_poll_once_200():
    cfg = Config()
    async with aiohttp.ClientSession() as session:
        feed = KindFeed(cfg, session)
        with aioresponses() as m:
            m.get(cfg.kind_rss_url, body=SAMPLE_RSS, status=200)
            items = await feed.poll_once()
    assert len(items) == 1
    assert items[0].ticker == "005930"


async def test_poll_once_304_returns_empty():
    cfg = Config()
    async with aiohttp.ClientSession() as session:
        feed = KindFeed(cfg, session)
        with aioresponses() as m:
            m.get(cfg.kind_rss_url, status=304)
            items = await feed.poll_once()
    assert items == []
    assert feed._consecutive_failures == 0


async def test_poll_once_500_increments_failures():
    cfg = Config()
    async with aiohttp.ClientSession() as session:
        feed = KindFeed(cfg, session)
        with aioresponses() as m:
            m.get(cfg.kind_rss_url, status=500)
            items = await feed.poll_once()
    assert items == []
    assert feed._consecutive_failures == 1


async def test_backoff_increases_interval():
    cfg = Config(feed_backoff_threshold=3)
    async with aiohttp.ClientSession() as session:
        feed = KindFeed(cfg, session)
        base = feed._base_interval()

        # Below threshold: no backoff
        feed._consecutive_failures = 2
        interval = feed._interval_with_backoff()
        assert abs(interval - base) <= base * cfg.feed_jitter_pct * 1.1

        # At threshold: 2x backoff
        feed._consecutive_failures = 3
        interval = feed._interval_with_backoff()
        assert interval > base  # Should be ~2x base ± jitter


async def test_success_resets_failures():
    cfg = Config()
    async with aiohttp.ClientSession() as session:
        feed = KindFeed(cfg, session)
        feed._consecutive_failures = 5
        with aioresponses() as m:
            m.get(cfg.kind_rss_url, body=SAMPLE_RSS, status=200)
            await feed.poll_once()
    assert feed._consecutive_failures == 0


async def test_etag_sent_on_second_poll():
    cfg = Config()
    async with aiohttp.ClientSession() as session:
        feed = KindFeed(cfg, session)
        with aioresponses() as m:
            m.get(cfg.kind_rss_url, body=SAMPLE_RSS, status=200,
                  headers={"ETag": '"abc123"'})
            await feed.poll_once()

        assert feed._etag == '"abc123"'
