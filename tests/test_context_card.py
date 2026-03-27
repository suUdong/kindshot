"""Tests for context_card pykrx cache behavior and gap calculation."""

import json
from datetime import datetime, timezone
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

import kindshot.context_card as cc
from kindshot.config import Config
from kindshot.context_card import ContextCardData
from kindshot.kis_client import OrderbookSnapshot, PriceInfo, QuoteRiskState
from kindshot.models import ContextCard, MarketContext


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
    assert raw.gap == 2.0
    # ret_today = (52000 / 50000 - 1) * 100 = 4.0%
    assert card.ret_today == 4.0
    assert card.spread_bps == 10.0
    assert card.intraday_value_vs_adv20d == 0.1


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


async def test_context_card_preserves_quote_risk_state(monkeypatch):
    cc._pykrx_cache.clear()
    monkeypatch.setattr(cc, "_PYKRX_CACHE_TTL", 300)
    monkeypatch.setattr(cc, "_PYKRX_CACHE_MAX_SIZE", 512)

    async def _fake_to_thread(func, *args, **kwargs):
        return {"prev_close": 50000}

    monkeypatch.setattr(cc.asyncio, "to_thread", _fake_to_thread)

    risk_state = QuoteRiskState(temp_stop_yn="Y", sltr_yn="N", vi_cls_code="D")
    mock_kis = AsyncMock()
    mock_kis.get_price = AsyncMock(return_value=PriceInfo(
        px=52000, open_px=51000, spread_bps=10.0, cum_value=1e9, fetch_latency_ms=50, risk_state=risk_state,
    ))

    _card, raw = await cc.build_context_card("005930", kis=mock_kis)

    assert raw.quote_risk_state == risk_state


async def test_context_card_preserves_orderbook_snapshot(monkeypatch):
    cc._pykrx_cache.clear()
    monkeypatch.setattr(cc, "_PYKRX_CACHE_TTL", 300)
    monkeypatch.setattr(cc, "_PYKRX_CACHE_MAX_SIZE", 512)

    async def _fake_to_thread(func, *args, **kwargs):
        return {"prev_close": 50000}

    monkeypatch.setattr(cc.asyncio, "to_thread", _fake_to_thread)

    orderbook = OrderbookSnapshot(
        ask_price1=52100.0,
        bid_price1=51900.0,
        ask_size1=80,
        bid_size1=120,
        total_ask_size=2000,
        total_bid_size=2500,
        spread_bps=38.5,
    )
    mock_kis = AsyncMock()
    mock_kis.get_price = AsyncMock(return_value=PriceInfo(
        px=52000, open_px=51000, spread_bps=38.5, cum_value=1e9, fetch_latency_ms=50, orderbook=orderbook,
    ))

    _card, raw = await cc.build_context_card("005930", kis=mock_kis)

    assert raw.orderbook_snapshot == orderbook


async def test_context_card_preserves_participation_fields(monkeypatch):
    cc._pykrx_cache.clear()
    monkeypatch.setattr(cc, "_PYKRX_CACHE_TTL", 300)
    monkeypatch.setattr(cc, "_PYKRX_CACHE_MAX_SIZE", 512)

    async def _fake_to_thread(func, *args, **kwargs):
        return {"prev_close": 50000, "adv_value_20d": 20_000_000_000}

    monkeypatch.setattr(cc.asyncio, "to_thread", _fake_to_thread)

    mock_kis = AsyncMock()
    mock_kis.get_price = AsyncMock(return_value=PriceInfo(
        px=52000,
        open_px=51000,
        spread_bps=12.0,
        cum_value=100_000_000.0,
        fetch_latency_ms=50,
        cum_volume=2_500_000.0,
        listed_shares=400_000_000.0,
        volume_turnover_rate=0.63,
        prior_volume_rate=180.2,
    ))

    card, raw = await cc.build_context_card("005930", kis=mock_kis)

    assert card.intraday_value_vs_adv20d == 0.005
    assert raw.cum_volume == 2_500_000.0
    assert raw.listed_shares == 400_000_000.0
    assert raw.volume_turnover_rate == 0.63
    assert raw.prior_volume_rate == 180.2
    assert raw.intraday_value_vs_adv20d == 0.005


async def test_context_card_surfaces_support_reference(monkeypatch):
    cc._pykrx_cache.clear()
    monkeypatch.setattr(cc, "_PYKRX_CACHE_TTL", 300)
    monkeypatch.setattr(cc, "_PYKRX_CACHE_MAX_SIZE", 512)

    async def _fake_to_thread(func, *args, **kwargs):
        return {
            "prev_close": 50000,
            "support_price_5d": 48200.0,
            "support_price_20d": 47100.0,
            "support_reference_px": 48200.0,
        }

    monkeypatch.setattr(cc.asyncio, "to_thread", _fake_to_thread)

    mock_kis = AsyncMock()
    mock_kis.get_price = AsyncMock(return_value=PriceInfo(
        px=52000, open_px=51000, spread_bps=12.0, cum_value=1e9, fetch_latency_ms=50,
    ))

    card, raw = await cc.build_context_card("005930", kis=mock_kis)

    assert card.support_price_5d == 48200.0
    assert card.support_price_20d == 47100.0
    assert card.support_reference_px == 48200.0
    assert raw.support_reference_px == 48200.0


async def test_context_card_normalizes_quote_and_liquidity_flags(monkeypatch):
    cc._pykrx_cache.clear()
    monkeypatch.setattr(cc, "_PYKRX_CACHE_TTL", 300)
    monkeypatch.setattr(cc, "_PYKRX_CACHE_MAX_SIZE", 512)

    async def _fake_to_thread(func, *args, **kwargs):
        return {"prev_close": 50000, "adv_value_20d": 20_000_000_000}

    monkeypatch.setattr(cc.asyncio, "to_thread", _fake_to_thread)

    orderbook = OrderbookSnapshot(
        ask_price1=52_100.0,
        bid_price1=51_900.0,
        ask_size1=80,
        bid_size1=120,
        total_ask_size=2000,
        total_bid_size=2500,
        spread_bps=38.5,
    )
    risk_state = QuoteRiskState(temp_stop_yn="Y", sltr_yn="N")
    mock_kis = AsyncMock()
    mock_kis.get_price = AsyncMock(return_value=PriceInfo(
        px=52000,
        open_px=51000,
        spread_bps=38.5,
        cum_value=100_000_000.0,
        fetch_latency_ms=50,
        risk_state=risk_state,
        orderbook=orderbook,
    ))

    card, _raw = await cc.build_context_card("005930", kis=mock_kis)

    assert card.quote_temp_stop is True
    assert card.quote_liquidation_trade is False
    assert card.top_ask_notional == 4_168_000.0


def test_context_card_data_defaults():
    raw = ContextCardData()

    assert raw.adv_value_20d is None
    assert raw.sector == ""
    assert raw.support_reference_px is None


async def test_append_runtime_context_card_writes_jsonl(tmp_path):
    cfg = Config(
        runtime_context_cards_dir=tmp_path / "data" / "runtime" / "context_cards",
        runtime_index_path=tmp_path / "data" / "runtime" / "index.json",
    )
    detected_at = datetime(2026, 3, 16, 0, 15, tzinfo=timezone.utc)
    ctx = ContextCard(adv_value_20d=10e9, spread_bps=10.0, ret_today=5.0)
    raw = ContextCardData(
        adv_value_20d=10e9,
        spread_bps=10.0,
        ret_today=5.0,
        quote_risk_state=QuoteRiskState(temp_stop_yn="Y", vi_cls_code="D"),
        orderbook_snapshot=OrderbookSnapshot(
            ask_price1=50_100.0,
            bid_price1=49_900.0,
            ask_size1=90,
            bid_size1=120,
            total_ask_size=2000,
            total_bid_size=2400,
            spread_bps=40.0,
        ),
    )

    await cc.append_runtime_context_card(
        cfg,
        run_id="run1",
        mode="paper",
        event_id="evt1",
        event_kind="ORIGINAL",
        ticker="005930",
        corp_name="삼성전자",
        headline="공급계약 체결",
        bucket="POS_STRONG",
        detected_at=detected_at,
        disclosed_at=None,
        delay_ms=1234,
        quant_check_passed=True,
        skip_stage=None,
        skip_reason=None,
        promotion_original_event_id="evt0",
        promotion_original_bucket="UNKNOWN",
        promotion_confidence=91,
        promotion_policy="paper_only_conf85_strong_only",
        ctx=ctx,
        raw=raw,
        market_ctx=MarketContext(kospi_change_pct=-0.5, kosdaq_change_pct=0.3),
    )

    files = list((tmp_path / "data" / "runtime" / "context_cards").glob("*.jsonl"))
    assert len(files) == 1
    rows = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
    assert rows[0]["type"] == "context_card"
    assert rows[0]["event_id"] == "evt1"
    assert rows[0]["quant_check_passed"] is True
    assert rows[0]["ctx"]["spread_bps"] == 10.0
    assert rows[0]["raw"]["quote_risk_state"]["temp_stop_yn"] == "Y"
    assert rows[0]["raw"]["orderbook_snapshot"]["ask_price1"] == 50100.0
    assert rows[0]["promotion_original_event_id"] == "evt0"
    assert rows[0]["promotion_original_bucket"] == "UNKNOWN"

    index_payload = json.loads((tmp_path / "data" / "runtime" / "index.json").read_text(encoding="utf-8"))
    assert index_payload["entries"][0]["artifacts"]["context_cards"]["exists"] is True


def test_configure_cache_sets_globals():
    """configure_cache should update module-level cache settings."""
    cc.configure_cache(ttl_s=120, max_size=64)
    assert cc._PYKRX_CACHE_TTL == 120
    assert cc._PYKRX_CACHE_MAX_SIZE == 64
    # Reset to defaults
    cc.configure_cache(ttl_s=300, max_size=512)


def test_configure_cache_enforces_minimums():
    """configure_cache should enforce minimum of 1 for both settings."""
    cc.configure_cache(ttl_s=0, max_size=-5)
    assert cc._PYKRX_CACHE_TTL == 1
    assert cc._PYKRX_CACHE_MAX_SIZE == 1
    cc.configure_cache(ttl_s=300, max_size=512)


def test_prune_cache_removes_expired():
    """_prune_cache should remove entries past TTL."""
    cc._pykrx_cache.clear()
    now = time.monotonic()
    cc._pykrx_cache["expired"] = ({"v": 1}, now - 10)
    cc._pykrx_cache["valid"] = ({"v": 2}, now + 300)
    cc._prune_cache(now, max_size=512)
    assert "expired" not in cc._pykrx_cache
    assert "valid" in cc._pykrx_cache


def test_prune_cache_evicts_oldest_when_over_max():
    """_prune_cache should evict oldest entries when over max_size."""
    cc._pykrx_cache.clear()
    now = time.monotonic()
    for i in range(5):
        cc._pykrx_cache[f"t{i}"] = ({"v": i}, now + 300)
    cc._prune_cache(now, max_size=2)
    assert len(cc._pykrx_cache) == 2


async def test_build_context_card_no_kis(monkeypatch):
    """build_context_card with kis=None should return empty card."""
    cc._pykrx_cache.clear()
    monkeypatch.setattr(cc, "_PYKRX_CACHE_TTL", 300)
    monkeypatch.setattr(cc, "_PYKRX_CACHE_MAX_SIZE", 512)

    async def _fake_to_thread(func, *args, **kwargs):
        return {"prev_close": 50000, "adv_value_20d": 10e9}

    monkeypatch.setattr(cc.asyncio, "to_thread", _fake_to_thread)

    card, raw = await cc.build_context_card("005930", kis=None)
    # No KIS → no price data, but pykrx features still loaded
    assert card.adv_value_20d == 10e9
    assert card.spread_bps is None  # no KIS price → no spread


async def test_append_runtime_context_card_normalizes_numpy_scalar_market_ctx(tmp_path):
    np = pytest.importorskip("numpy")
    cfg = Config(
        runtime_context_cards_dir=tmp_path / "data" / "runtime" / "context_cards",
        runtime_index_path=tmp_path / "data" / "runtime" / "index.json",
    )
    detected_at = datetime(2026, 3, 16, 7, 0, tzinfo=timezone.utc)

    await cc.append_runtime_context_card(
        cfg,
        run_id="run_numpy",
        mode="paper",
        event_id="evt_numpy",
        event_kind="ORIGINAL",
        ticker="005930",
        corp_name="삼성전자",
        headline="시황 테스트",
        bucket="IGNORE",
        detected_at=detected_at,
        disclosed_at=None,
        delay_ms=None,
        quant_check_passed=True,
        skip_stage=None,
        skip_reason=None,
        promotion_original_event_id=None,
        promotion_original_bucket=None,
        promotion_confidence=None,
        promotion_policy=None,
        ctx=ContextCard(),
        raw=ContextCardData(),
        market_ctx={"breadth_ok": np.bool_(True)},
    )

    files = list((tmp_path / "data" / "runtime" / "context_cards").glob("*.jsonl"))
    rows = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
    assert rows[0]["market_ctx"]["breadth_ok"] is True
