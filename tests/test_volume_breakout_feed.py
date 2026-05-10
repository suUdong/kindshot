"""Tests for v86 VolumeBreakoutFeed."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from kindshot.config import Config
from kindshot.feeds.volume_breakout_feed import VolumeBreakoutFeed
from kindshot.strategy import SignalSource, Strategy


def _qualifying_features() -> dict[str, Any]:
    return {
        "close_today": 12000.0,
        "prior_high_n": 11500.0,
        "vol_today": 3_000_000.0,
        "prior_avg_vol_n": 1_000_000.0,
        "adv_value_n": 5_000_000_000.0,
        "ret_today": 2.5,
    }


def _non_qualifying_features_no_breakout() -> dict[str, Any]:
    feats = _qualifying_features()
    feats["close_today"] = 11000.0
    return feats


def _non_qualifying_features_no_volume() -> dict[str, Any]:
    feats = _qualifying_features()
    feats["vol_today"] = 1_100_000.0
    return feats


def _make_config(
    *,
    enabled: bool = True,
    tickers: tuple[str, ...] = ("005930",),
    cooldown_s: float = 0.0,
    poll_s: float = 0.01,
) -> Config:
    return Config(
        volume_breakout_feed_enabled=enabled,
        volume_breakout_feed_tickers=tickers,
        volume_breakout_feed_signal_cooldown_s=cooldown_s,
        volume_breakout_feed_poll_interval_s=poll_s,
    )


def _async_return(value):
    async def _coro(*_a, **_kw):
        return value
    return _coro


@pytest.mark.asyncio
async def test_volume_breakout_feed_is_protocol_compliant():
    strategy = VolumeBreakoutFeed(
        _make_config(),
        hist_fetcher=_async_return(_qualifying_features()),
    )
    assert isinstance(strategy, Strategy)
    assert strategy.name == "volume_breakout"
    assert strategy.source == SignalSource.TECHNICAL
    assert strategy.enabled is True


@pytest.mark.asyncio
async def test_disabled_when_tickers_empty():
    strategy = VolumeBreakoutFeed(
        _make_config(tickers=()),
        hist_fetcher=_async_return(_qualifying_features()),
    )
    assert strategy.enabled is False
    assert await strategy.scan_once() == []


@pytest.mark.asyncio
async def test_emits_signal_for_qualifying_snapshot():
    strategy = VolumeBreakoutFeed(
        _make_config(),
        hist_fetcher=_async_return(_qualifying_features()),
    )
    signals = await strategy.scan_once()
    assert len(signals) == 1
    sig = signals[0]
    assert sig.ticker == "005930"
    assert sig.source == SignalSource.TECHNICAL
    assert sig.confidence >= 70
    assert sig.strategy_name == "volume_breakout"
    assert "breakout" in sig.reason.lower()
    assert sig.metadata["vol_ratio"] >= 2.0


@pytest.mark.asyncio
async def test_skips_when_no_breakout():
    strategy = VolumeBreakoutFeed(
        _make_config(),
        hist_fetcher=_async_return(_non_qualifying_features_no_breakout()),
    )
    assert await strategy.scan_once() == []


@pytest.mark.asyncio
async def test_skips_when_volume_below_threshold():
    strategy = VolumeBreakoutFeed(
        _make_config(),
        hist_fetcher=_async_return(_non_qualifying_features_no_volume()),
    )
    assert await strategy.scan_once() == []


@pytest.mark.asyncio
async def test_skips_when_ret_today_exceeds_chase_cap():
    feats = _qualifying_features()
    feats["ret_today"] = 12.0
    strategy = VolumeBreakoutFeed(
        _make_config(),
        hist_fetcher=_async_return(feats),
    )
    assert await strategy.scan_once() == []


@pytest.mark.asyncio
async def test_skips_when_adv_below_min():
    feats = _qualifying_features()
    feats["adv_value_n"] = 100_000_000.0  # 0.1B << 0.5B default
    strategy = VolumeBreakoutFeed(
        _make_config(),
        hist_fetcher=_async_return(feats),
    )
    assert await strategy.scan_once() == []


@pytest.mark.asyncio
async def test_cooldown_suppresses_duplicate_emission():
    counter = {"calls": 0}

    async def fetcher(_ticker, _lookback_n):
        counter["calls"] += 1
        return _qualifying_features()

    times = iter([100.0, 100.5, 100.6])

    strategy = VolumeBreakoutFeed(
        _make_config(cooldown_s=60.0),
        hist_fetcher=fetcher,
        monotonic_fn=lambda: next(times),
    )

    first = await strategy.scan_once()
    assert len(first) == 1
    second = await strategy.scan_once()
    assert second == []


@pytest.mark.asyncio
async def test_returns_empty_when_hist_fetcher_returns_empty():
    strategy = VolumeBreakoutFeed(
        _make_config(),
        hist_fetcher=_async_return({}),
    )
    assert await strategy.scan_once() == []


@pytest.mark.asyncio
async def test_stream_signals_terminates_on_stop_event():
    stop_event = asyncio.Event()

    async def sleep_and_stop(_seconds):
        stop_event.set()

    strategy = VolumeBreakoutFeed(
        _make_config(),
        stop_event=stop_event,
        hist_fetcher=_async_return(_qualifying_features()),
        sleep_fn=sleep_and_stop,
    )

    signals = [sig async for sig in strategy.stream_signals()]
    assert len(signals) >= 1
    assert all(s.ticker == "005930" for s in signals)


@pytest.mark.asyncio
async def test_disabled_stream_yields_nothing():
    strategy = VolumeBreakoutFeed(
        _make_config(enabled=False),
        hist_fetcher=_async_return(_qualifying_features()),
    )
    signals = [sig async for sig in strategy.stream_signals()]
    assert signals == []


@pytest.mark.asyncio
async def test_confidence_scales_with_volume_ratio():
    high_vol = _qualifying_features()
    high_vol["vol_today"] = 5_000_000.0  # 5x avg

    strategy = VolumeBreakoutFeed(
        _make_config(),
        hist_fetcher=_async_return(high_vol),
    )
    signals = await strategy.scan_once()
    assert len(signals) == 1
    assert signals[0].confidence > 75
