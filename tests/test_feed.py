"""Tests for KIND RSS feed: ETag 304, backoff, recovery."""

import asyncio
import time
from collections import OrderedDict
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aioresponses import aioresponses
import aiohttp

from kindshot.config import Config
from kindshot.feed import KindFeed, KisFeed, _extract_ticker_corp, _extract_kind_uid
from kindshot.kis_client import NewsDisclosure


def _news_item(
    news_id: str,
    data_dt: str,
    data_tm: str,
    title: str,
    *,
    ticker: str = "005930",
    dorg: str = "한국거래소",
) -> NewsDisclosure:
    return NewsDisclosure(
        news_id=news_id,
        data_dt=data_dt,
        data_tm=data_tm,
        title=title,
        dorg=dorg,
        tickers=(ticker,) if ticker else (),
    )


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
    # detected_at should be KST (UTC+9)
    from datetime import timedelta, timezone
    kst = timezone(timedelta(hours=9))
    assert items[0].detected_at.tzinfo is not None
    assert items[0].detected_at.utcoffset() == timedelta(hours=9)


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


async def test_weekend_is_off_market():
    """Weekend should always return off-market hours."""
    cfg = Config()
    from datetime import datetime as dt, timedelta, timezone
    kst = timezone(timedelta(hours=9))
    # Saturday 10:00 KST
    saturday_10am = dt(2026, 3, 14, 10, 0, 0, tzinfo=kst)  # 2026-03-14 is a Saturday
    with patch("kindshot.feed.datetime") as mock_dt:
        mock_dt.now.return_value = saturday_10am
        mock_dt.side_effect = lambda *args, **kw: dt(*args, **kw)
        async with aiohttp.ClientSession() as session:
            feed = KindFeed(cfg, session)
            assert feed._is_market_hours() is False


async def test_stream_stop_interrupts_sleep():
    """stop() should break stream sleep without waiting full polling interval."""
    cfg = Config(feed_interval_market_s=30.0, feed_interval_off_s=30.0)
    async with aiohttp.ClientSession() as session:
        feed = KindFeed(cfg, session)
        feed.poll_once = AsyncMock(return_value=[])  # type: ignore[method-assign]

        async def _consume() -> None:
            async for _batch in feed.stream():
                pass

        task = asyncio.create_task(_consume())
        await asyncio.sleep(0.05)
        t0 = time.monotonic()
        feed.stop()
        await asyncio.wait_for(task, timeout=0.5)
        assert time.monotonic() - t0 < 0.5


async def test_kind_feed_updates_last_poll_at():
    cfg = Config()
    async with aiohttp.ClientSession() as session:
        feed = KindFeed(cfg, session)
        assert feed.last_poll_at is None
        with aioresponses() as m:
            m.get(cfg.kind_rss_url, body=SAMPLE_RSS, status=200)
            await feed.poll_once()
        assert feed.last_poll_at is not None


async def test_kis_feed_updates_last_poll_at():
    cfg = Config()
    kis = AsyncMock()
    kis.get_news_disclosure_items = AsyncMock(return_value=[])
    feed = KisFeed(cfg, kis)
    assert feed.last_poll_at is None
    await feed.poll_once()
    assert feed.last_poll_at is not None


async def test_kis_feed_last_time_updates_on_noise():
    """_last_time should advance even for noise items that get filtered."""
    cfg = Config()
    kis = AsyncMock()
    kis.get_news_disclosure_items = AsyncMock(return_value=[
        _news_item("NOISE001", "20260311", "143000", "삼성전자(005930) - 급등 관련 뉴스")
    ])
    feed = KisFeed(cfg, kis)
    results = await feed.poll_once()
    # Item is filtered out (noise pattern "급등")
    assert results == []
    # But _last_time still advances so next poll starts from latest
    assert feed._last_time == "143000"


async def test_kis_feed_filters_non_disclosure_word_fragment_noise():
    cfg = Config()
    kis = AsyncMock()
    kis.get_news_disclosure_items = AsyncMock(return_value=[
        _news_item(
            "NOISE002",
            "20260313",
            "092333",
            "[특징주] 비료株, 호르무즈 해협 봉쇄로 공급망 불안해지자 강세",
            ticker="001550",
            dorg="연합뉴스",
        )
    ])
    feed = KisFeed(cfg, kis)

    results = await feed.poll_once()

    assert results == []
    assert feed._last_time == "092333"


async def test_kis_feed_keeps_contract_termination_keyword():
    cfg = Config()
    kis = AsyncMock()
    kis.get_news_disclosure_items = AsyncMock(return_value=[
        _news_item(
            "DISC001",
            "20260313",
            "101500",
            "A사, 공급계약 해지 결정",
            ticker="123456",
            dorg="연합뉴스",
        )
    ])
    feed = KisFeed(cfg, kis)

    results = await feed.poll_once()

    assert len(results) == 1
    assert results[0].ticker == "123456"


async def test_kis_feed_sorts_items_deterministically():
    cfg = Config()
    kis = AsyncMock()
    kis.get_news_disclosure_items = AsyncMock(return_value=[
        _news_item("NEWS002", "20260312", "143100", "삼성전자(005930) 공급계약 체결"),
        _news_item("NEWS001", "20260312", "143000", "삼성전자(005930) 공급계약 체결"),
    ])
    feed = KisFeed(cfg, kis)

    results = await feed.poll_once()

    assert [item.rss_guid for item in results] == ["NEWS001", "NEWS002"]
    assert feed._last_time == "143100"


async def test_kis_feed_always_queries_without_from_time():
    """KIS API returns items BEFORE from_time, so we always send empty string."""
    cfg = Config()
    kis = AsyncMock()
    kis.get_news_disclosure_items = AsyncMock(return_value=[])
    feed = KisFeed(cfg, kis)
    feed._last_time = "143000"

    await feed.poll_once()

    kis.get_news_disclosure_items.assert_awaited_once_with(from_time="")


async def test_kis_feed_state_persists_across_restart():
    cfg = Config()
    kis = AsyncMock()
    kis.get_news_disclosure_items = AsyncMock(return_value=[
        _news_item("NEWS001", "20260312", "143000", "삼성전자(005930) 공급계약 체결")
    ])
    files: dict[str, str] = {}
    state_dir = Path("virtual-feed-state")

    def _exists(path_self: Path) -> bool:
        return str(path_self) in files

    def _read_text(path_self: Path, encoding: str = "utf-8") -> str:
        return files[str(path_self)]

    def _write_text(path_self: Path, data: str, encoding: str = "utf-8") -> int:
        files[str(path_self)] = data
        return len(data)

    with patch.object(Path, "mkdir", autospec=True) as mock_mkdir, \
         patch.object(Path, "exists", autospec=True, side_effect=_exists), \
         patch.object(Path, "read_text", autospec=True, side_effect=_read_text), \
         patch.object(Path, "write_text", autospec=True, side_effect=_write_text):
        feed1 = KisFeed(cfg, kis, state_dir=state_dir)
        results = await feed1.poll_once()
        assert len(results) == 1

        feed2 = KisFeed(cfg, kis, state_dir=state_dir)
        assert feed2._last_time == "143000"
        assert "NEWS001" in feed2._seen_ids
        assert mock_mkdir.called


async def test_kis_feed_state_resets_on_new_day():
    cfg = Config()
    kis = AsyncMock()
    state_dir = Path("virtual-feed-state")
    files = {
        str(state_dir / "kis_feed_20260311.json"): '{"last_time":"143000","seen_ids":["NEWS001"]}',
    }

    def _exists(path_self: Path) -> bool:
        return str(path_self) in files

    def _read_text(path_self: Path, encoding: str = "utf-8") -> str:
        return files[str(path_self)]

    def _write_text(path_self: Path, data: str, encoding: str = "utf-8") -> int:
        files[str(path_self)] = data
        return len(data)

    with patch.object(Path, "mkdir", autospec=True), \
         patch.object(Path, "exists", autospec=True, side_effect=_exists), \
         patch.object(Path, "read_text", autospec=True, side_effect=_read_text), \
         patch.object(Path, "write_text", autospec=True, side_effect=_write_text), \
         patch("kindshot.feed.datetime") as mock_dt:
        real_datetime = __import__("datetime").datetime
        kst = __import__("datetime").timezone(__import__("datetime").timedelta(hours=9))
        mock_dt.now.return_value = real_datetime(2026, 3, 12, 9, 0, 0, tzinfo=kst)
        mock_dt.strptime.side_effect = real_datetime.strptime
        mock_dt.side_effect = lambda *args, **kwargs: real_datetime(*args, **kwargs)

        feed = KisFeed(cfg, kis, state_dir=state_dir)
        feed._prune_if_new_day(real_datetime(2026, 3, 12, 9, 0, 0, tzinfo=kst))

    assert feed._last_time == ""
    assert feed._seen_ids == OrderedDict()
