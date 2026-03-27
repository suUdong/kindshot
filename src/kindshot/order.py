"""Live order execution via KIS API."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from kindshot.config import Config
    from kindshot.kis_client import KisClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OrderResult:
    success: bool
    order_no: str
    ticker: str
    side: str  # "BUY" or "SELL"
    qty: int
    message: str


@dataclass
class Position:
    ticker: str
    qty: int
    entry_price: float
    event_id: str


class OrderExecutor:
    """Manages live order execution and position tracking.

    - buy_market: 시장가 매수 → qty 계산, safety cap 적용, KIS API 호출
    - sell_position: event_id 기반 포지션 시장가 매도
    """

    def __init__(self, kis: "KisClient", config: "Config") -> None:
        self._kis = kis
        self._config = config
        self._positions: dict[str, Position] = {}  # event_id -> Position

    @property
    def positions(self) -> dict[str, Position]:
        return dict(self._positions)

    async def buy_market(
        self,
        event_id: str,
        ticker: str,
        target_won: float,
        current_price: float,
    ) -> OrderResult:
        """시장가 매수. target_won 금액 기준으로 수량 계산 후 주문."""
        if current_price <= 0:
            return OrderResult(
                success=False, order_no="", ticker=ticker,
                side="BUY", qty=0, message="current_price <= 0",
            )

        # Safety cap: micro-live 주문 금액 상한
        max_won = self._config.micro_live_max_order_won
        capped = min(target_won, max_won) if max_won > 0 else target_won
        qty = int(capped / current_price)
        if qty <= 0:
            return OrderResult(
                success=False, order_no="", ticker=ticker,
                side="BUY", qty=0,
                message=f"qty=0 (target={capped:.0f}won, px={current_price:.0f})",
            )

        logger.info(
            "LIVE BUY attempt [%s] qty=%d px=%.0f amount=%.0f (cap=%.0f)",
            ticker, qty, current_price, qty * current_price, max_won,
        )

        resp = await self._kis.place_order(ticker, qty, side="BUY")
        result = OrderResult(
            success=resp.success, order_no=resp.order_no,
            ticker=ticker, side="BUY", qty=qty, message=resp.message,
        )
        if resp.success:
            self._positions[event_id] = Position(
                ticker=ticker, qty=qty,
                entry_price=current_price, event_id=event_id,
            )
            logger.info(
                "LIVE BUY OK [%s] order_no=%s qty=%d amount=%.0f (positions=%d)",
                ticker, resp.order_no, qty, qty * current_price, len(self._positions),
            )
        else:
            logger.warning("LIVE BUY FAIL [%s]: %s", ticker, resp.message)
        return result

    async def sell_position(self, event_id: str, ticker: str) -> Optional[OrderResult]:
        """event_id 포지션 시장가 매도. 포지션 없으면 None 반환."""
        pos = self._positions.pop(event_id, None)
        if pos is None:
            logger.warning("LIVE SELL skip [%s]: no position for %s", ticker, event_id[:8])
            return None

        logger.info("LIVE SELL attempt [%s] qty=%d event=%s", ticker, pos.qty, event_id[:8])
        resp = await self._kis.place_order(ticker, pos.qty, side="SELL")
        result = OrderResult(
            success=resp.success, order_no=resp.order_no,
            ticker=ticker, side="SELL", qty=pos.qty, message=resp.message,
        )
        if resp.success:
            logger.info(
                "LIVE SELL OK [%s] order_no=%s qty=%d (positions=%d)",
                ticker, resp.order_no, pos.qty, len(self._positions),
            )
        else:
            # 실패 시 포지션 복원 (재시도 가능)
            self._positions[event_id] = pos
            logger.warning("LIVE SELL FAIL [%s]: %s (position restored)", ticker, resp.message)
        return result

    def has_position(self, event_id: str) -> bool:
        return event_id in self._positions
