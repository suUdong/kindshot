"""Tests for context_card pykrx cache behavior and gap calculation."""

import time
from unittest.mock import AsyncMock, MagicMock

import kindshot.context_card as cc
from kindshot.config import Config
from kindshot.kis_client import PriceInfo


async def test_pykrx_features_cache_hit(monkeypatch):
    cc._pykrx_cache.clear()
    monkeypatch.setattr(cc, "_PYKRX_CACHE_TTL", 300)
    monkeypatch.setattr(cc, "_PYKRX_CACHE_MAX_SIZE", 512)

    calls = {"count": 0}

    async def _fake_to_thread(func, *args, **kwargs):
        calls["count"] += 1
        return {"adv_value_20d": 123, "prev_close": 100}

    monkeypatch.setattr(cc.asyncio, "to_thread", _fake_to_thread)

    first = await cc._pykrx_features("005930")
    second = await cc._pykrx_features("005930")

    assert first == second
    assert calls["count"] == 1
    assert len(cc._pykrx_cache) == 1


async def test_pykrx_cache_lru_eviction(monkeypatch):
    cc._pykrx_cache.clear()
    monkeypatch.setattr(cc, "_PYKRX_CACHE_TTL", 300)
    monkeypatch.setattr(cc, "_PYKRX_CACHE_MAX_SIZE", 2)

    async def _fake_to_thread(func, *args, **kwargs):
        return {"ok": True}

    monkeypatch.setattr(cc.asyncio, "to_thread", _fake_to_thread)

    await cc._pykrx_features("A")
    await cc._pykrx_features("B")
    await cc._pykrx_features("A")  # refresh A as most recently used
    await cc._pykrx_features("C")  # should evict B

    assert "A" in cc._pykrx_cache
    assert "C" in cc._pykrx_cache
    assert "B" not in cc._pykrx_cache
    assert len(cc._pykrx_cache) == 2


async def test_pykrx_cache_prunes_expired(monkeypatch):
    cc._pykrx_cache.clear()
    monkeypatch.setattr(cc, "_PYKRX_CACHE_TTL", 300)
    monkeypatch.setattr(cc, "_PYKRX_CACHE_MAX_SIZE", 512)

    cc._pykrx_cache["OLD"] = ({"v": 0}, time.monotonic() - 1)

    async def _fake_to_thread(func, *args, **kwargs):
        return {"v": 1}

    monkeypatch.setattr(cc.asyncio, "to_thread", _fake_to_thread)

    await cc._pykrx_features("NEW")

    assert "OLD" not in cc._pykrx_cache
    assert "NEW" in cc._pykrx_cache


async def test_gap_calculated_from_open_px(monkeypatch):
    """gap should be (open_px / prev_close - 1) * 100 when KIS provides open_px."""
    cc._pykrx_cache.clear()
    monkeypatch.setattr(cc, "_PYKRX_CACHE_TTL", 300)
    monkeypatch.setattr(cc, "_PYKRX_CACHE_MAX_SIZE", 512)

    async def _fake_to_thread(func, *args, **kwargs):
        return {"prev_close": 50000, "adv_value_20d": 10e9}

    monkeypatch.setattr(cc.asyncio, "to_thread", _fake_to_thread)

    mock_kis = AsyncMock()
    mock_kis.get_price = AsyncMock(return_value=PriceInfo(
        px=52000, open_px=51000, spread_bps=10.0, cum_value=1e9, fetch_latency_ms=50,
    ))

    card, raw = await cc.build_context_card("005930", kis=mock_kis)

    # gap = (51000 / 50000 - 1) * 100 = 2.0%
    assert card.gap == 2.0
    assert raw["gap"] == 2.0
    # ret_today = (52000 / 50000 - 1) * 100 = 4.0%
    assert card.ret_today == 4.0
    assert card.spread_bps == 10.0


async def test_gap_none_without_open_px(monkeypatch):
    """gap should be None when open_px is not available."""
    cc._pykrx_cache.clear()
    monkeypatch.setattr(cc, "_PYKRX_CACHE_TTL", 300)
    monkeypatch.setattr(cc, "_PYKRX_CACHE_MAX_SIZE", 512)

    async def _fake_to_thread(func, *args, **kwargs):
        return {"prev_close": 50000}

    monkeypatch.setattr(cc.asyncio, "to_thread", _fake_to_thread)

    mock_kis = AsyncMock()
    mock_kis.get_price = AsyncMock(return_value=PriceInfo(
        px=52000, open_px=None, spread_bps=None, cum_value=1e9, fetch_latency_ms=50,
    ))

    card, raw = await cc.build_context_card("005930", kis=mock_kis)

    assert card.gap is None
