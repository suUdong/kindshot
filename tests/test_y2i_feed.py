"""Tests for Y2iFeed: y2i signal artifacts -> RawDisclosure."""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from kindshot.config import Config
from kindshot.feed import Y2iFeed, _Y2I_VERDICT_RANK


def _make_legacy_signal(
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


def _make_kindshot_signal(
    ticker: str = "005930.KS",
    company_name: str = "Samsung Electronics Co., Ltd.",
    channel: str = "itgod",
    signal_date: str | None = None,
    confidence: float = 0.62,
    verdict: str = "BUY",
    *,
    consensus_signal: bool = False,
    channel_weight: float = 1.0,
) -> dict:
    if signal_date is None:
        signal_date = datetime.now().strftime("%Y-%m-%d")
    return {
        "ticker": ticker,
        "company_name": company_name,
        "signal_source": "y2i",
        "signal_date": signal_date,
        "confidence": confidence,
        "verdict": verdict,
        "channel": channel,
        "channel_weight": channel_weight,
        "consensus_signal": consensus_signal,
        "consensus_strength": "MODERATE" if consensus_signal else None,
        "consensus_channel_count": 2 if consensus_signal else 0,
        "evidence": ["점수 62.0 | BUY | itgod"],
    }


def _write_tracker(path: Path, signals: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"signals": signals, "updated_at": "2026-03-28T00:00:00Z"}))


@pytest.fixture
def y2i_config(tmp_path: Path) -> Config:
    signal_path = tmp_path / "kindshot_feed.json"
    return Config(
        y2i_feed_enabled=True,
        y2i_signal_path=str(signal_path),
        y2i_min_score=55.0,
        y2i_min_verdict="WATCH",
        y2i_poll_interval_s=1.0,
        y2i_lookback_days=3,
    )


class TestY2iFeedPollOnce:
    def test_basic_kindshot_contract_signal(self, y2i_config: Config) -> None:
        """Dedicated kindshot contract is accepted by default."""
        signal_path = Path(y2i_config.y2i_signal_path)
        _write_tracker(signal_path, [_make_kindshot_signal(confidence=0.64)])

        feed = Y2iFeed(y2i_config)
        results = feed.poll_once()

        assert len(results) == 1
        rd = results[0]
        assert rd.ticker == "005930"
        assert rd.corp_name == "Samsung Electronics Co., Ltd."
        assert rd.dorg == "y2i"
        assert "[Y2I:itgod]" in rd.title
        assert rd.link.startswith("y2i://signal/")

    def test_legacy_tracker_contract_still_supported(self, y2i_config: Config) -> None:
        signal_path = Path(y2i_config.y2i_signal_path)
        _write_tracker(signal_path, [_make_legacy_signal(signal_score=60.0)])

        feed = Y2iFeed(y2i_config)
        results = feed.poll_once()

        assert len(results) == 1
        assert results[0].ticker == "005930"

    def test_filters_non_krx(self, y2i_config: Config) -> None:
        """미국 종목(AVGO)은 필터링."""
        signal_path = Path(y2i_config.y2i_signal_path)
        _write_tracker(signal_path, [_make_kindshot_signal(ticker="AVGO")])

        feed = Y2iFeed(y2i_config)
        results = feed.poll_once()
        assert len(results) == 0

    def test_filters_low_score(self, y2i_config: Config) -> None:
        """min_score 미만은 필터링."""
        signal_path = Path(y2i_config.y2i_signal_path)
        _write_tracker(signal_path, [_make_kindshot_signal(confidence=0.40)])

        feed = Y2iFeed(y2i_config)
        results = feed.poll_once()
        assert len(results) == 0

    def test_filters_low_verdict(self, y2i_config: Config) -> None:
        """REJECT verdict는 필터링 (min_verdict=WATCH)."""
        signal_path = Path(y2i_config.y2i_signal_path)
        _write_tracker(signal_path, [_make_kindshot_signal(verdict="REJECT", confidence=0.80)])

        feed = Y2iFeed(y2i_config)
        results = feed.poll_once()
        assert len(results) == 0

    def test_dedup_same_ticker_date(self, y2i_config: Config) -> None:
        """동일 (ticker, signal_date)는 strongest candidate 1건만 유지."""
        signal_path = Path(y2i_config.y2i_signal_path)
        today = datetime.now().strftime("%Y-%m-%d")
        _write_tracker(signal_path, [
            _make_kindshot_signal(signal_date=today, channel="ch1", confidence=0.61),
            _make_kindshot_signal(signal_date=today, channel="ch2", confidence=0.62, consensus_signal=True),
        ])

        feed = Y2iFeed(y2i_config)
        r1 = feed.poll_once()
        assert len(r1) == 1  # 같은 ticker+date → 1건만
        assert "[Y2I:ch2]" in r1[0].title

        # 두 번째 poll: 이미 본 시그널 → 0건
        r2 = feed.poll_once()
        assert len(r2) == 0

    def test_lookback_filter(self, y2i_config: Config) -> None:
        """lookback_days 밖의 시그널은 무시."""
        signal_path = Path(y2i_config.y2i_signal_path)
        old_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        _write_tracker(signal_path, [_make_kindshot_signal(signal_date=old_date)])

        feed = Y2iFeed(y2i_config)
        results = feed.poll_once()
        assert len(results) == 0

    def test_kosdaq_ticker(self, y2i_config: Config) -> None:
        """코스닥(.KQ) 종목도 정상 처리."""
        signal_path = Path(y2i_config.y2i_signal_path)
        _write_tracker(signal_path, [_make_kindshot_signal(ticker="240810.KQ", company_name="Wonik IPS")])

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
            _make_kindshot_signal(ticker="005930.KS", confidence=0.60),
            _make_kindshot_signal(ticker="240810.KQ", company_name="Wonik IPS", confidence=0.58),
        ])

        feed = Y2iFeed(y2i_config)
        results = feed.poll_once()
        assert len(results) == 2
        tickers = {r.ticker for r in results}
        assert tickers == {"005930", "240810"}

    def test_prefers_consensus_and_weight_when_same_day_duplicates_exist(self, y2i_config: Config) -> None:
        signal_path = Path(y2i_config.y2i_signal_path)
        today = datetime.now().strftime("%Y-%m-%d")
        _write_tracker(signal_path, [
            _make_kindshot_signal(signal_date=today, channel="base", confidence=0.67, channel_weight=1.0),
            _make_kindshot_signal(signal_date=today, channel="consensus", confidence=0.65, consensus_signal=True, channel_weight=1.2),
        ])

        feed = Y2iFeed(y2i_config)
        results = feed.poll_once()

        assert len(results) == 1
        assert "[Y2I:consensus]" in results[0].title


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
