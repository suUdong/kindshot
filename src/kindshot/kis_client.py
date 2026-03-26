"""KIS REST API client. Gracefully returns None when credentials are missing."""

from __future__ import annotations

import asyncio
from collections import Counter
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

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
    risk_state: "QuoteRiskState" = field(default_factory=lambda: QuoteRiskState())
    orderbook: Optional["OrderbookSnapshot"] = None
    cum_volume: Optional[float] = None
    listed_shares: Optional[float] = None
    volume_turnover_rate: Optional[float] = None
    prior_volume_rate: Optional[float] = None


@dataclass(frozen=True)
class OrderbookSnapshot:
    ask_price1: float
    bid_price1: float
    ask_size1: int
    bid_size1: int
    total_ask_size: int
    total_bid_size: int
    spread_bps: float


@dataclass(frozen=True)
class QuoteRiskState:
    temp_stop_yn: str = ""
    sltr_yn: str = ""
    short_over_yn: str = ""
    vi_cls_code: str = ""
    ovtm_vi_cls_code: str = ""
    invt_caful_yn: str = ""
    mrkt_warn_cls_code: str = ""
    mang_issu_cls_code: str = ""


@dataclass
class IndexInfo:
    iscd: str
    change_pct: float
    fetch_latency_ms: int
    up_issue_count: Optional[int] = None
    down_issue_count: Optional[int] = None
    flat_issue_count: Optional[int] = None


@dataclass(frozen=True)
class IndexDailyInfo:
    iscd: str
    date: str
    close: float
    open_px: float
    high: float
    low: float
    volume: Optional[float]
    value: Optional[float]
    fetch_latency_ms: int


@dataclass(frozen=True)
class NewsDisclosure:
    news_id: str
    data_dt: str
    data_tm: str
    title: str
    dorg: str
    tickers: tuple[str, ...]
    provider_code: str = ""


@dataclass(frozen=True)
class NewsDisclosureFetchResult:
    items: list[NewsDisclosure]
    pagination_truncated: bool = False


@dataclass(frozen=True)
class KisGetSpec:
    path: str
    tr_id: str
    output_key: str
    timeout_s: float = 5.0


@dataclass(frozen=True)
class KisResponse:
    data: dict[str, Any]
    tr_cont: str = ""


class KisClient:
    """KIS REST API client with token management and rate limiting.

    Paper mode dual-server: 실전 서버 credentials가 있으면 시세 조회는 실전 서버,
    주문 실행은 모의투자 서버 사용. VTS는 실시간 시세 미제공(전일 종가 반환) 문제 해결.
    """

    def __init__(self, config: Config, session: aiohttp.ClientSession) -> None:
        self._config = config
        self._session = session
        self._base = BASE_URL_PAPER if config.kis_is_paper else BASE_URL_REAL
        self._token: Optional[str] = None
        self._token_expires: float = 0.0
        self._last_request: float = 0.0
        self._rate_limit = _RATE_LIMIT_PAPER if config.kis_is_paper else _RATE_LIMIT_REAL
        self._request_failures: Counter[str] = Counter()
        self._invalid_payloads: Counter[str] = Counter()

        # Dual-server: 실전 서버 시세 조회용 (paper 모드에서만 활성화)
        self._has_real_market_data = (
            config.kis_is_paper
            and bool(config.kis_real_app_key)
            and bool(config.kis_real_app_secret)
        )
        self._real_base = BASE_URL_REAL
        self._real_token: Optional[str] = None
        self._real_token_expires: float = 0.0
        self._real_last_request: float = 0.0
        self._real_rate_limit = _RATE_LIMIT_REAL
        if self._has_real_market_data:
            logger.info("Dual-server enabled: real server for market data, paper for orders")

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

    async def _market_data_token(self) -> tuple[Optional[str], bool]:
        """시세 조회용 토큰 반환. (token, use_real). 실전 서버 우선, 없으면 기본 서버."""
        if self._has_real_market_data:
            token = await self._ensure_real_token()
            if token:
                return token, True
        token = await self._ensure_token()
        return token, False

    async def _ensure_real_token(self) -> Optional[str]:
        """실전 서버 토큰 발급 (시세 조회용)."""
        if not self._has_real_market_data:
            return None
        if self._real_token and time.time() < self._real_token_expires:
            return self._real_token
        try:
            async with self._session.post(
                f"{self._real_base}/oauth2/tokenP",
                json={
                    "grant_type": "client_credentials",
                    "appkey": self._config.kis_real_app_key,
                    "appsecret": self._config.kis_real_app_secret,
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                self._real_token = data.get("access_token")
                self._real_token_expires = time.time() + 23 * 3600
                logger.info("Real server token acquired for market data")
                return self._real_token
        except Exception:
            logger.exception("KIS real server token fetch failed")
            return None

    def _headers(self, token: str, tr_id: str, *, tr_cont: str = "", use_real: bool = False) -> dict[str, str]:
        app_key = self._config.kis_real_app_key if use_real else self._config.kis_app_key
        app_secret = self._config.kis_real_app_secret if use_real else self._config.kis_app_secret
        headers = {
            "authorization": f"Bearer {token}",
            "appkey": app_key,
            "appsecret": app_secret,
            "tr_id": tr_id,
            "content-type": "application/json; charset=utf-8",
        }
        if tr_cont:
            headers["tr_cont"] = tr_cont
        return headers

    async def _rate_limit_wait(self) -> None:
        """Enforce KIS rate limit between API calls."""
        now = time.monotonic()
        elapsed = now - self._last_request
        if elapsed < self._rate_limit:
            await asyncio.sleep(self._rate_limit - elapsed)
        self._last_request = time.monotonic()

    async def _get_json(
        self,
        token: str,
        spec: KisGetSpec,
        params: dict[str, str],
        *,
        tr_cont: str = "",
        use_real: bool = False,
    ) -> Optional[KisResponse]:
        """Run a rate-limited KIS GET request and return the JSON object body plus response header state."""
        base = self._real_base if use_real else self._base
        try:
            if use_real:
                now = time.monotonic()
                elapsed = now - self._real_last_request
                if elapsed < self._real_rate_limit:
                    await asyncio.sleep(self._real_rate_limit - elapsed)
                self._real_last_request = time.monotonic()
            else:
                await self._rate_limit_wait()
            async with self._session.get(
                f"{base}{spec.path}",
                headers=self._headers(token, spec.tr_id, tr_cont=tr_cont, use_real=use_real),
                params=params,
                timeout=aiohttp.ClientTimeout(total=spec.timeout_s),
            ) as resp:
                data = await resp.json()
                resp_tr_cont = resp.headers.get("tr_cont", "")
        except Exception:
            self._request_failures[spec.tr_id] += 1
            server = "real" if use_real else "paper"
            logger.exception("KIS request failed (tr_id=%s, path=%s, server=%s)", spec.tr_id, spec.path, server)
            return None

        if not isinstance(data, dict):
            self._invalid_payloads[spec.tr_id] += 1
            logger.warning("KIS non-object response (tr_id=%s, path=%s)", spec.tr_id, spec.path)
            return None
        return KisResponse(data=data, tr_cont=resp_tr_cont)

    def _output_dict(
        self,
        data: dict[str, Any],
        spec: KisGetSpec,
        *,
        context: str,
        allow_empty: bool = False,
        log_level: str = "warning",
    ) -> Optional[dict[str, Any]]:
        output = data.get(spec.output_key)
        if isinstance(output, dict) and (output or allow_empty):
            return output

        message = (
            "KIS empty/invalid %s (tr_id=%s, key=%s): msg_cd=%s, msg1=%s"
            % (context, spec.tr_id, spec.output_key, data.get("msg_cd", ""), data.get("msg1", ""))
        )
        if log_level == "debug":
            logger.debug(message)
        else:
            logger.warning(message)
        self._invalid_payloads[spec.tr_id] += 1
        return None

    def _output_list(
        self,
        data: dict[str, Any],
        spec: KisGetSpec,
        *,
        context: str,
    ) -> list[dict[str, Any]]:
        output = data.get(spec.output_key, [])
        if output is None:
            return []
        if isinstance(output, list):
            return [item for item in output if isinstance(item, dict)]
        if isinstance(output, dict):
            return [output]

        logger.warning(
            "KIS empty/invalid %s (tr_id=%s, key=%s): msg_cd=%s, msg1=%s",
            context,
            spec.tr_id,
            spec.output_key,
            data.get("msg_cd", ""),
            data.get("msg1", ""),
        )
        self._invalid_payloads[spec.tr_id] += 1
        return []

    async def get_price(self, ticker: str) -> Optional[PriceInfo]:
        """Get current price + orderbook spread for a ticker. Returns None on any failure.

        Paper mode dual-server: 실전 서버 credentials가 있으면 실전 서버에서 실시간 시세 조회.
        VTS(모의투자) 서버는 전일 종가만 반환하는 문제 회피.
        """
        token, use_real = await self._market_data_token()
        if not token:
            return None

        t0 = time.monotonic()
        price_spec = KisGetSpec(
            path="/uapi/domestic-stock/v1/quotations/inquire-price",
            tr_id="FHKST01010100",
            output_key="output",
        )
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker,
        }

        response = await self._get_json(token, price_spec, params, use_real=use_real)
        if response is None:
            return None

        output = self._output_dict(response.data, price_spec, context=f"price output for {ticker}")
        if output is None:
            return None

        try:
            px = float(output.get("stck_prpr", 0))
            if px <= 0:
                logger.warning("KIS returned px=0 for %s, treating as UNAVAILABLE", ticker)
                return None

            open_px_raw = output.get("stck_oprc", 0)
            open_px = float(open_px_raw) if open_px_raw else None
            if open_px is not None and open_px <= 0:
                open_px = None
            cum_value = float(output.get("acml_tr_pbmn", 0))
            cum_volume = float(output.get("acml_vol", 0))
            listed_shares_raw = output.get("lstn_stcn", 0)
            listed_shares = float(listed_shares_raw) if listed_shares_raw not in ("", None) else None
            if listed_shares is not None and listed_shares <= 0:
                listed_shares = None
            volume_turnover_rate_raw = output.get("vol_tnrt", "")
            volume_turnover_rate = (
                float(volume_turnover_rate_raw)
                if volume_turnover_rate_raw not in ("", None)
                else None
            )
            prior_volume_rate_raw = output.get("prdy_vrss_vol_rate", "")
            prior_volume_rate = (
                float(prior_volume_rate_raw)
                if prior_volume_rate_raw not in ("", None)
                else None
            )
            risk_state = QuoteRiskState(
                temp_stop_yn=str(output.get("temp_stop_yn", "")).strip(),
                sltr_yn=str(output.get("sltr_yn", "")).strip(),
                short_over_yn=str(output.get("short_over_yn", "")).strip(),
                vi_cls_code=str(output.get("vi_cls_code", "")).strip(),
                ovtm_vi_cls_code=str(output.get("ovtm_vi_cls_code", "")).strip(),
                invt_caful_yn=str(output.get("invt_caful_yn", "")).strip(),
                mrkt_warn_cls_code=str(output.get("mrkt_warn_cls_code", "")).strip(),
                mang_issu_cls_code=str(output.get("mang_issu_cls_code", "")).strip(),
            )
        except (TypeError, ValueError):
            logger.warning("KIS invalid numeric fields in price output for %s", ticker)
            return None

        orderbook = await self._get_orderbook_snapshot(token, ticker)
        spread_bps = orderbook.spread_bps if orderbook is not None else None
        latency = int((time.monotonic() - t0) * 1000)
        return PriceInfo(
            px=px,
            open_px=open_px,
            spread_bps=spread_bps,
            cum_value=cum_value,
            fetch_latency_ms=latency,
            risk_state=risk_state,
            orderbook=orderbook,
            cum_volume=cum_volume,
            listed_shares=listed_shares,
            volume_turnover_rate=volume_turnover_rate,
            prior_volume_rate=prior_volume_rate,
        )

    async def _get_orderbook_snapshot(self, token: str, ticker: str) -> Optional[OrderbookSnapshot]:
        """Fetch level-1/aggregate orderbook values needed for liquidity checks."""
        spec = KisGetSpec(
            path="/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
            tr_id="FHKST01010200",
            output_key="output1",
        )
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker,
        }
        response = await self._get_json(token, spec, params)
        if response is None:
            return None

        output1 = self._output_dict(
            response.data,
            spec,
            context=f"orderbook output for {ticker}",
            log_level="debug",
        )
        if output1 is None:
            return None

        try:
            askp1 = float(output1.get("askp1", 0))
            bidp1 = float(output1.get("bidp1", 0))
            ask_size1 = int(float(output1.get("askp_rsqn1", 0)))
            bid_size1 = int(float(output1.get("bidp_rsqn1", 0)))
            total_ask_size = int(float(output1.get("total_askp_rsqn", 0)))
            total_bid_size = int(float(output1.get("total_bidp_rsqn", 0)))
        except (TypeError, ValueError):
            logger.debug("KIS invalid orderbook fields for %s", ticker)
            return None
        if askp1 <= 0 or bidp1 <= 0:
            return None

        mid = (askp1 + bidp1) / 2
        return OrderbookSnapshot(
            ask_price1=askp1,
            bid_price1=bidp1,
            ask_size1=max(0, ask_size1),
            bid_size1=max(0, bid_size1),
            total_ask_size=max(0, total_ask_size),
            total_bid_size=max(0, total_bid_size),
            spread_bps=round((askp1 - bidp1) / mid * 10000, 1),
        )

    async def get_index_change(self, iscd: str = "0001") -> Optional[float]:
        """Get index change % by ISCD. '0001'=KOSPI, '2001'=KOSDAQ. Returns None on failure."""
        info = await self.get_index_info(iscd)
        if info is None:
            return None
        return info.change_pct

    async def get_index_info(self, iscd: str = "0001") -> Optional[IndexInfo]:
        """Get normalized index info by ISCD. '0001'=KOSPI, '2001'=KOSDAQ."""
        token, use_real = await self._market_data_token()
        if not token:
            return None

        t0 = time.monotonic()
        spec = KisGetSpec(
            path="/uapi/domestic-stock/v1/quotations/inquire-index-price",
            tr_id="FHPUP02100000",
            output_key="output",
        )
        params = {
            "FID_COND_MRKT_DIV_CODE": "U",
            "FID_INPUT_ISCD": iscd,
        }
        response = await self._get_json(token, spec, params, use_real=use_real)
        if response is None:
            return None

        output = self._output_dict(response.data, spec, context=f"index output for {iscd}")
        if output is None:
            return None

        raw = output.get("bstp_nmix_prdy_ctrt") or output.get("prdy_ctrt")
        if raw is None or raw == "":
            logger.warning("KIS index change missing (iscd=%s): msg_cd=%s", iscd, response.data.get("msg_cd", ""))
            return None
        try:
            change_pct = float(raw)
            up_issue_count = int(output["ascn_issu_cnt"]) if output.get("ascn_issu_cnt") not in ("", None) else None
            down_issue_count = int(output["down_issu_cnt"]) if output.get("down_issu_cnt") not in ("", None) else None
            flat_issue_count = int(output["stnr_issu_cnt"]) if output.get("stnr_issu_cnt") not in ("", None) else None
        except (TypeError, ValueError):
            logger.warning("KIS invalid index breadth/change value (iscd=%s): %r", iscd, raw)
            return None
        latency = int((time.monotonic() - t0) * 1000)
        return IndexInfo(
            iscd=iscd,
            change_pct=change_pct,
            fetch_latency_ms=latency,
            up_issue_count=up_issue_count,
            down_issue_count=down_issue_count,
            flat_issue_count=flat_issue_count,
        )

    async def get_index_daily_info(self, iscd: str, date: str) -> Optional[IndexDailyInfo]:
        """Get historical daily index OHLCV for an exact business date via KIS."""
        token, use_real = await self._market_data_token()
        if not token:
            return None

        t0 = time.monotonic()
        spec = KisGetSpec(
            path="/uapi/domestic-stock/v1/quotations/inquire-index-daily-price",
            tr_id="FHPUP02120000",
            output_key="output2",
        )
        params = {
            "FID_PERIOD_DIV_CODE": "D",
            "FID_COND_MRKT_DIV_CODE": "U",
            "FID_INPUT_ISCD": iscd,
            "FID_INPUT_DATE_1": date,
        }
        response = await self._get_json(token, spec, params, use_real=use_real)
        if response is None:
            return None

        rows = self._output_list(response.data, spec, context=f"index daily output for {iscd}:{date}")
        if not rows:
            return None

        exact_row = next(
            (row for row in rows if str(row.get("stck_bsop_date", "")).strip() == date),
            None,
        )
        if exact_row is None:
            logger.debug(
                "KIS index daily exact date not found (iscd=%s date=%s rows=%d msg_cd=%s)",
                iscd,
                date,
                len(rows),
                response.data.get("msg_cd", ""),
            )
            return None

        try:
            close = float(exact_row["bstp_nmix_prpr"])
            open_px = float(exact_row["bstp_nmix_oprc"])
            high = float(exact_row["bstp_nmix_hgpr"])
            low = float(exact_row["bstp_nmix_lwpr"])
            volume = (
                float(exact_row["acml_vol"])
                if exact_row.get("acml_vol") not in ("", None)
                else None
            )
            value = (
                float(exact_row["acml_tr_pbmn"])
                if exact_row.get("acml_tr_pbmn") not in ("", None)
                else None
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("KIS invalid index daily fields (iscd=%s date=%s)", iscd, date)
            return None

        return IndexDailyInfo(
            iscd=iscd,
            date=date,
            close=close,
            open_px=open_px,
            high=high,
            low=low,
            volume=volume,
            value=value,
            fetch_latency_ms=int((time.monotonic() - t0) * 1000),
        )

    async def get_news_disclosures(
        self,
        ticker: str = "",
        from_time: str = "",
        date: str = "",
    ) -> list[dict]:
        items = await self.get_news_disclosure_items(ticker=ticker, from_time=from_time, date=date)
        rows: list[dict[str, str]] = []
        for item in items:
            row: dict[str, str] = {"cntt_usiq_srno": item.news_id}
            if item.data_dt:
                row["data_dt"] = item.data_dt
            if item.data_tm:
                row["data_tm"] = item.data_tm
            if item.title:
                row["hts_pbnt_titl_cntt"] = item.title
            if item.dorg:
                row["dorg"] = item.dorg
            if item.provider_code:
                row["news_ofer_entp_code"] = item.provider_code
            for i, ticker_value in enumerate(item.tickers, start=1):
                row[f"iscd{i}"] = ticker_value
            rows.append(row)
        return rows

    async def get_news_disclosure_items(
        self,
        ticker: str = "",
        from_time: str = "",
        date: str = "",
    ) -> list[NewsDisclosure]:
        result = await self.get_news_disclosure_fetch_result(
            ticker=ticker,
            from_time=from_time,
            date=date,
        )
        return result.items

    async def get_news_disclosure_fetch_result(
        self,
        ticker: str = "",
        from_time: str = "",
        date: str = "",
    ) -> NewsDisclosureFetchResult:
        """Fetch news/disclosure titles via KIS API (국내주식-141).

        Returns list of dicts with keys: cntt_usiq_srno, data_dt, data_tm,
        hts_pbnt_titl_cntt, iscd1..iscd5, news_ofer_entp_code, dorg.
        """
        token = await self._ensure_token()
        if not token:
            return NewsDisclosureFetchResult(items=[])

        spec = KisGetSpec(
            path="/uapi/domestic-stock/v1/quotations/news-title",
            tr_id="FHKST01011800",
            output_key="output",
            timeout_s=30,
        )
        params = {
            "FID_NEWS_OFER_ENTP_CODE": "",
            "FID_COND_MRKT_CLS_CODE": "",
            "FID_INPUT_ISCD": ticker,
            "FID_TITL_CNTT": "",
            "FID_INPUT_DATE_1": date,
            "FID_INPUT_HOUR_1": from_time,
            "FID_RANK_SORT_CLS_CODE": "",
            "FID_INPUT_SRNO": "",
        }
        items: list[dict[str, Any]] = []
        request_tr_cont = ""

        for _ in range(10):
            response = await self._get_json(token, spec, params, tr_cont=request_tr_cont)
            if response is None:
                return NewsDisclosureFetchResult(items=self._normalize_news_items(items))

            items.extend(self._output_list(response.data, spec, context="news disclosure output"))
            if response.tr_cont != "M":
                return NewsDisclosureFetchResult(items=self._normalize_news_items(items))
            request_tr_cont = "N"

        logger.warning("KIS news disclosure pagination stopped at max pages")
        return NewsDisclosureFetchResult(
            items=self._normalize_news_items(items),
            pagination_truncated=True,
        )

    def _normalize_news_items(self, items: list[dict[str, Any]]) -> list[NewsDisclosure]:
        normalized: list[NewsDisclosure] = []
        for item in items:
            news_id = str(item.get("cntt_usiq_srno", "")).strip()
            if not news_id:
                continue
            tickers = tuple(
                ticker
                for ticker in (
                    str(item.get(f"iscd{i}", "")).strip()
                    for i in range(1, 6)
                )
                if len(ticker) == 6 and ticker.isdigit()
            )
            normalized.append(
                NewsDisclosure(
                    news_id=news_id,
                    data_dt=str(item.get("data_dt", "")),
                    data_tm=str(item.get("data_tm", "")),
                    title=str(item.get("hts_pbnt_titl_cntt", "")),
                    dorg=str(item.get("dorg", "")),
                    tickers=tickers,
                    provider_code=str(item.get("news_ofer_entp_code", "")),
                )
            )
        return normalized

    async def get_kospi_index(self) -> Optional[float]:
        """Get current KOSPI change %. Compat wrapper."""
        return await self.get_index_change("0001")

    def stats_snapshot(self) -> dict[str, dict[str, int]]:
        return {
            "request_failures": dict(self._request_failures),
            "invalid_payloads": dict(self._invalid_payloads),
        }
