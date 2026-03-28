"""Tests for Y2iFeed: signal_tracker.json → RawDisclosure 변환."""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from kindshot.config import Config
from kindshot.feed import Y2iFeed, _Y2I_VERDICT_RANK


def _make_signal(
    ticker: str = "005930.KS",
    company_name: str = "Samsung Electronics Co., Ltd.",
    channel_slug: str = "itgod",
    signal_date: str | None = None,
    signal_score: float = 60.0,
    verdict: str = "WATCH",
) -> dict:
    if signal_date is None:
        signal_date = datetime.now().strftime("%Y-%m-%d")
    return {
        "ticker": ticker,
        "company_name": company_name,
        "channel_slug": channel_slug,
        "signal_date": signal_date,
        "signal_score": signal_score,
        "verdict": verdict,
        "source_video_id": None,
        "source_title": None,
        "entry_price": 60000.0,
        "returns": {"1d": 0.5, "3d": 1.2},
    }


def _write_tracker(path: Path, signals: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"signals": signals, "updated_at": "2026-03-28T00:00:00Z"}))


@pytest.fixture
def y2i_config(tmp_path: Path) -> Config:
    signal_path = tmp_path / "signal_tracker.json"
    return Config(
        y2i_feed_enabled=True,
        y2i_signal_path=str(signal_path),
        y2i_min_score=55.0,
        y2i_min_verdict="WATCH",
        y2i_poll_interval_s=1.0,
        y2i_lookback_days=3,
    )


class TestY2iFeedPollOnce:
    def test_basic_krx_signal(self, y2i_config: Config, tmp_path: Path) -> None:
        """KRX 종목이 RawDisclosure로 정상 변환되는지 확인."""
        signal_path = Path(y2i_config.y2i_signal_path)
        _write_tracker(signal_path, [_make_signal(signal_score=60.0)])

        feed = Y2iFeed(y2i_config)
        results = feed.poll_once()

        assert len(results) == 1
        rd = results[0]
        assert rd.ticker == "005930"
        assert rd.corp_name == "Samsung Electronics Co., Ltd."
        assert rd.dorg == "y2i"
        assert "[Y2I:itgod]" in rd.title
        assert rd.link.startswith("y2i://signal/")

    def test_filters_non_krx(self, y2i_config: Config) -> None:
        """미국 종목(AVGO)은 필터링."""
        signal_path = Path(y2i_config.y2i_signal_path)
        _write_tracker(signal_path, [_make_signal(ticker="AVGO")])

        feed = Y2iFeed(y2i_config)
        results = feed.poll_once()
        assert len(results) == 0

    def test_filters_low_score(self, y2i_config: Config) -> None:
        """min_score 미만은 필터링."""
        signal_path = Path(y2i_config.y2i_signal_path)
        _write_tracker(signal_path, [_make_signal(signal_score=40.0)])

        feed = Y2iFeed(y2i_config)
        results = feed.poll_once()
        assert len(results) == 0

    def test_filters_low_verdict(self, y2i_config: Config) -> None:
        """REJECT verdict는 필터링 (min_verdict=WATCH)."""
        signal_path = Path(y2i_config.y2i_signal_path)
        _write_tracker(signal_path, [_make_signal(verdict="REJECT", signal_score=70.0)])

        feed = Y2iFeed(y2i_config)
        results = feed.poll_once()
        assert len(results) == 0

    def test_dedup_same_ticker_date(self, y2i_config: Config) -> None:
        """동일 (ticker, signal_date) 시그널은 중복 제거."""
        signal_path = Path(y2i_config.y2i_signal_path)
        today = datetime.now().strftime("%Y-%m-%d")
        _write_tracker(signal_path, [
            _make_signal(signal_date=today, channel_slug="ch1"),
            _make_signal(signal_date=today, channel_slug="ch2"),
        ])

        feed = Y2iFeed(y2i_config)
        r1 = feed.poll_once()
        assert len(r1) == 1  # 같은 ticker+date → 1건만

        # 두 번째 poll: 이미 본 시그널 → 0건
        r2 = feed.poll_once()
        assert len(r2) == 0

    def test_lookback_filter(self, y2i_config: Config) -> None:
        """lookback_days 밖의 시그널은 무시."""
        signal_path = Path(y2i_config.y2i_signal_path)
        old_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        _write_tracker(signal_path, [_make_signal(signal_date=old_date)])

        feed = Y2iFeed(y2i_config)
        results = feed.poll_once()
        assert len(results) == 0

    def test_kosdaq_ticker(self, y2i_config: Config) -> None:
        """코스닥(.KQ) 종목도 정상 처리."""
        signal_path = Path(y2i_config.y2i_signal_path)
        _write_tracker(signal_path, [_make_signal(ticker="240810.KQ", company_name="Wonik IPS")])

        feed = Y2iFeed(y2i_config)
        results = feed.poll_once()
        assert len(results) == 1
        assert results[0].ticker == "240810"

    def test_missing_file_returns_empty(self, y2i_config: Config) -> None:
        """시그널 파일이 없으면 빈 목록."""
        feed = Y2iFeed(y2i_config)
        results = feed.poll_once()
        assert len(results) == 0

    def test_malformed_json_returns_empty(self, y2i_config: Config) -> None:
        """잘못된 JSON이면 빈 목록."""
        signal_path = Path(y2i_config.y2i_signal_path)
        signal_path.parent.mkdir(parents=True, exist_ok=True)
        signal_path.write_text("not json{{{")

        feed = Y2iFeed(y2i_config)
        results = feed.poll_once()
        assert len(results) == 0

    def test_multiple_tickers(self, y2i_config: Config) -> None:
        """여러 종목이 모두 변환."""
        signal_path = Path(y2i_config.y2i_signal_path)
        _write_tracker(signal_path, [
            _make_signal(ticker="005930.KS", signal_score=60.0),
            _make_signal(ticker="240810.KQ", company_name="Wonik IPS", signal_score=58.0),
        ])

        feed = Y2iFeed(y2i_config)
        results = feed.poll_once()
        assert len(results) == 2
        tickers = {r.ticker for r in results}
        assert tickers == {"005930", "240810"}


class TestY2iVerdictRank:
    def test_verdict_ordering(self) -> None:
        assert _Y2I_VERDICT_RANK["REJECT"] < _Y2I_VERDICT_RANK["WATCH"]
        assert _Y2I_VERDICT_RANK["WATCH"] < _Y2I_VERDICT_RANK["BUY"]
        assert _Y2I_VERDICT_RANK["BUY"] < _Y2I_VERDICT_RANK["STRONG_BUY"]


class TestY2iExtractKrxTicker:
    def test_kospi(self) -> None:
        assert Y2iFeed._extract_krx_ticker("005930.KS") == "005930"

    def test_kosdaq(self) -> None:
        assert Y2iFeed._extract_krx_ticker("240810.KQ") == "240810"

    def test_us_stock(self) -> None:
        assert Y2iFeed._extract_krx_ticker("AVGO") is None

    def test_empty(self) -> None:
        assert Y2iFeed._extract_krx_ticker("") is None

    def test_invalid_format(self) -> None:
        assert Y2iFeed._extract_krx_ticker("12345.KS") is None  # 5자리
