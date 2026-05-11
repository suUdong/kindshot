"""Tests for VTSBroker — KIS 모의투자 wrapper."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from kindshot.broker._kis_rest import KisRestClient
from kindshot.broker.vts import VTSBroker
from kindshot.config import Config


def _vts_config(paper_keys: bool = True) -> Config:
    return Config(
        kis_app_key="paper-key" if paper_keys else "",
        kis_app_secret="paper-secret" if paper_keys else "",
        kis_account_no="12345678-01",
    )


def _run(coro):
    return asyncio.run(coro)


def test_vts_missing_paper_keys_raises() -> None:
    with pytest.raises(ValueError, match="KIS_APP_KEY"):
        VTSBroker(_vts_config(paper_keys=False))


def test_vts_uses_paper_tr_ids_and_vts_host() -> None:
    broker = VTSBroker(_vts_config())
    creds = broker._client._creds
    assert creds.is_paper
    assert creds.order_tr_id("BUY") == "VTTC0802U"
    assert creds.order_tr_id("SELL") == "VTTC0801U"
    assert "openapivts" in creds.base_url()


def test_vts_place_order_success() -> None:
    broker = VTSBroker(_vts_config())
    fake_resp = {"rt_cd": "0", "msg1": "정상", "order_no": "VTSORD001", "tr_id": "VTTC0802U"}
    with patch.object(KisRestClient, "place_order", new=AsyncMock(return_value=fake_resp)):
        result = _run(broker.place_order("005930", 5, side="BUY", market_price=70_000))
    assert result.success
    assert result.order_no == "VTSORD001"
    assert broker.name == "vts"


def test_vts_place_order_rejection() -> None:
    broker = VTSBroker(_vts_config())
    fake_resp = {"rt_cd": "1", "msg1": "모의투자 한도 초과", "order_no": "", "tr_id": "VTTC0802U"}
    with patch.object(KisRestClient, "place_order", new=AsyncMock(return_value=fake_resp)):
        result = _run(broker.place_order("005930", 5, side="BUY", market_price=70_000))
    assert not result.success
    assert "모의투자 한도 초과" in result.message


def test_vts_balance_parses_payload() -> None:
    broker = VTSBroker(_vts_config())
    fake = {
        "cash": 1_000_000.0,
        "positions": [{"ticker": "005930", "qty": 2, "entry_price": 70_000.0}],
    }
    with patch.object(KisRestClient, "fetch_balance", new=AsyncMock(return_value=fake)):
        balance = _run(broker.get_balance())
    assert balance.cash == 1_000_000.0
    assert balance.positions["005930"].qty == 2
