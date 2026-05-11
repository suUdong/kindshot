"""VTSBroker — KIS 모의투자 (paper-server) wrapper.

Uses KIS_APP_KEY / KIS_APP_SECRET against the openapivts host with paper
TR_IDs (VTTC0802U / VTTC0801U). Distinct class from LiveBroker.
"""

from __future__ import annotations

import logging
from typing import Optional

import aiohttp

from kindshot.broker._kis_rest import KisCredentials, KisRestClient
from kindshot.broker.base import (
    BrokerBalance,
    BrokerInterface,
    BrokerOrderResult,
    BrokerPosition,
)
from kindshot.config import Config

logger = logging.getLogger(__name__)


class VTSBroker(BrokerInterface):
    """KIS 모의투자 wrapper (openapivts). Distinct from LiveBroker."""

    def __init__(
        self,
        config: Config,
        *,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> None:
        if not config.kis_app_key or not config.kis_app_secret:
            raise ValueError(
                "VTSBroker requires KIS_APP_KEY and KIS_APP_SECRET (모의투자 keys)."
            )
        creds = KisCredentials(
            app_key=config.kis_app_key,
            app_secret=config.kis_app_secret,
            account_no=config.kis_account_no,
            is_paper=True,
        )
        self._client = KisRestClient(creds, session=session)
        self._config = config
        logger.info("VTSBroker initialised against openapivts (KIS 모의투자)")

    @property
    def name(self) -> str:
        return "vts"

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
                success=False, order_no="", ticker=ticker, side=side, qty=qty,
                fill_price=0.0, message=f"invalid side: {side}",
            )
        if qty <= 0:
            return BrokerOrderResult(
                success=False, order_no="", ticker=ticker, side=side, qty=qty,
                fill_price=0.0, message=f"invalid qty: {qty}",
            )

        resp = await self._client.place_order(ticker, qty, side=side, ord_dvsn=ord_dvsn)
        rt_cd = resp.get("rt_cd", "")
        success = rt_cd == "0"
        msg = resp.get("msg1", "")
        order_no = resp.get("order_no", "")
        if success:
            logger.info(
                "VTS %s OK [%s] qty=%d order_no=%s tr_id=%s",
                side, ticker, qty, order_no, resp.get("tr_id", ""),
            )
        else:
            logger.warning(
                "VTS %s REJECT [%s] qty=%d rt_cd=%s msg=%s tr_id=%s",
                side, ticker, qty, rt_cd, msg, resp.get("tr_id", ""),
            )
        return BrokerOrderResult(
            success=success, order_no=order_no, ticker=ticker, side=side,
            qty=qty, fill_price=float(market_price) if success else 0.0,
            message=f"[{rt_cd}] {msg}" if not success else msg,
        )

    async def cancel_order(self, order_no: str, ticker: str) -> bool:
        resp = await self._client.cancel_order(order_no, ticker)
        return resp.get("rt_cd") == "0"

    async def get_balance(self) -> BrokerBalance:
        data = await self._client.fetch_balance()
        cash = float(data.get("cash", 0.0))
        positions: dict[str, BrokerPosition] = {}
        rows = data.get("positions", [])
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                ticker = str(row.get("ticker", ""))
                qty = int(row.get("qty", 0) or 0)
                entry = float(row.get("entry_price", 0.0) or 0.0)
                if ticker and qty > 0:
                    positions[ticker] = BrokerPosition(
                        ticker=ticker, qty=qty, entry_price=entry,
                    )
        equity = cash + sum(p.qty * p.entry_price for p in positions.values())
        return BrokerBalance(cash=cash, equity=equity, positions=positions)

    async def get_position(self, ticker: str) -> Optional[BrokerPosition]:
        balance = await self.get_balance()
        return balance.positions.get(ticker)

    async def get_positions(self) -> dict[str, BrokerPosition]:
        balance = await self.get_balance()
        return balance.positions

    async def close(self) -> None:
        await self._client.close()
