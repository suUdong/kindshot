"""Tests for OrderExecutor.buy_market_with_retry()."""

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest
from kindshot.order import OrderExecutor, OrderResult


@dataclass
class FakePlaceOrderResp:
    success: bool
    order_no: str
    message: str


@pytest.fixture
def make_executor():
    def _factory(responses: list[FakePlaceOrderResp]):
        kis = MagicMock()
        call_count = 0

        async def fake_place_order(ticker, qty, side="BUY"):
            nonlocal call_count
            resp = responses[min(call_count, len(responses) - 1)]
            call_count += 1
            return resp

        kis.place_order = AsyncMock(side_effect=fake_place_order)
        config = MagicMock()
        config.micro_live_max_order_won = 10_000_000
        executor = OrderExecutor(kis=kis, config=config)
        return executor, kis

    return _factory


@pytest.mark.asyncio
async def test_immediate_success(make_executor):
    """첫 시도 성공 → 재시도 없이 즉시 반환."""
    executor, kis = make_executor([
        FakePlaceOrderResp(success=True, order_no="ORD001", message="OK"),
    ])
    result = await executor.buy_market_with_retry(
        "evt1", "005930", 5_000_000, 70000, base_delay_s=0.01,
    )
    assert result.success is True
    assert result.order_no == "ORD001"
    assert kis.place_order.call_count == 1


@pytest.mark.asyncio
async def test_fail_then_success(make_executor):
    """1회 실패 후 2회째 성공."""
    executor, kis = make_executor([
        FakePlaceOrderResp(success=False, order_no="", message="TIMEOUT"),
        FakePlaceOrderResp(success=True, order_no="ORD002", message="OK"),
    ])
    result = await executor.buy_market_with_retry(
        "evt2", "005930", 5_000_000, 70000, base_delay_s=0.01,
    )
    assert result.success is True
    assert result.order_no == "ORD002"
    assert kis.place_order.call_count == 2


@pytest.mark.asyncio
async def test_all_retries_exhausted(make_executor):
    """모든 재시도 실패 → 마지막 실패 결과 반환."""
    executor, kis = make_executor([
        FakePlaceOrderResp(success=False, order_no="", message="FAIL1"),
        FakePlaceOrderResp(success=False, order_no="", message="FAIL2"),
        FakePlaceOrderResp(success=False, order_no="", message="FAIL3"),
    ])
    result = await executor.buy_market_with_retry(
        "evt3", "005930", 5_000_000, 70000, max_retries=2, base_delay_s=0.01,
    )
    assert result.success is False
    assert kis.place_order.call_count == 3  # 1 initial + 2 retries


@pytest.mark.asyncio
async def test_price_zero_no_retry(make_executor):
    """current_price=0이면 buy_market에서 바로 실패 → 재시도해도 동일."""
    executor, kis = make_executor([])  # place_order never called
    result = await executor.buy_market_with_retry(
        "evt4", "005930", 5_000_000, 0, max_retries=2, base_delay_s=0.01,
    )
    assert result.success is False
    assert result.qty == 0
    assert kis.place_order.call_count == 0
