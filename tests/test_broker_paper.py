"""Tests for PaperBroker — simulated fills, no network."""

from __future__ import annotations

import asyncio

import pytest

from kindshot.broker import BrokerInterface
from kindshot.broker.paper import PaperBroker


@pytest.fixture
def broker() -> PaperBroker:
    return PaperBroker(starting_cash=10_000_000.0)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def test_paper_broker_is_broker_interface(broker: PaperBroker) -> None:
    assert isinstance(broker, BrokerInterface)
    assert broker.name == "paper"


def test_paper_buy_fill_updates_cash_and_position(broker: PaperBroker) -> None:
    result = _run(broker.place_order("005930", qty=10, side="BUY", market_price=70_000.0))
    assert result.success
    assert result.order_no.startswith("paper-")
    assert result.fill_price == 70_000.0
    assert result.qty == 10

    balance = _run(broker.get_balance())
    assert balance.cash == 10_000_000.0 - 700_000.0
    pos = _run(broker.get_position("005930"))
    assert pos is not None
    assert pos.qty == 10
    assert pos.entry_price == 70_000.0


def test_paper_sell_closes_position_and_returns_cash(broker: PaperBroker) -> None:
    _run(broker.place_order("000660", qty=5, side="BUY", market_price=200_000.0))
    sell = _run(broker.place_order("000660", qty=5, side="SELL", market_price=210_000.0))
    assert sell.success
    assert _run(broker.get_position("000660")) is None
    balance = _run(broker.get_balance())
    # Started 10m → -1m on buy → +1.05m on sell = 10.05m
    assert balance.cash == pytest.approx(10_050_000.0)


def test_paper_sell_without_position_fails(broker: PaperBroker) -> None:
    result = _run(broker.place_order("999999", qty=1, side="SELL", market_price=10_000.0))
    assert not result.success
    assert "no/insufficient position" in result.message


def test_paper_buy_insufficient_cash_fails(broker: PaperBroker) -> None:
    result = _run(broker.place_order("005930", qty=1000, side="BUY", market_price=70_000.0))
    assert not result.success
    assert "insufficient cash" in result.message


def test_paper_buy_blends_entry_price(broker: PaperBroker) -> None:
    _run(broker.place_order("005930", qty=10, side="BUY", market_price=70_000.0))
    _run(broker.place_order("005930", qty=10, side="BUY", market_price=80_000.0))
    pos = _run(broker.get_position("005930"))
    assert pos is not None
    assert pos.qty == 20
    assert pos.entry_price == pytest.approx(75_000.0)


def test_paper_cancel_is_noop_success(broker: PaperBroker) -> None:
    assert _run(broker.cancel_order("paper-00000001", "005930")) is True


def test_paper_invalid_inputs_fail_fast(broker: PaperBroker) -> None:
    bad_side = _run(broker.place_order("005930", qty=1, side="HOLD", market_price=1000.0))
    assert not bad_side.success
    bad_qty = _run(broker.place_order("005930", qty=0, side="BUY", market_price=1000.0))
    assert not bad_qty.success
    bad_px = _run(broker.place_order("005930", qty=1, side="BUY", market_price=0.0))
    assert not bad_px.success
