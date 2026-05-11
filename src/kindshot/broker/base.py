"""Broker abstraction for Kindshot.

PaperBroker (simulated), VTSBroker (KIS 모의투자 서버), and LiveBroker (KIS 실전계좌)
all conform to the BrokerInterface ABC defined here. The existing OrderExecutor /
KisClient stack in src/kindshot/{order,kis_client}.py is left untouched —
this package layers a uniform interface so the daemon can opt into live mode
behind safety preflight gates without changing the paper code path.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class BrokerOrderResult:
    success: bool
    order_no: str
    ticker: str
    side: str          # "BUY" or "SELL"
    qty: int
    fill_price: float  # 시장가 체결가 (실패 시 0.0)
    message: str = ""
    dry_run: bool = False


@dataclass
class BrokerPosition:
    ticker: str
    qty: int
    entry_price: float


@dataclass
class BrokerBalance:
    cash: float
    equity: float
    positions: dict[str, BrokerPosition] = field(default_factory=dict)


class BrokerInterface(ABC):
    """Async broker interface implemented by Paper / VTS / Live brokers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable broker tag, e.g. 'paper', 'vts', 'live'."""

    @abstractmethod
    async def place_order(
        self,
        ticker: str,
        qty: int,
        *,
        side: str = "BUY",
        ord_dvsn: str = "01",
        market_price: float = 0.0,
    ) -> BrokerOrderResult:
        """Submit a market/limit order. side='BUY'|'SELL', ord_dvsn '01'=시장가."""

    @abstractmethod
    async def cancel_order(self, order_no: str, ticker: str) -> bool:
        """Cancel a pending order. Returns True when the broker accepted the cancel."""

    @abstractmethod
    async def get_balance(self) -> BrokerBalance:
        """Return current cash + equity + open positions snapshot."""

    @abstractmethod
    async def get_position(self, ticker: str) -> Optional[BrokerPosition]:
        """Return the open position for a ticker, or None."""

    @abstractmethod
    async def get_positions(self) -> dict[str, BrokerPosition]:
        """Return all open positions keyed by ticker."""
