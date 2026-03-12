"""Context Card: pykrx historical features + KIS realtime features."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from kindshot.config import Config
from kindshot.kis_client import KisClient
from kindshot.models import ContextCard

logger = logging.getLogger(__name__)

_PYKRX_CACHE_TTL = 300  # 5 minutes
_PYKRX_CACHE_MAX_SIZE = 512
_pykrx_cache: OrderedDict[str, tuple[dict, float]] = OrderedDict()  # ticker -> (data, expire_time)


@dataclass(frozen=True)
class ContextCardData:
    adv_value_20d: Optional[float] = None
    spread_bps: Optional[float] = None
    ret_today: Optional[float] = None
    gap: Optional[float] = None
    prev_close: Optional[float] = None
    cum_volume: Optional[float] = None
    listed_shares: Optional[float] = None
    volume_turnover_rate: Optional[float] = None
    prior_volume_rate: Optional[float] = None
    intraday_value_vs_adv20d: Optional[float] = None
    quote_risk_state: object = None
    orderbook_snapshot: object = None
    sector: str = ""


def configure_cache(ttl_s: int, max_size: int) -> None:
    """Set cache policy from Config at runtime."""
    global _PYKRX_CACHE_TTL, _PYKRX_CACHE_MAX_SIZE
    _PYKRX_CACHE_TTL = max(1, int(ttl_s))
    _PYKRX_CACHE_MAX_SIZE = max(1, int(max_size))


def _prune_cache(now: float, max_size: int) -> None:
    expired = [ticker for ticker, (_data, exp) in _pykrx_cache.items() if exp <= now]
    for ticker in expired:
        _pykrx_cache.pop(ticker, None)

    while len(_pykrx_cache) > max_size:
        _pykrx_cache.popitem(last=False)


async def _pykrx_features(ticker: str) -> dict:
    """Fetch historical features from pykrx. Runs in thread (blocking I/O)."""

    def _fetch() -> dict:
        try:
            from pykrx import stock

            _KST = timezone(timedelta(hours=9))
            today = datetime.now(_KST).strftime("%Y%m%d")
            start_20d = (datetime.now(_KST) - timedelta(days=40)).strftime("%Y%m%d")

            df = stock.get_market_ohlcv(start_20d, today, ticker)
            if df.empty or len(df) < 2:
                return {}

            # pykrx column names vary; try Korean then English
            close_col = "종가" if "종가" in df.columns else "Close" if "Close" in df.columns else None
            vol_col = "거래량" if "거래량" in df.columns else "Volume" if "Volume" in df.columns else None
            val_col = "거래대금" if "거래대금" in df.columns else "Value" if "Value" in df.columns else None

            if not close_col:
                logger.warning("pykrx unexpected columns for %s: %s", ticker, list(df.columns))
                return {}

            close = df[close_col]
            prev_close = close.iloc[-2] if len(close) >= 2 else None
            cur_close = close.iloc[-1]
            close_3d = close.iloc[-4] if len(close) >= 4 else None

            ret_1d = ((cur_close / close.iloc[-2]) - 1) * 100 if len(close) >= 2 else None
            ret_3d = ((cur_close / close_3d) - 1) * 100 if close_3d else None

            last_20 = close.tail(20)
            if len(last_20) >= 2:
                low_20 = last_20.min()
                high_20 = last_20.max()
                rng = high_20 - low_20
                pos_20d = ((cur_close - low_20) / rng * 100) if rng > 0 else 50.0
            else:
                pos_20d = None

            adv_20d = None
            if val_col:
                value = df[val_col]
                adv_20d = value.tail(20).mean() if len(value) >= 20 else value.mean()
            elif vol_col and close_col:
                # 거래대금 컬럼이 없으면 종가 × 거래량으로 근사
                approx_value = df[close_col] * df[vol_col]
                adv_20d = approx_value.tail(20).mean() if len(approx_value) >= 20 else approx_value.mean()

            vol_pct = None
            if vol_col:
                volume = df[vol_col]
                vol_20 = volume.tail(20)
                cur_vol = volume.iloc[-1]
                vol_pct = (vol_20 < cur_vol).sum() / len(vol_20) * 100 if len(vol_20) > 0 else None

            return {
                "ret_1d": round(ret_1d, 2) if ret_1d is not None else None,
                "ret_3d": round(ret_3d, 2) if ret_3d is not None else None,
                "pos_20d": round(pos_20d, 1) if pos_20d is not None else None,
                "adv_value_20d": round(adv_20d) if adv_20d is not None else None,
                "vol_pct_20d": round(vol_pct, 1) if vol_pct is not None else None,
                "prev_close": prev_close,
            }
        except Exception:
            logger.exception("pykrx fetch failed for %s", ticker)
            return {}

    ttl_s = _PYKRX_CACHE_TTL
    max_size = _PYKRX_CACHE_MAX_SIZE
    now = time.monotonic()
    _prune_cache(now, max_size)

    cached = _pykrx_cache.get(ticker)
    if cached and cached[1] > now:
        _pykrx_cache.move_to_end(ticker)
        return cached[0]

    result = await asyncio.to_thread(_fetch)
    expire_at = time.monotonic() + ttl_s
    _pykrx_cache[ticker] = (result, expire_at)
    _pykrx_cache.move_to_end(ticker)
    _prune_cache(time.monotonic(), max_size)
    return result


async def build_context_card(
    ticker: str,
    kis: Optional[KisClient] = None,
    config: Optional[Config] = None,
) -> tuple[ContextCard, ContextCardData]:
    """Build context card for a ticker.

    Returns (ContextCard, ContextCardData) with additional normalized
    fields needed by quant and guardrail checks.
    """
    if config is not None:
        configure_cache(config.pykrx_cache_ttl_s, config.pykrx_cache_max_size)
    hist = await _pykrx_features(ticker)

    # KIS realtime features (optional)
    spread_bps: Optional[float] = None
    ret_today: Optional[float] = None
    gap: Optional[float] = None
    intraday_value_vs_adv20d: Optional[float] = None
    top_ask_notional: Optional[float] = None
    quote_temp_stop: Optional[bool] = None
    quote_liquidation_trade: Optional[bool] = None

    if kis:
        price_info = await kis.get_price(ticker)
        if price_info:
            spread_bps = price_info.spread_bps
            prev_close = hist.get("prev_close")
            if prev_close and prev_close > 0:
                ret_today = round(((price_info.px / prev_close) - 1) * 100, 2)
                if price_info.open_px and price_info.open_px > 0:
                    gap = round(((price_info.open_px / prev_close) - 1) * 100, 2)
            adv_value_20d = hist.get("adv_value_20d")
            if adv_value_20d and adv_value_20d > 0 and price_info.cum_value is not None:
                intraday_value_vs_adv20d = round(price_info.cum_value / adv_value_20d, 4)
            if price_info.orderbook is not None:
                top_ask_notional = round(price_info.orderbook.ask_price1 * price_info.orderbook.ask_size1, 2)
            quote_temp_stop = price_info.risk_state.temp_stop_yn == "Y"
            quote_liquidation_trade = price_info.risk_state.sltr_yn == "Y"

    card = ContextCard(
        ret_today=ret_today,
        ret_1d=hist.get("ret_1d"),
        ret_3d=hist.get("ret_3d"),
        pos_20d=hist.get("pos_20d"),
        gap=gap,
        adv_value_20d=hist.get("adv_value_20d"),
        spread_bps=spread_bps,
        vol_pct_20d=hist.get("vol_pct_20d"),
        intraday_value_vs_adv20d=intraday_value_vs_adv20d,
        top_ask_notional=top_ask_notional,
        quote_temp_stop=quote_temp_stop,
        quote_liquidation_trade=quote_liquidation_trade,
    )

    raw = ContextCardData(
        adv_value_20d=hist.get("adv_value_20d"),
        spread_bps=spread_bps,
        ret_today=ret_today,
        gap=gap,
        prev_close=hist.get("prev_close"),
        cum_volume=price_info.cum_volume if kis and price_info else None,
        listed_shares=price_info.listed_shares if kis and price_info else None,
        volume_turnover_rate=price_info.volume_turnover_rate if kis and price_info else None,
        prior_volume_rate=price_info.prior_volume_rate if kis and price_info else None,
        intraday_value_vs_adv20d=intraday_value_vs_adv20d,
        quote_risk_state=price_info.risk_state if kis and price_info else None,
        orderbook_snapshot=price_info.orderbook if kis and price_info else None,
    )
    return card, raw
