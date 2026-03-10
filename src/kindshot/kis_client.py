"""KIS REST API client. Gracefully returns None when credentials are missing."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

import aiohttp

from kindshot.config import Config

logger = logging.getLogger(__name__)

BASE_URL_REAL = "https://openapi.koreainvestment.com:9443"
BASE_URL_PAPER = "https://openapivts.koreainvestment.com:29443"

# KIS rate limits: production 20 req/s (0.05s), paper 2 req/s (0.5s)
_RATE_LIMIT_REAL = 0.05
_RATE_LIMIT_PAPER = 0.5


@dataclass
class PriceInfo:
    px: float
    open_px: Optional[float]
    spread_bps: Optional[float]
    cum_value: Optional[float]
    fetch_latency_ms: int


class KisClient:
    """KIS REST API client with token management and rate limiting."""

    def __init__(self, config: Config, session: aiohttp.ClientSession) -> None:
        self._config = config
        self._session = session
        self._base = BASE_URL_PAPER if config.kis_is_paper else BASE_URL_REAL
        self._token: Optional[str] = None
        self._token_expires: float = 0.0
        self._last_request: float = 0.0
        self._rate_limit = _RATE_LIMIT_PAPER if config.kis_is_paper else _RATE_LIMIT_REAL

    async def _ensure_token(self) -> Optional[str]:
        if not self._config.kis_app_key or not self._config.kis_app_secret:
            return None
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

    async def _rate_limit_wait(self) -> None:
        """Enforce KIS rate limit between API calls."""
        now = time.monotonic()
        elapsed = now - self._last_request
        if elapsed < self._rate_limit:
            await asyncio.sleep(self._rate_limit - elapsed)
        self._last_request = time.monotonic()

    async def get_price(self, ticker: str) -> Optional[PriceInfo]:
        """Get current price + orderbook spread for a ticker. Returns None on any failure."""
        token = await self._ensure_token()
        if not token:
            return None

        t0 = time.monotonic()
        try:
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker,
            }

            # 1. inquire-price for px, open_px, cum_value
            await self._rate_limit_wait()
            async with self._session.get(
                f"{self._base}/uapi/domestic-stock/v1/quotations/inquire-price",
                headers=self._headers(token, "FHKST01010100"),
                params=params,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                data = await resp.json()
                output = data.get("output")
                if not output or not isinstance(output, dict):
                    logger.warning("KIS empty/invalid output for %s: %s", ticker, data.get("msg_cd", ""))
                    return None

                px = float(output.get("stck_prpr", 0))
                if px <= 0:
                    logger.warning("KIS returned px=0 for %s, treating as UNAVAILABLE", ticker)
                    return None

                open_px_raw = output.get("stck_oprc", 0)
                open_px = float(open_px_raw) if open_px_raw else None
                if open_px is not None and open_px <= 0:
                    open_px = None
                cum_value = float(output.get("acml_tr_pbmn", 0))

            # 2. inquire-asking-price for spread_bps (호가)
            spread_bps = await self._get_spread_bps(token, ticker)

            latency = int((time.monotonic() - t0) * 1000)
            return PriceInfo(
                px=px,
                open_px=open_px,
                spread_bps=spread_bps,
                cum_value=cum_value,
                fetch_latency_ms=latency,
            )

        except Exception:
            logger.exception("KIS price fetch failed for %s", ticker)
            return None

    async def _get_spread_bps(self, token: str, ticker: str) -> Optional[float]:
        """Fetch best ask/bid from 호가 API and compute spread in bps."""
        try:
            await self._rate_limit_wait()
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker,
            }
            async with self._session.get(
                f"{self._base}/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
                headers=self._headers(token, "FHKST01010200"),
                params=params,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                data = await resp.json()
                output1 = data.get("output1")
                if not output1 or not isinstance(output1, dict):
                    logger.debug("KIS orderbook empty for %s", ticker)
                    return None

                askp1 = float(output1.get("askp1", 0))
                bidp1 = float(output1.get("bidp1", 0))
                if askp1 <= 0 or bidp1 <= 0:
                    return None

                mid = (askp1 + bidp1) / 2
                return round((askp1 - bidp1) / mid * 10000, 1)
        except Exception:
            logger.exception("KIS orderbook fetch failed for %s", ticker)
            return None

    async def get_index_change(self, iscd: str = "0001") -> Optional[float]:
        """Get index change % by ISCD. '0001'=KOSPI, '2001'=KOSDAQ. Returns None on failure."""
        token = await self._ensure_token()
        if not token:
            return None

        try:
            await self._rate_limit_wait()
            params = {
                "FID_COND_MRKT_DIV_CODE": "U",
                "FID_INPUT_ISCD": iscd,
            }
            async with self._session.get(
                f"{self._base}/uapi/domestic-stock/v1/quotations/inquire-index-price",
                headers=self._headers(token, "FHPUP02100000"),
                params=params,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                data = await resp.json()
                output = data.get("output", {})
                if not output:
                    logger.warning("KIS index empty output (iscd=%s): msg_cd=%s, msg1=%s",
                                   iscd, data.get("msg_cd", ""), data.get("msg1", ""))
                    return None
                # Index API uses bstp_nmix_prdy_ctrt (not prdy_ctrt)
                raw = output.get("bstp_nmix_prdy_ctrt") or output.get("prdy_ctrt")
                if raw is None or raw == "":
                    logger.warning("KIS index change missing (iscd=%s): msg_cd=%s",
                                   iscd, data.get("msg_cd", ""))
                    return None
                return float(raw)
        except Exception:
            logger.exception("KIS index fetch failed (iscd=%s)", iscd)
            return None

    async def get_news_disclosures(
        self,
        ticker: str = "",
        from_time: str = "",
    ) -> list[dict]:
        """Fetch news/disclosure titles via KIS API (국내주식-141).

        Returns list of dicts with keys: cntt_usiq_srno, data_dt, data_tm,
        hts_pbnt_titl_cntt, iscd1..iscd5, news_ofer_entp_code, dorg.
        """
        token = await self._ensure_token()
        if not token:
            return []

        try:
            await self._rate_limit_wait()
            params = {
                "FID_NEWS_OFER_ENTP_CODE": "",
                "FID_COND_MRKT_CLS_CODE": "",
                "FID_INPUT_ISCD": ticker,
                "FID_TITL_CNTT": "",
                "FID_INPUT_DATE_1": "",
                "FID_INPUT_HOUR_1": from_time,
                "FID_RANK_SORT_CLS_CODE": "",
                "FID_INPUT_SRNO": "",
            }
            async with self._session.get(
                f"{self._base}/uapi/domestic-stock/v1/quotations/news-title",
                headers=self._headers(token, "FHKST01011800"),
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                output = data.get("output", [])
                if not isinstance(output, list):
                    output = [output] if output else []
                return output
        except Exception:
            logger.exception("KIS news disclosure fetch failed")
            return []

    async def get_kospi_index(self) -> Optional[float]:
        """Get current KOSPI change %. Compat wrapper."""
        return await self.get_index_change("0001")
