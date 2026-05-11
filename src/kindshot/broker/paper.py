"""PaperBroker — simulated fills, no network calls. Used by daemon when paper_trading=True."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from kindshot.broker.base import (
    BrokerBalance,
    BrokerInterface,
    BrokerOrderResult,
    BrokerPosition,
)

logger = logging.getLogger(__name__)


class PaperBroker(BrokerInterface):
    """In-memory simulated broker. Fills at the market_price hint provided to place_order."""

    def __init__(self, starting_cash: float = 50_000_000.0) -> None:
        self._cash = float(starting_cash)
        self._positions: dict[str, BrokerPosition] = {}
        self._seq = 0
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return "paper"

    async def place_order(
        self,
        ticker: str,
        qty: int,
        *,
        side: str = "BUY",
        ord_dvsn: str = "01",
        market_price: float = 0.0,
    ) -> BrokerOrderResult:
        side = side.upper()
        if side not in ("BUY", "SELL"):
            return BrokerOrderResult(
                success=False, order_no="", ticker=ticker, side=side,
                qty=qty, fill_price=0.0, message=f"invalid side: {side}",
            )
        if qty <= 0:
            return BrokerOrderResult(
                success=False, order_no="", ticker=ticker, side=side,
                qty=qty, fill_price=0.0, message=f"invalid qty: {qty}",
            )
        if market_price <= 0:
            return BrokerOrderResult(
                success=False, order_no="", ticker=ticker, side=side,
                qty=qty, fill_price=0.0, message="market_price required for paper fill",
            )

        async with self._lock:
            self._seq += 1
            order_no = f"paper-{self._seq:08d}"
            notional = float(market_price) * qty

            if side == "BUY":
                if notional > self._cash:
                    return BrokerOrderResult(
                        success=False, order_no="", ticker=ticker, side=side,
                        qty=qty, fill_price=0.0,
                        message=f"insufficient cash: need={notional:.0f} have={self._cash:.0f}",
                    )
                self._cash -= notional
                existing = self._positions.get(ticker)
                if existing is None:
                    self._positions[ticker] = BrokerPosition(
                        ticker=ticker, qty=qty, entry_price=float(market_price),
                    )
                else:
                    new_qty = existing.qty + qty
                    blended = (
                        (existing.entry_price * existing.qty) + (market_price * qty)
                    ) / new_qty
                    self._positions[ticker] = BrokerPosition(
                        ticker=ticker, qty=new_qty, entry_price=blended,
                    )
            else:  # SELL
                pos = self._positions.get(ticker)
                if pos is None or pos.qty < qty:
                    return BrokerOrderResult(
                        success=False, order_no="", ticker=ticker, side=side,
                        qty=qty, fill_price=0.0,
                        message=f"no/insufficient position to sell: have={pos.qty if pos else 0}",
                    )
                self._cash += notional
                remaining = pos.qty - qty
                if remaining <= 0:
                    del self._positions[ticker]
                else:
                    self._positions[ticker] = BrokerPosition(
                        ticker=ticker, qty=remaining, entry_price=pos.entry_price,
                    )

            logger.info(
                "PAPER %s [%s] qty=%d px=%.0f order_no=%s cash=%.0f",
                side, ticker, qty, market_price, order_no, self._cash,
            )
            return BrokerOrderResult(
                success=True, order_no=order_no, ticker=ticker, side=side,
                qty=qty, fill_price=float(market_price), message="ok",
            )

    async def cancel_order(self, order_no: str, ticker: str) -> bool:
        # Paper fills are instantaneous, so cancellation is a no-op success.
        logger.debug("PAPER cancel (noop) order_no=%s ticker=%s", order_no, ticker)
        return True

    async def get_balance(self) -> BrokerBalance:
        equity = self._cash + sum(
            pos.qty * pos.entry_price for pos in self._positions.values()
        )
        return BrokerBalance(cash=self._cash, equity=equity, positions=dict(self._positions))

    async def get_position(self, ticker: str) -> Optional[BrokerPosition]:
        return self._positions.get(ticker)

    async def get_positions(self) -> dict[str, BrokerPosition]:
        return dict(self._positions)
