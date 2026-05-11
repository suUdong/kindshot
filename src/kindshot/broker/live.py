"""LiveBroker — KIS 실전계좌 (real-server) wrapper.

Requires KIS_REAL_APP_KEY / KIS_REAL_APP_SECRET. The constructor raises
ValueError when credentials are missing. Supports dry_run for rehearsal —
fills are echoed without hitting the KIS API.

Distinct class from VTSBroker (모의투자 서버) by design: TR_IDs and base URL
are baked into KisCredentials(is_paper=False) and cannot be flipped at runtime.
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


class LiveBroker(BrokerInterface):
    """KIS 실전계좌 wrapper. Distinct from VTSBroker (모의투자)."""

    def __init__(
        self,
        config: Config,
        *,
        session: Optional[aiohttp.ClientSession] = None,
        dry_run: bool = False,
    ) -> None:
        if not config.kis_real_app_key or not config.kis_real_app_secret:
            raise ValueError(
                "LiveBroker requires KIS_REAL_APP_KEY and KIS_REAL_APP_SECRET — "
                "set in env or systemd drop-in, never commit. "
                "Use VTSBroker for 모의투자 (KIS_APP_KEY/KIS_APP_SECRET)."
            )
        creds = KisCredentials(
            app_key=config.kis_real_app_key,
            app_secret=config.kis_real_app_secret,
            account_no=config.kis_account_no,
            is_paper=False,
        )
        self._client = KisRestClient(creds, session=session)
        self._config = config
        self._dry_run = bool(dry_run)
        self._seq = 0
        if self._dry_run:
            logger.warning("LiveBroker DRY_RUN enabled — no real orders will be placed")
        else:
            logger.warning("LiveBroker LIVE mode — orders WILL hit KIS 실전계좌")

    @property
    def name(self) -> str:
        return "live-dry" if self._dry_run else "live"

    @property
    def dry_run(self) -> bool:
        return self._dry_run

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

        if self._dry_run:
            self._seq += 1
            order_no = f"dry-{self._seq:08d}"
            logger.info(
                "LIVE DRY-RUN %s [%s] qty=%d px=%.0f order_no=%s",
                side, ticker, qty, market_price, order_no,
            )
            return BrokerOrderResult(
                success=True, order_no=order_no, ticker=ticker, side=side,
                qty=qty, fill_price=float(market_price), message="dry-run",
                dry_run=True,
            )

        resp = await self._client.place_order(ticker, qty, side=side, ord_dvsn=ord_dvsn)
        rt_cd = resp.get("rt_cd", "")
        success = rt_cd == "0"
        msg = resp.get("msg1", "")
        order_no = resp.get("order_no", "")
        if success:
            logger.info(
                "LIVE %s OK [%s] qty=%d order_no=%s tr_id=%s",
                side, ticker, qty, order_no, resp.get("tr_id", ""),
            )
        else:
            logger.warning(
                "LIVE %s REJECT [%s] qty=%d rt_cd=%s msg=%s tr_id=%s",
                side, ticker, qty, rt_cd, msg, resp.get("tr_id", ""),
            )
        return BrokerOrderResult(
            success=success, order_no=order_no, ticker=ticker, side=side,
            qty=qty, fill_price=float(market_price) if success else 0.0,
            message=f"[{rt_cd}] {msg}" if not success else msg,
        )

    async def cancel_order(self, order_no: str, ticker: str) -> bool:
        if self._dry_run:
            logger.info("LIVE DRY-RUN cancel [%s] order=%s", ticker, order_no)
            return True
        resp = await self._client.cancel_order(order_no, ticker)
        return resp.get("rt_cd") == "0"

    async def get_balance(self) -> BrokerBalance:
        if self._dry_run:
            return BrokerBalance(cash=0.0, equity=0.0, positions={})
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
