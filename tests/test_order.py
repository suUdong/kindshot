"""Dry-run tests for order execution flow — verifies logic without placing real orders."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from kindshot.order import OrderExecutor, OrderResult


@dataclass(frozen=True)
class FakeOrderResponse:
    success: bool
    order_no: str
    message: str


def _make_executor(
    *,
    max_order_won: float = 1_000_000,
    order_success: bool = True,
    order_no: str = "0000001",
) -> tuple[OrderExecutor, MagicMock]:
    """Build an OrderExecutor with a mocked KisClient."""
    kis = MagicMock()
    kis.place_order = AsyncMock(
        return_value=FakeOrderResponse(
            success=order_success,
            order_no=order_no,
            message="OK" if order_success else "REJECTED",
        )
    )
    config = MagicMock()
    config.micro_live_max_order_won = max_order_won
    return OrderExecutor(kis, config), kis


# ── buy_market ──


@pytest.mark.asyncio
async def test_buy_market_qty_calculation():
    """qty = int(target_won / current_price), safety cap 적용."""
    executor, kis = _make_executor(max_order_won=1_000_000)
    result = await executor.buy_market(
        event_id="evt_001", ticker="005930",
        target_won=5_000_000, current_price=70_000,
    )
    assert result.success
    # 1,000,000 cap / 70,000 = 14주
    assert result.qty == 14
    assert result.side == "BUY"
    kis.place_order.assert_awaited_once_with("005930", 14, side="BUY")


@pytest.mark.asyncio
async def test_buy_market_no_cap_needed():
    """target_won < cap 이면 cap 미적용."""
    executor, kis = _make_executor(max_order_won=10_000_000)
    result = await executor.buy_market(
        event_id="evt_002", ticker="035420",
        target_won=3_000_000, current_price=300_000,
    )
    assert result.success
    # 3,000,000 / 300,000 = 10주
    assert result.qty == 10


@pytest.mark.asyncio
async def test_buy_market_zero_price():
    """가격이 0이면 주문 안 함."""
    executor, kis = _make_executor()
    result = await executor.buy_market(
        event_id="evt_003", ticker="005930",
        target_won=1_000_000, current_price=0,
    )
    assert not result.success
    assert result.qty == 0
    kis.place_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_buy_market_qty_rounds_to_zero():
    """가격이 너무 비싸서 qty=0이면 주문 안 함."""
    executor, kis = _make_executor(max_order_won=500_000)
    result = await executor.buy_market(
        event_id="evt_004", ticker="005930",
        target_won=5_000_000, current_price=600_000,
    )
    assert not result.success
    assert result.qty == 0
    kis.place_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_buy_market_failure():
    """KIS 거부 시 포지션 미등록."""
    executor, kis = _make_executor(order_success=False)
    result = await executor.buy_market(
        event_id="evt_005", ticker="005930",
        target_won=1_000_000, current_price=70_000,
    )
    assert not result.success
    assert not executor.has_position("evt_005")


@pytest.mark.asyncio
async def test_buy_market_registers_position():
    """매수 성공 시 포지션 등록."""
    executor, _ = _make_executor()
    await executor.buy_market(
        event_id="evt_006", ticker="005930",
        target_won=1_000_000, current_price=70_000,
    )
    assert executor.has_position("evt_006")
    pos = executor.positions["evt_006"]
    assert pos.ticker == "005930"
    assert pos.qty == 14


# ── sell_position ──


@pytest.mark.asyncio
async def test_sell_position_success():
    """매도 성공 시 포지션 제거."""
    executor, kis = _make_executor()
    await executor.buy_market(
        event_id="evt_007", ticker="005930",
        target_won=1_000_000, current_price=70_000,
    )
    result = await executor.sell_position("evt_007", "005930")
    assert result is not None
    assert result.success
    assert result.side == "SELL"
    assert result.qty == 14
    assert not executor.has_position("evt_007")


@pytest.mark.asyncio
async def test_sell_position_no_position():
    """포지션 없으면 None 반환."""
    executor, _ = _make_executor()
    result = await executor.sell_position("nonexistent", "005930")
    assert result is None


@pytest.mark.asyncio
async def test_sell_position_failure_restores():
    """매도 실패 시 포지션 복원."""
    executor, kis = _make_executor(order_success=True)
    await executor.buy_market(
        event_id="evt_008", ticker="005930",
        target_won=1_000_000, current_price=70_000,
    )
    # 매도 실패 설정
    kis.place_order = AsyncMock(
        return_value=FakeOrderResponse(success=False, order_no="", message="NETWORK_ERROR")
    )
    result = await executor.sell_position("evt_008", "005930")
    assert result is not None
    assert not result.success
    # 포지션 복원됨
    assert executor.has_position("evt_008")


# ── end-to-end flow ──


@pytest.mark.asyncio
async def test_buy_then_sell_flow():
    """BUY → SELL 전체 흐름 검증."""
    executor, kis = _make_executor(max_order_won=2_000_000)

    # BUY
    buy = await executor.buy_market(
        event_id="evt_flow", ticker="035420",
        target_won=5_000_000, current_price=200_000,
    )
    assert buy.success
    assert buy.qty == 10  # 2M cap / 200k = 10

    # SELL
    sell = await executor.sell_position("evt_flow", "035420")
    assert sell is not None
    assert sell.success
    assert sell.qty == 10
    assert not executor.has_position("evt_flow")

    # 2회 호출 확인 (BUY + SELL)
    assert kis.place_order.await_count == 2
