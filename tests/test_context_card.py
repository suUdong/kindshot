"""Tests for context_card pykrx cache behavior."""

import time

import kindshot.context_card as cc


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
