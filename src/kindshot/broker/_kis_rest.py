"""Minimal KIS REST wrapper shared by VTSBroker and LiveBroker.

A thin extract — does NOT replace kindshot.kis_client.KisClient. The daemon's
KisClient handles market-data + dual-server routing; this helper covers the
small set of trading-side endpoints (order-cash, balance, position) that the
broker layer needs, with explicit server targeting so VTSBroker and
LiveBroker remain distinct classes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

BASE_URL_REAL = "https://openapi.koreainvestment.com:9443"
BASE_URL_PAPER = "https://openapivts.koreainvestment.com:29443"

_RATE_LIMIT_REAL = 0.05
_RATE_LIMIT_PAPER = 0.5


@dataclass
class KisCredentials:
    app_key: str
    app_secret: str
    account_no: str
    is_paper: bool

    def base_url(self) -> str:
        return BASE_URL_PAPER if self.is_paper else BASE_URL_REAL

    def rate_limit(self) -> float:
        return _RATE_LIMIT_PAPER if self.is_paper else _RATE_LIMIT_REAL

    def order_tr_id(self, side: str) -> str:
        if side == "BUY":
            return "VTTC0802U" if self.is_paper else "TTTC0802U"
        return "VTTC0801U" if self.is_paper else "TTTC0801U"

    def cancel_tr_id(self) -> str:
        return "VTTC0803U" if self.is_paper else "TTTC0803U"

    def balance_tr_id(self) -> str:
        return "VTTC8434R" if self.is_paper else "TTTC8434R"


class KisRestClient:
    """Tiny KIS REST client scoped to broker-level trading endpoints."""

    def __init__(self, creds: KisCredentials, session: Optional[aiohttp.ClientSession] = None) -> None:
        if not creds.app_key or not creds.app_secret:
            raise ValueError(
                f"KIS credentials missing (paper={creds.is_paper}). "
                "Live broker requires KIS_REAL_APP_KEY/KIS_REAL_APP_SECRET; "
                "VTS broker requires KIS_APP_KEY/KIS_APP_SECRET."
            )
        if not creds.account_no or len(creds.account_no.replace("-", "").strip()) < 10:
            raise ValueError(
                f"KIS account_no invalid (len={len(creds.account_no)}) — set KIS_ACCOUNT_NO."
            )
        self._creds = creds
        self._session = session
        self._owns_session = session is None
        self._token: Optional[str] = None
        self._token_expires: float = 0.0
        self._last_request: float = 0.0
        self._lock = asyncio.Lock()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()

    async def _ensure_token(self) -> Optional[str]:
        async with self._lock:
            if self._token and time.time() < self._token_expires:
                return self._token
            session = await self._ensure_session()
            try:
                async with session.post(
                    f"{self._creds.base_url()}/oauth2/tokenP",
                    json={
                        "grant_type": "client_credentials",
                        "appkey": self._creds.app_key,
                        "appsecret": self._creds.app_secret,
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()
            except Exception:
                logger.exception("KIS token fetch failed (paper=%s)", self._creds.is_paper)
                return None
            token = data.get("access_token") if isinstance(data, dict) else None
            if not token:
                logger.warning("KIS token response missing access_token: %r", data)
                return None
            self._token = token
            self._token_expires = time.time() + 23 * 3600
            return token

    async def _rate_limit_wait(self) -> None:
        elapsed = time.monotonic() - self._last_request
        rl = self._creds.rate_limit()
        if elapsed < rl:
            await asyncio.sleep(rl - elapsed)
        self._last_request = time.monotonic()

    def _headers(self, token: str, tr_id: str) -> dict[str, str]:
        return {
            "authorization": f"Bearer {token}",
            "appkey": self._creds.app_key,
            "appsecret": self._creds.app_secret,
            "tr_id": tr_id,
            "content-type": "application/json; charset=utf-8",
        }

    def _account_parts(self) -> tuple[str, str]:
        acc = self._creds.account_no.replace("-", "").strip()
        return acc[:8], acc[8:10]

    async def place_order(
        self,
        ticker: str,
        qty: int,
        *,
        side: str,
        ord_dvsn: str = "01",
    ) -> dict[str, str]:
        token = await self._ensure_token()
        if not token:
            return {"rt_cd": "1", "msg1": "no auth token", "order_no": ""}
        cano, acnt_prdt_cd = self._account_parts()
        body = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "PDNO": ticker,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(qty),
            "ORD_UNPR": "0",
        }
        tr_id = self._creds.order_tr_id(side)
        await self._rate_limit_wait()
        session = await self._ensure_session()
        try:
            async with session.post(
                f"{self._creds.base_url()}/uapi/domestic-stock/v1/trading/order-cash",
                headers=self._headers(token, tr_id),
                json=body,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
        except Exception as exc:
            logger.exception("KIS order failed (side=%s, ticker=%s)", side, ticker)
            return {"rt_cd": "1", "msg1": f"request exception: {exc}", "order_no": "", "tr_id": tr_id}
        output = data.get("output") if isinstance(data, dict) else {}
        order_no = str(output.get("ODNO", "")) if isinstance(output, dict) else ""
        return {
            "rt_cd": str(data.get("rt_cd", "")),
            "msg1": str(data.get("msg1", "")),
            "order_no": order_no,
            "tr_id": tr_id,
        }

    async def cancel_order(self, order_no: str, ticker: str) -> dict[str, str]:
        token = await self._ensure_token()
        if not token:
            return {"rt_cd": "1", "msg1": "no auth token"}
        cano, acnt_prdt_cd = self._account_parts()
        body = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "KRX_FWDG_ORD_ORGNO": "",
            "ORGN_ODNO": order_no,
            "ORD_DVSN": "00",
            "RVSE_CNCL_DVSN_CD": "02",  # 02=취소
            "ORD_QTY": "0",
            "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "Y",
        }
        tr_id = self._creds.cancel_tr_id()
        await self._rate_limit_wait()
        session = await self._ensure_session()
        try:
            async with session.post(
                f"{self._creds.base_url()}/uapi/domestic-stock/v1/trading/order-rvsecncl",
                headers=self._headers(token, tr_id),
                json=body,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
        except Exception as exc:
            logger.exception("KIS cancel failed (order=%s, ticker=%s)", order_no, ticker)
            return {"rt_cd": "1", "msg1": f"request exception: {exc}", "tr_id": tr_id}
        return {
            "rt_cd": str(data.get("rt_cd", "")),
            "msg1": str(data.get("msg1", "")),
            "tr_id": tr_id,
        }

    async def fetch_balance(self) -> dict[str, object]:
        """주식잔고조회. Returns {'cash': float, 'positions': [{'ticker','qty','entry_price'}]}."""
        token = await self._ensure_token()
        if not token:
            return {"cash": 0.0, "positions": [], "error": "no auth token"}
        cano, acnt_prdt_cd = self._account_parts()
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        tr_id = self._creds.balance_tr_id()
        await self._rate_limit_wait()
        session = await self._ensure_session()
        try:
            async with session.get(
                f"{self._creds.base_url()}/uapi/domestic-stock/v1/trading/inquire-balance",
                headers=self._headers(token, tr_id),
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
        except Exception as exc:
            logger.exception("KIS balance fetch failed")
            return {"cash": 0.0, "positions": [], "error": f"{exc}"}
        if not isinstance(data, dict):
            return {"cash": 0.0, "positions": [], "error": "invalid payload"}
        output1 = data.get("output1", []) or []
        output2 = data.get("output2", []) or []
        positions: list[dict[str, object]] = []
        if isinstance(output1, list):
            for row in output1:
                if not isinstance(row, dict):
                    continue
                try:
                    qty = int(float(row.get("hldg_qty", 0)))
                except (TypeError, ValueError):
                    continue
                if qty <= 0:
                    continue
                try:
                    entry_price = float(row.get("pchs_avg_pric", 0))
                except (TypeError, ValueError):
                    entry_price = 0.0
                ticker = str(row.get("pdno", "")).strip()
                if not ticker:
                    continue
                positions.append({"ticker": ticker, "qty": qty, "entry_price": entry_price})
        cash = 0.0
        if isinstance(output2, list) and output2:
            head = output2[0] if isinstance(output2[0], dict) else {}
            try:
                cash = float(head.get("dnca_tot_amt", 0))
            except (TypeError, ValueError):
                cash = 0.0
        return {"cash": cash, "positions": positions, "tr_id": tr_id}
