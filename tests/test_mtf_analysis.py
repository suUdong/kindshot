"""멀티 타임프레임 분석 테스트."""
import pytest
from kindshot.mtf_analysis import _determine_trend, _calc_alignment_score, MtfResult


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


def test_mtf_confidence_adjustment():
    from kindshot.guardrails import apply_mtf_confidence_adjustment
    assert apply_mtf_confidence_adjustment(80, 100) == 83  # +3
    assert apply_mtf_confidence_adjustment(80, 70) == 83   # +3
    assert apply_mtf_confidence_adjustment(80, 30) == 77   # -3
    assert apply_mtf_confidence_adjustment(80, 50) == 80   # 변화없음
