"""Context Card: pykrx historical features + KIS realtime features."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from kindshot.kis_client import KisClient
from kindshot.models import ContextCard

logger = logging.getLogger(__name__)


async def _pykrx_features(ticker: str) -> dict:
    """Fetch historical features from pykrx. Runs in thread (blocking I/O)."""

    def _fetch() -> dict:
        try:
            from pykrx import stock

            today = datetime.now().strftime("%Y%m%d")
            start_20d = (datetime.now() - timedelta(days=40)).strftime("%Y%m%d")

            df = stock.get_market_ohlcv(start_20d, today, ticker)
            if df.empty or len(df) < 2:
                return {}

            close = df["종가"]
            volume = df["거래량"]
            value = df["거래대금"]

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

            adv_20d = value.tail(20).mean() if len(value) >= 20 else value.mean()

            # vol_pct_20d: current volume percentile in 20-day window
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

    return await asyncio.to_thread(_fetch)


async def build_context_card(
    ticker: str,
    kis: Optional[KisClient] = None,
) -> tuple[ContextCard, dict]:
    """Build context card for a ticker.

    Returns (ContextCard, raw_data_dict) where raw_data_dict has
    additional fields like prev_close needed by quant check.
    """
    hist = await _pykrx_features(ticker)

    # KIS realtime features (optional)
    spread_bps: Optional[float] = None
    ret_today: Optional[float] = None
    gap: Optional[float] = None

    if kis:
        price_info = await kis.get_price(ticker)
        if price_info:
            spread_bps = price_info.spread_bps
            prev_close = hist.get("prev_close")
            if prev_close and prev_close > 0:
                ret_today = round(((price_info.px / prev_close) - 1) * 100, 2)

    card = ContextCard(
        ret_today=ret_today,
        ret_1d=hist.get("ret_1d"),
        ret_3d=hist.get("ret_3d"),
        pos_20d=hist.get("pos_20d"),
        gap=gap,
        adv_value_20d=hist.get("adv_value_20d"),
        spread_bps=spread_bps,
        vol_pct_20d=hist.get("vol_pct_20d"),
    )

    raw = {**hist, "spread_bps": spread_bps, "ret_today": ret_today}
    return card, raw
