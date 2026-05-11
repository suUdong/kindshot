"""Tests for LiveBroker — KIS 실전계좌 wrapper."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from kindshot.broker.live import LiveBroker
from kindshot.broker._kis_rest import KisCredentials, KisRestClient
from kindshot.config import Config


def _live_config(real_keys: bool = True, account: str = "12345678-01") -> Config:
    return Config(
        kis_app_key="paper-key",
        kis_app_secret="paper-secret",
        kis_account_no=account,
        kis_real_app_key="real-key" if real_keys else "",
        kis_real_app_secret="real-secret" if real_keys else "",
    )


def _run(coro):
    return asyncio.run(coro)


def test_live_broker_missing_real_keys_raises() -> None:
    with pytest.raises(ValueError, match="KIS_REAL_APP_KEY"):
        LiveBroker(_live_config(real_keys=False))


def test_live_broker_distinct_from_vts_uses_real_tr_id() -> None:
    broker = LiveBroker(_live_config(), dry_run=True)
    # creds are baked: paper=False → TR_ID 'TTTC0802U' for BUY
    creds = broker._client._creds
    assert not creds.is_paper
    assert creds.order_tr_id("BUY") == "TTTC0802U"
    assert creds.order_tr_id("SELL") == "TTTC0801U"
    assert "openapi.koreainvestment.com" in creds.base_url()
    assert "openapivts" not in creds.base_url()


def test_live_broker_dry_run_does_not_call_kis() -> None:
    broker = LiveBroker(_live_config(), dry_run=True)
    with patch.object(KisRestClient, "place_order", new=AsyncMock()) as mock:
        result = _run(broker.place_order("005930", 5, side="BUY", market_price=70_000))
    assert result.success
    assert result.dry_run
    assert result.order_no.startswith("dry-")
    mock.assert_not_called()


def test_live_broker_calls_kis_when_not_dry_run() -> None:
    broker = LiveBroker(_live_config(), dry_run=False)
    fake_resp = {"rt_cd": "0", "msg1": "정상", "order_no": "0000123456", "tr_id": "TTTC0802U"}
    with patch.object(KisRestClient, "place_order", new=AsyncMock(return_value=fake_resp)) as mock:
        result = _run(broker.place_order("005930", 5, side="BUY", market_price=70_000))
    mock.assert_called_once()
    assert result.success
    assert result.order_no == "0000123456"
    assert result.fill_price == 70_000.0
    assert not result.dry_run


def test_live_broker_kis_rejection_returns_failure() -> None:
    broker = LiveBroker(_live_config(), dry_run=False)
    fake_resp = {"rt_cd": "1", "msg1": "주문 가능 금액 부족", "order_no": "", "tr_id": "TTTC0802U"}
    with patch.object(KisRestClient, "place_order", new=AsyncMock(return_value=fake_resp)):
        result = _run(broker.place_order("005930", 5, side="BUY", market_price=70_000))
    assert not result.success
    assert "주문 가능 금액 부족" in result.message
    assert result.fill_price == 0.0


def test_live_broker_invalid_side_or_qty_fails_fast() -> None:
    broker = LiveBroker(_live_config(), dry_run=True)
    bad_side = _run(broker.place_order("005930", 5, side="HOLD"))
    assert not bad_side.success
    bad_qty = _run(broker.place_order("005930", 0, side="BUY"))
    assert not bad_qty.success


def test_live_broker_balance_parses_kis_payload() -> None:
    broker = LiveBroker(_live_config(), dry_run=False)
    fake = {
        "cash": 5_000_000.0,
        "positions": [
            {"ticker": "005930", "qty": 10, "entry_price": 70_000.0},
            {"ticker": "000660", "qty": 5, "entry_price": 200_000.0},
        ],
    }
    with patch.object(KisRestClient, "fetch_balance", new=AsyncMock(return_value=fake)):
        balance = _run(broker.get_balance())
    assert balance.cash == 5_000_000.0
    assert len(balance.positions) == 2
    assert balance.positions["005930"].qty == 10
    assert balance.equity == pytest.approx(5_000_000.0 + 10 * 70_000 + 5 * 200_000)


def test_live_broker_dry_run_balance_returns_zero() -> None:
    broker = LiveBroker(_live_config(), dry_run=True)
    balance = _run(broker.get_balance())
    assert balance.cash == 0.0
    assert balance.equity == 0.0


def test_live_broker_cancel_dry_run_succeeds() -> None:
    broker = LiveBroker(_live_config(), dry_run=True)
    assert _run(broker.cancel_order("0000123456", "005930")) is True


def test_kis_credentials_invalid_account_raises() -> None:
    with pytest.raises(ValueError, match="account_no"):
        KisRestClient(KisCredentials(
            app_key="k", app_secret="s", account_no="123", is_paper=False,
        ))
