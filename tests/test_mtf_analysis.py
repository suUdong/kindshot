"""멀티 타임프레임 분석 테스트."""
from unittest.mock import AsyncMock

import pytest
from kindshot.config import Config
from kindshot.mtf_analysis import _determine_trend, _calc_alignment_score, _mtf_cache, analyze_mtf, configure_mtf_cache, MtfResult


def test_determine_trend_up():
    candles = [{"close": 110}, {"close": 108}, {"close": 105}, {"close": 103}, {"close": 100}]
    assert _determine_trend(candles) == "UP"


def test_determine_trend_down():
    candles = [{"close": 90}, {"close": 92}, {"close": 95}, {"close": 97}, {"close": 100}]
    assert _determine_trend(candles) == "DOWN"


def test_determine_trend_sideways():
    candles = [{"close": 100}, {"close": 101}, {"close": 99}, {"close": 100}, {"close": 100}]
    assert _determine_trend(candles) == "SIDEWAYS"


def test_determine_trend_insufficient_data():
    candles = [{"close": 100}, {"close": 99}]
    assert _determine_trend(candles) == "SIDEWAYS"


def test_alignment_score_all_up():
    assert _calc_alignment_score("UP", "UP", "UP") == 100


def test_alignment_score_all_down():
    assert _calc_alignment_score("DOWN", "DOWN", "DOWN") == 0


def test_alignment_score_mixed():
    assert _calc_alignment_score("UP", "UP", "DOWN") == 75
    assert _calc_alignment_score("DOWN", "DOWN", "UP") == 25
    assert _calc_alignment_score("UP", "DOWN", "SIDEWAYS") == 50


@pytest.mark.asyncio
async def test_analyze_mtf_disabled():
    config = Config(mtf_enabled=False)
    result = await analyze_mtf("005930", AsyncMock(), config)
    assert result.alignment_score == 50
    assert "disabled" in result.detail


@pytest.mark.asyncio
async def test_analyze_mtf_caches_result():
    _mtf_cache.clear()
    config = Config(mtf_enabled=True)
    kis = AsyncMock()
    up_candles = [{"close": c} for c in [110, 108, 105, 103, 100, 98, 95, 93, 90, 88]]
    kis.fetch_minute_candles = AsyncMock(return_value=up_candles)

    r1 = await analyze_mtf("005930", kis, config)
    count_after_first = kis.fetch_minute_candles.call_count
    r2 = await analyze_mtf("005930", kis, config)

    assert r1 == r2
    assert kis.fetch_minute_candles.call_count == count_after_first
    _mtf_cache.clear()


@pytest.mark.asyncio
async def test_analyze_mtf_empty_candles_returns_sideways():
    _mtf_cache.clear()
    config = Config(mtf_enabled=True)
    kis = AsyncMock()
    kis.fetch_minute_candles = AsyncMock(return_value=[])
    result = await analyze_mtf("005930", kis, config)
    assert result.trend_5m == "SIDEWAYS"
    _mtf_cache.clear()


def test_configure_mtf_cache_minimum():
    configure_mtf_cache(0)
    from kindshot.mtf_analysis import _MTF_CACHE_TTL
    assert _MTF_CACHE_TTL == 1
    configure_mtf_cache(120)


def test_mtf_confidence_adjustment():
    from kindshot.guardrails import apply_mtf_confidence_adjustment
    assert apply_mtf_confidence_adjustment(80, 100) == 83  # +3
    assert apply_mtf_confidence_adjustment(80, 70) == 83   # +3
    assert apply_mtf_confidence_adjustment(80, 30) == 77   # -3
    assert apply_mtf_confidence_adjustment(80, 50) == 80   # 변화없음
