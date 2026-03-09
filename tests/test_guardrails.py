"""Tests for guardrails."""

from kindshot.config import Config
from kindshot.guardrails import check_guardrails, GuardrailResult


def _cfg(**kw) -> Config:
    return Config(**kw)


def test_all_pass():
    """All data within limits → pass."""
    r = check_guardrails(
        ticker="005930",
        config=_cfg(spread_check_enabled=True),
        spread_bps=10.0,
        adv_value_20d=10e9,
        ret_today=5.0,
    )
    assert r.passed is True
    assert r.reason is None


def test_spread_too_wide():
    r = check_guardrails(
        ticker="005930",
        config=_cfg(spread_check_enabled=True),
        spread_bps=30.0,
        adv_value_20d=10e9,
        ret_today=5.0,
    )
    assert r.passed is False
    assert r.reason == "SPREAD_TOO_WIDE"


def test_spread_missing_fails_close():
    r = check_guardrails(
        ticker="005930",
        config=_cfg(spread_check_enabled=True),
        spread_bps=None,
        adv_value_20d=10e9,
        ret_today=5.0,
    )
    assert r.passed is False
    assert r.reason == "SPREAD_DATA_MISSING"


def test_spread_check_disabled_skips():
    r = check_guardrails(
        ticker="005930",
        config=_cfg(spread_check_enabled=False),
        spread_bps=None,
        adv_value_20d=10e9,
        ret_today=5.0,
    )
    assert r.passed is True


def test_adv_too_low():
    r = check_guardrails(
        ticker="005930",
        config=_cfg(),
        spread_bps=10.0,
        adv_value_20d=1e9,
        ret_today=5.0,
    )
    assert r.passed is False
    assert r.reason == "ADV_TOO_LOW"


def test_adv_missing():
    r = check_guardrails(
        ticker="005930",
        config=_cfg(),
        adv_value_20d=None,
        ret_today=5.0,
    )
    assert r.passed is False
    assert r.reason == "ADV_DATA_MISSING"


def test_extreme_move():
    r = check_guardrails(
        ticker="005930",
        config=_cfg(),
        adv_value_20d=10e9,
        ret_today=25.0,
    )
    assert r.passed is False
    assert r.reason == "EXTREME_MOVE"


def test_ret_today_missing():
    r = check_guardrails(
        ticker="005930",
        config=_cfg(),
        adv_value_20d=10e9,
        ret_today=None,
    )
    assert r.passed is False
    assert r.reason == "RET_TODAY_DATA_MISSING"
