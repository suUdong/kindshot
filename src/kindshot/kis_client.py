"""KIS REST API client. Gracefully returns None when credentials are missing."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

import aiohttp

from kindshot.config import Config

logger = logging.getLogger(__name__)

BASE_URL_REAL = "https://openapi.koreainvestment.com:9443"
BASE_URL_PAPER = "https://openapivts.koreainvestment.com:29443"


@dataclass
class PriceInfo:
    px: float
    spread_bps: Optional[float]
    cum_value: Optional[float]
    fetch_latency_ms: int


class KisClient:
    """KIS REST API client with token management."""

    def __init__(self, config: Config, session: aiohttp.ClientSession) -> None:
        self._config = config
        self._session = session
        self._base = BASE_URL_PAPER if config.kis_is_paper else BASE_URL_REAL
        self._token: Optional[str] = None
        self._token_expires: float = 0.0

    async def _ensure_token(self) -> Optional[str]:
        if self._token and time.time() < self._token_expires:
            return self._token

        try:
            async with self._session.post(
                f"{self._base}/oauth2/tokenP",
                json={
                    "grant_type": "client_credentials",
                    "appkey": self._config.kis_app_key,
                    "appsecret": self._config.kis_app_secret,
                },
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                data = await resp.json()
                self._token = data.get("access_token")
                # Expire 23h to be safe (actual: 24h)
                self._token_expires = time.time() + 23 * 3600
                return self._token
        except Exception:
            logger.exception("KIS token fetch failed")
            return None

    def _headers(self, token: str, tr_id: str) -> dict[str, str]:
        return {
            "authorization": f"Bearer {token}",
            "appkey": self._config.kis_app_key,
            "appsecret": self._config.kis_app_secret,
            "tr_id": tr_id,
            "content-type": "application/json; charset=utf-8",
        }

    async def get_price(self, ticker: str) -> Optional[PriceInfo]:
        """Get current price for a ticker. Returns None on any failure."""
        token = await self._ensure_token()
        if not token:
            return None

        t0 = time.monotonic()
        try:
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker,
            }
            async with self._session.get(
                f"{self._base}/uapi/domestic-stock/v1/quotations/inquire-price",
                headers=self._headers(token, "FHKST01010100"),
                params=params,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                data = await resp.json()
                output = data.get("output", {})

                px = float(output.get("stck_prpr", 0))
                ask = float(output.get("stck_sdpr", 0))  # best ask
                bid = float(output.get("stck_hgpr", 0))  # best bid — field names may vary
                cum_value = float(output.get("acml_tr_pbmn", 0))

                spread_bps = None
                if ask > 0 and bid > 0 and px > 0:
                    spread_bps = ((ask - bid) / px) * 10000

                latency = int((time.monotonic() - t0) * 1000)
                return PriceInfo(px=px, spread_bps=spread_bps, cum_value=cum_value, fetch_latency_ms=latency)

        except Exception:
            logger.exception("KIS price fetch failed for %s", ticker)
            return None

    async def get_kospi_index(self) -> Optional[float]:
        """Get current KOSPI change %. Returns None on failure."""
        token = await self._ensure_token()
        if not token:
            return None

        try:
            params = {
                "FID_COND_MRKT_DIV_CODE": "U",
                "FID_INPUT_ISCD": "0001",  # KOSPI
            }
            async with self._session.get(
                f"{self._base}/uapi/domestic-stock/v1/quotations/inquire-index-price",
                headers=self._headers(token, "FHPUP02100000"),
                params=params,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                data = await resp.json()
                output = data.get("output", {})
                return float(output.get("prdy_ctrt", 0))
        except Exception:
            logger.exception("KIS KOSPI fetch failed")
            return None
