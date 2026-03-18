"""Tests for quant 3-second check."""

from datetime import datetime, timedelta, timezone

import pytest

from kindshot.quant import quant_check, QuantResult
from kindshot.config import Config


def _cfg(**kw) -> Config:
    return Config(**kw)


def test_all_pass():
    r = quant_check(
        adv_value_20d=10_000_000_000,
        spread_bps=10.0,
        ret_today=5.0,
        config=_cfg(),
    )
    assert r.passed is True
    assert r.detail.adv_value_20d_ok is True
    assert r.detail.spread_bps_ok is True
    assert r.detail.extreme_move_ok is True


def test_adv_too_low():
    r = quant_check(
        adv_value_20d=300_000_000,
        spread_bps=10.0,
        ret_today=5.0,
        config=_cfg(),
    )
    assert r.passed is False
    assert r.skip_reason == "ADV_TOO_LOW"


def test_spread_too_wide():
    r = quant_check(
        adv_value_20d=10_000_000_000,
        spread_bps=55.0,
        ret_today=5.0,
        config=_cfg(spread_check_enabled=True),
    )
    assert r.passed is False
    assert r.skip_reason == "SPREAD_TOO_WIDE"


def test_spread_check_disabled():
    """When spread check is disabled, wide spread should pass."""
    r = quant_check(
        adv_value_20d=10_000_000_000,
        spread_bps=30.0,
        ret_today=5.0,
        config=_cfg(spread_check_enabled=False),
    )
    assert r.detail.spread_bps_ok is True


def test_spread_none_fail_open_default():
    """Default spread_missing_policy='pass' means None spread passes."""
    r = quant_check(
        adv_value_20d=10_000_000_000,
        spread_bps=None,
        ret_today=5.0,
        config=_cfg(spread_check_enabled=True),
        observed_at=datetime(2026, 3, 13, 9, 5, tzinfo=timezone(timedelta(hours=9))),
    )
    assert r.passed is True
    assert r.detail.spread_bps_ok is True


def test_spread_none_fail_close_when_policy_fail():
    """When spread_missing_policy='fail', None spread blocks."""
    r = quant_check(
        adv_value_20d=10_000_000_000,
        spread_bps=None,
        ret_today=5.0,
        config=_cfg(spread_check_enabled=True, spread_missing_policy="fail"),
        observed_at=datetime(2026, 3, 13, 9, 5, tzinfo=timezone(timedelta(hours=9))),
    )
    assert r.passed is False
    assert r.detail.spread_bps_ok is False
    assert r.skip_reason == "SPREAD_DATA_MISSING"


def test_spread_none_off_hours_fail_close():
    """Off-hours spread None with policy='fail' uses distinct reason."""
    r = quant_check(
        adv_value_20d=10_000_000_000,
        spread_bps=None,
        ret_today=5.0,
        config=_cfg(spread_check_enabled=True, spread_missing_policy="fail"),
        observed_at=datetime(2026, 3, 13, 8, 40, tzinfo=timezone(timedelta(hours=9))),
    )
    assert r.passed is False
    assert r.detail.spread_bps_ok is False
    assert r.skip_reason == "SPREAD_DATA_MISSING_OFF_HOURS"


def test_extreme_move():
    r = quant_check(
        adv_value_20d=10_000_000_000,
        spread_bps=10.0,
        ret_today=25.0,
        config=_cfg(),
    )
    assert r.passed is False
    assert r.skip_reason == "EXTREME_MOVE"


def test_extreme_move_negative():
    r = quant_check(
        adv_value_20d=10_000_000_000,
        spread_bps=10.0,
        ret_today=-22.0,
        config=_cfg(),
    )
    assert r.passed is False


def test_ret_today_none_fail_close():
    r = quant_check(
        adv_value_20d=10_000_000_000,
        spread_bps=10.0,
        ret_today=None,
        config=_cfg(),
    )
    assert r.passed is False
    assert r.detail.extreme_move_ok is False
    assert r.skip_reason == "RET_TODAY_DATA_MISSING"


def test_should_track_price_sampling(monkeypatch):
    """10% sampling of quant fails for price tracking."""
    import kindshot.quant as qmod
    monkeypatch.setattr(qmod.random, "random", lambda: 0.05)  # < 0.10

    r = quant_check(
        adv_value_20d=300_000_000,
        spread_bps=10.0,
        ret_today=5.0,
        config=_cfg(),
    )
    assert r.passed is False
    assert r.should_track_price is True


def test_no_tracking_when_not_sampled(monkeypatch):
    import kindshot.quant as qmod
    monkeypatch.setattr(qmod.random, "random", lambda: 0.50)  # > 0.10

    r = quant_check(
        adv_value_20d=300_000_000,
        spread_bps=10.0,
        ret_today=5.0,
        config=_cfg(),
    )
    assert r.should_track_price is False
