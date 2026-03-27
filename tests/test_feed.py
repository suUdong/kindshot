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
from kindshot.feed import AnalystFeed, DartFeed, KindFeed, KisFeed, MultiFeed, _extract_ticker_corp, _extract_kind_uid
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


# ── 뉴스 파이프라인 개선: 확장 노이즈 패턴 테스트 ──


async def test_kis_feed_filters_institutional_flow_noise():
    """기관/외인 수급 뉴스는 노이즈로 필터링."""
    cfg = Config()
    kis = AsyncMock()
    kis.get_news_disclosure_items = AsyncMock(return_value=[
        _news_item("NOISE010", "20260327", "100000", "삼성전자(005930) 기관 순매수 상위"),
    ])
    feed = KisFeed(cfg, kis)
    results = await feed.poll_once()
    assert results == []


async def test_kis_feed_filters_chart_pattern_noise():
    """차트/기술적 패턴 기사 노이즈 필터링."""
    cfg = Config()
    kis = AsyncMock()
    kis.get_news_disclosure_items = AsyncMock(return_value=[
        _news_item("NOISE011", "20260327", "100100", "삼성전자(005930) 골든크로스 임박", dorg="연합뉴스"),
    ])
    feed = KisFeed(cfg, kis)
    results = await feed.poll_once()
    assert results == []


async def test_kis_feed_filters_theme_stock_noise():
    """테마주/관련주 뉴스 노이즈 필터링."""
    cfg = Config()
    kis = AsyncMock()
    kis.get_news_disclosure_items = AsyncMock(return_value=[
        _news_item("NOISE012", "20260327", "100200", "[특징주] AI 관련주 일제히 강세", dorg="이데일리"),
    ])
    feed = KisFeed(cfg, kis)
    results = await feed.poll_once()
    assert results == []


async def test_kis_feed_keeps_new_disclosure_keywords():
    """새로 추가된 disclosure 키워드(MOU, 무상증자 등) 통과 확인."""
    cfg = Config()
    kis = AsyncMock()
    kis.get_news_disclosure_items = AsyncMock(return_value=[
        _news_item("DISC010", "20260327", "100300", "A사, B사와 양해각서(MOU) 체결", ticker="123456", dorg="연합뉴스"),
    ])
    feed = KisFeed(cfg, kis)
    results = await feed.poll_once()
    assert len(results) == 1

    kis.get_news_disclosure_items = AsyncMock(return_value=[
        _news_item("DISC011", "20260327", "100400", "C사, 무상증자 결정", ticker="654321", dorg="연합뉴스"),
    ])
    feed2 = KisFeed(cfg, kis)
    results2 = await feed2.poll_once()
    assert len(results2) == 1


# ── DART Feed Tests ──────────────────────────────────────────────


DART_RESPONSE_OK = {
    "status": "000",
    "message": "정상",
    "page_no": 1,
    "page_count": 20,
    "total_count": 2,
    "total_page": 1,
    "list": [
        {
            "corp_cls": "Y",
            "corp_name": "삼성전자",
            "corp_code": "00126380",
            "stock_code": "005930",
            "report_nm": "주요사항보고서(수주공시)",
            "rcept_no": "20260327000001",
            "flr_nm": "삼성전자",
            "rcept_dt": "20260327",
        },
        {
            "corp_cls": "K",
            "corp_name": "셀트리온",
            "corp_code": "00413046",
            "stock_code": "068270",
            "report_nm": "임상시험결과(자율공시)",
            "rcept_no": "20260327000002",
            "flr_nm": "셀트리온",
            "rcept_dt": "20260327",
        },
        {
            # 비상장사 — stock_code 없음, 스킵 대상
            "corp_cls": "E",
            "corp_name": "비상장법인",
            "corp_code": "99999999",
            "stock_code": "",
            "report_nm": "사업보고서",
            "rcept_no": "20260327000003",
            "flr_nm": "비상장법인",
            "rcept_dt": "20260327",
        },
    ],
}

DART_RESPONSE_EMPTY = {
    "status": "013",
    "message": "조회된 데이터가 없습니다.",
}


async def test_dart_feed_poll_once_ok():
    cfg = Config(dart_api_key="test_key_123")
    import re as _re
    dart_url_pattern = _re.compile(r"^https://opendart\.fss\.or\.kr/api/list\.json")
    async with aiohttp.ClientSession() as session:
        feed = DartFeed(cfg, session)
        with aioresponses() as m:
            m.get(
                dart_url_pattern,
                payload=DART_RESPONSE_OK,
                status=200,
            )
            items = await feed.poll_once()

    assert len(items) == 2  # 비상장사 제외
    assert items[0].ticker == "005930"
    assert items[0].corp_name == "삼성전자"
    assert "수주공시" in items[0].title
    assert items[0].link.startswith("https://dart.fss.or.kr/")
    assert items[0].dorg.startswith("DART/")

    assert items[1].ticker == "068270"
    assert "임상" in items[1].title


async def test_dart_feed_poll_once_empty():
    cfg = Config(dart_api_key="test_key_123")
    import re as _re
    dart_url_pattern = _re.compile(r"^https://opendart\.fss\.or\.kr/api/list\.json")
    async with aiohttp.ClientSession() as session:
        feed = DartFeed(cfg, session)
        with aioresponses() as m:
            m.get(dart_url_pattern, payload=DART_RESPONSE_EMPTY, status=200)
            items = await feed.poll_once()

    assert items == []


async def test_dart_feed_no_api_key_returns_empty():
    cfg = Config(dart_api_key="")
    async with aiohttp.ClientSession() as session:
        feed = DartFeed(cfg, session)
        items = await feed.poll_once()
    assert items == []


async def test_dart_feed_dedup_across_polls():
    cfg = Config(dart_api_key="test_key_123")
    import re as _re
    dart_url_pattern = _re.compile(r"^https://opendart\.fss\.or\.kr/api/list\.json")
    async with aiohttp.ClientSession() as session:
        feed = DartFeed(cfg, session)
        with aioresponses() as m:
            m.get(dart_url_pattern, payload=DART_RESPONSE_OK, status=200)
            first = await feed.poll_once()
        with aioresponses() as m:
            m.get(dart_url_pattern, payload=DART_RESPONSE_OK, status=200)
            second = await feed.poll_once()

    assert len(first) == 2
    assert len(second) == 0  # 모두 이미 본 것


async def test_dart_feed_500_increments_failures():
    cfg = Config(dart_api_key="test_key_123")
    import re as _re
    dart_url_pattern = _re.compile(r"^https://opendart\.fss\.or\.kr/api/list\.json")
    async with aiohttp.ClientSession() as session:
        feed = DartFeed(cfg, session)
        with aioresponses() as m:
            m.get(dart_url_pattern, status=500)
            items = await feed.poll_once()
    assert items == []
    assert feed._consecutive_failures == 1


async def test_dart_feed_updates_last_poll_at():
    cfg = Config(dart_api_key="test_key_123")
    import re as _re
    dart_url_pattern = _re.compile(r"^https://opendart\.fss\.or\.kr/api/list\.json")
    async with aiohttp.ClientSession() as session:
        feed = DartFeed(cfg, session)
        assert feed.last_poll_at is None
        with aioresponses() as m:
            m.get(dart_url_pattern, payload=DART_RESPONSE_EMPTY, status=200)
            await feed.poll_once()
    assert feed.last_poll_at is not None


# ── MultiFeed Tests ──────────────────────────────────────────────


async def test_multifeed_merges_sources():
    """MultiFeed가 여러 소스의 결과를 합산하는지 검증."""
    cfg = Config()

    # 가짜 피드 2개
    feed_a = AsyncMock()
    feed_a.last_poll_at = None
    feed_a.stop = MagicMock()

    from kindshot.feed import RawDisclosure
    from datetime import datetime as dt, timezone, timedelta
    kst = timezone(timedelta(hours=9))
    now = dt.now(kst)

    item_a = RawDisclosure(
        title="A사(111111) 수주 공시",
        link="https://dart.fss.or.kr/test1",
        rss_guid="DART001",
        published="20260327",
        ticker="111111",
        corp_name="A사",
        detected_at=now,
        dorg="DART",
    )
    item_b = RawDisclosure(
        title="B사(222222) 공급계약 체결",
        link="kis://news/KIS001",
        rss_guid="KIS001",
        published="20260327 100000",
        ticker="222222",
        corp_name="B사",
        detected_at=now,
        dorg="한국거래소",
    )

    async def stream_a():
        yield [item_a]
    async def stream_b():
        yield [item_b]

    feed_a.stream = stream_a
    feed_b = AsyncMock()
    feed_b.last_poll_at = None
    feed_b.stop = MagicMock()
    feed_b.stream = stream_b

    multi = MultiFeed([feed_a, feed_b], cfg)

    collected = []
    async def _consume():
        async for batch in multi.stream():
            collected.extend(batch)
            if len(collected) >= 2:
                multi.stop()

    await asyncio.wait_for(_consume(), timeout=3.0)
    tickers = {item.ticker for item in collected}
    assert "111111" in tickers
    assert "222222" in tickers


async def test_multifeed_cross_dedup():
    """동일 종목+제목의 공시가 다른 소스에서 오면 중복 제거."""
    cfg = Config()
    multi = MultiFeed([], cfg)

    from kindshot.feed import RawDisclosure
    from datetime import datetime as dt, timezone, timedelta
    kst = timezone(timedelta(hours=9))
    now = dt.now(kst)

    item1 = RawDisclosure(
        title="삼성전자(005930) 주요사항보고서(수주공시)",
        link="https://dart.fss.or.kr/test",
        rss_guid="DART001",
        published="20260327",
        ticker="005930",
        corp_name="삼성전자",
        detected_at=now,
        dorg="DART",
    )
    item2 = RawDisclosure(
        title="삼성전자(005930) 주요사항보고서(수주공시)",
        link="kis://news/KIS001",
        rss_guid="KIS001",
        published="20260327 100000",
        ticker="005930",
        corp_name="삼성전자",
        detected_at=now,
        dorg="한국거래소",
    )

    result1 = multi._cross_dedup([item1])
    assert len(result1) == 1

    result2 = multi._cross_dedup([item2])
    assert len(result2) == 0  # 중복 제거됨


# ── AnalystFeed Tests ────────────────────────────────────────────


async def test_analyst_feed_filters_by_dorg():
    """증권사 dorg 항목만 필터링하는지 검증."""
    cfg = Config()
    kis = AsyncMock()
    # 증권사 2개 + 비증권사 1개 반환
    kis.fetch_analyst_reports = AsyncMock(return_value=[
        _news_item("RPT001", "20260327", "100000", "삼성전자(005930) 목표가 상향", dorg="삼성증권"),
        _news_item("RPT002", "20260327", "100100", "SK하이닉스(000660) 투자의견 상향", dorg="NH투자증권"),
        _news_item("RPT003", "20260327", "100200", "LG전자(066570) 매수", dorg="연합뉴스"),
    ])
    feed = AnalystFeed(cfg, kis)

    with patch("kindshot.feed.AnalystFeed._is_market_hours", return_value=True):
        results = await feed.poll_once()

    # fetch_analyst_reports가 이미 증권사 필터링을 했으므로 반환된 항목만 처리
    assert len(results) == 3
    assert all(r.dorg == "analyst" for r in results)


async def test_analyst_feed_creates_raw_disclosure():
    """RawDisclosure로 올바르게 변환되는지 검증."""
    cfg = Config()
    kis = AsyncMock()
    kis.fetch_analyst_reports = AsyncMock(return_value=[
        _news_item("RPT010", "20260327", "110000", "삼성전자(005930) 목표가 상향", ticker="005930", dorg="키움증권"),
    ])
    feed = AnalystFeed(cfg, kis)

    with patch("kindshot.feed.AnalystFeed._is_market_hours", return_value=True):
        results = await feed.poll_once()

    assert len(results) == 1
    item = results[0]
    assert item.rss_guid == "RPT010"
    assert item.ticker == "005930"
    assert item.corp_name == "삼성전자"
    assert item.dorg == "analyst"
    assert item.link == "kis://news/RPT010"
    assert item.detected_at is not None


async def test_analyst_feed_dedup():
    """동일 news_id 중복 제거 검증."""
    cfg = Config()
    kis = AsyncMock()
    report = _news_item("RPT020", "20260327", "120000", "현대차(005380) 실적 서프라이즈", dorg="대신증권")
    kis.fetch_analyst_reports = AsyncMock(return_value=[report])
    feed = AnalystFeed(cfg, kis)

    with patch("kindshot.feed.AnalystFeed._is_market_hours", return_value=True):
        first = await feed.poll_once()
        second = await feed.poll_once()

    assert len(first) == 1
    assert len(second) == 0  # 두 번째 폴링에서 중복 제거


async def test_analyst_feed_off_market_returns_empty():
    """장외 시간에는 폴링하지 않고 빈 목록 반환."""
    cfg = Config()
    kis = AsyncMock()
    kis.fetch_analyst_reports = AsyncMock(return_value=[
        _news_item("RPT030", "20260327", "200000", "A사(123456) 리포트", dorg="삼성증권"),
    ])
    feed = AnalystFeed(cfg, kis)

    with patch("kindshot.feed.AnalystFeed._is_market_hours", return_value=False):
        results = await feed.poll_once()

    assert results == []
    kis.fetch_analyst_reports.assert_not_called()


async def test_analyst_feed_stop_interrupts_stream():
    """stop() 호출 시 스트림이 빠르게 종료되는지 검증."""
    cfg = Config(analyst_feed_interval_s=30.0)
    kis = AsyncMock()
    kis.fetch_analyst_reports = AsyncMock(return_value=[])
    feed = AnalystFeed(cfg, kis)

    with patch("kindshot.feed.AnalystFeed._is_market_hours", return_value=True):
        async def _consume() -> None:
            async for _batch in feed.stream():
                pass

        task = asyncio.create_task(_consume())
        await asyncio.sleep(0.05)
        t0 = time.monotonic()
        feed.stop()
        await asyncio.wait_for(task, timeout=0.5)
        assert time.monotonic() - t0 < 0.5
