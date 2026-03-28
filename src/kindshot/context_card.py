"""Context Card: pykrx historical features + KIS realtime features."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from dataclasses import asdict, is_dataclass
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import aiohttp

from kindshot.alpha_scanner import (
    fetch_alpha_scanner_sector_snapshot,
    lookup_sector_snapshot_ticker,
)
from kindshot.config import Config
from kindshot.kis_client import KisClient
from kindshot.models import AlphaSignalContext, ContextCard, SectorMomentumContext
from kindshot.runtime_artifacts import update_runtime_artifact_index
from kindshot.tz import KST as _KST

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
    orderbook_bid_ask_ratio: Optional[float] = None
    quote_risk_state: object = None
    orderbook_snapshot: object = None
    sector: str = ""
    support_price_5d: Optional[float] = None
    support_price_20d: Optional[float] = None
    support_reference_px: Optional[float] = None
    avg_volume_20d: Optional[float] = None
    volume_ratio_vs_avg20d: Optional[float] = None
    alpha_signal: dict | None = None
    sector_momentum: dict | None = None

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

            today = datetime.now(_KST).strftime("%Y%m%d")
            start_20d = (datetime.now(_KST) - timedelta(days=70)).strftime("%Y%m%d")

            df = stock.get_market_ohlcv(start_20d, today, ticker)
            if df.empty or len(df) < 2:
                return {}

            # pykrx column names vary; try Korean then English
            close_col = "종가" if "종가" in df.columns else "Close" if "Close" in df.columns else None
            vol_col = "거래량" if "거래량" in df.columns else "Volume" if "Volume" in df.columns else None
            val_col = "거래대금" if "거래대금" in df.columns else "Value" if "Value" in df.columns else None
            low_col = "저가" if "저가" in df.columns else "Low" if "Low" in df.columns else None
            high_col = "고가" if "고가" in df.columns else "High" if "High" in df.columns else None

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

            support_price_5d = None
            support_price_20d = None
            support_reference_px = None
            if low_col and len(df) >= 2:
                completed_lows = df[low_col].iloc[:-1]
                if len(completed_lows) >= 1:
                    recent_window = completed_lows.tail(min(5, len(completed_lows)))
                    support_price_5d = float(recent_window.min())
                    medium_window = completed_lows.tail(min(20, len(completed_lows)))
                    if len(medium_window) > len(recent_window):
                        medium_window = medium_window.iloc[:-len(recent_window)]
                    support_price_20d = float(medium_window.min()) if len(medium_window) > 0 else None
                    support_candidates = [value for value in (support_price_5d, support_price_20d) if value and value > 0]
                    if support_candidates:
                        # Use the stronger available floor so noise does not trigger exits too early.
                        support_reference_px = max(support_candidates)

            # RSI-14 — 15거래일 이상 필요
            rsi_14 = None
            if len(close) < 15:
                logger.debug("RSI skipped for %s: only %d rows (need 15)", ticker, len(close))
            if len(close) >= 15:
                delta = close.diff()
                gain = delta.clip(lower=0)
                loss = (-delta.clip(upper=0))
                avg_gain = gain.rolling(14, min_periods=14).mean()
                avg_loss = loss.rolling(14, min_periods=14).mean()
                last_avg_gain = avg_gain.iloc[-1]
                last_avg_loss = avg_loss.iloc[-1]
                if last_avg_loss > 0:
                    rs = last_avg_gain / last_avg_loss
                    rsi_14 = round(100 - (100 / (1 + rs)), 1)
                elif last_avg_gain > 0:
                    rsi_14 = 100.0

            # MACD histogram (12/26/9) — 26거래일 이상 필요
            macd_hist = None
            if len(close) < 26:
                logger.debug("MACD skipped for %s: only %d rows (need 26)", ticker, len(close))
            if len(close) >= 26:
                ema12 = close.ewm(span=12, adjust=False).mean()
                ema26 = close.ewm(span=26, adjust=False).mean()
                macd_line = ema12 - ema26
                signal = macd_line.ewm(span=9, adjust=False).mean()
                macd_hist = round(float(macd_line.iloc[-1] - signal.iloc[-1]), 2)

            # Bollinger Bands (20일, 2σ) — 현재가의 밴드 내 위치 (0=하단, 100=상단)
            bb_position = None
            if len(close) >= 20:
                sma20 = close.rolling(20).mean()
                std20 = close.rolling(20).std()
                upper = sma20 + 2 * std20
                lower = sma20 - 2 * std20
                band_width = upper.iloc[-1] - lower.iloc[-1]
                if band_width > 0:
                    bb_position = round(
                        float((cur_close - lower.iloc[-1]) / band_width * 100), 1
                    )

            # ATR-14 (Average True Range) — 변동성 지표
            atr_14 = None
            if high_col and low_col and len(df) >= 15:
                high = df[high_col]
                low = df[low_col]
                tr1 = high - low
                tr2 = (high - close.shift(1)).abs()
                tr3 = (low - close.shift(1)).abs()
                import pandas as pd
                tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
                atr_series = tr.rolling(14, min_periods=14).mean()
                if not atr_series.isna().iloc[-1]:
                    # ATR을 현재가 대비 %로 표현 (비교 가능성)
                    atr_14 = round(float(atr_series.iloc[-1] / cur_close * 100), 2)

            # 20일 평균 거래량 (volume ratio 계산용)
            avg_volume_20d = None
            if vol_col:
                volume = df[vol_col]
                vol_tail_20 = volume.tail(20)
                if len(vol_tail_20) >= 5:
                    avg_volume_20d = float(vol_tail_20.mean())

            return {
                "ret_1d": round(ret_1d, 2) if ret_1d is not None else None,
                "ret_3d": round(ret_3d, 2) if ret_3d is not None else None,
                "pos_20d": round(pos_20d, 1) if pos_20d is not None else None,
                "adv_value_20d": round(adv_20d) if adv_20d is not None else None,
                "vol_pct_20d": round(vol_pct, 1) if vol_pct is not None else None,
                "avg_volume_20d": round(avg_volume_20d) if avg_volume_20d is not None else None,
                "prev_close": prev_close,
                "rsi_14": rsi_14,
                "macd_hist": macd_hist,
                "bb_position": bb_position,
                "atr_14": atr_14,
                "support_price_5d": round(support_price_5d, 2) if support_price_5d is not None else None,
                "support_price_20d": round(support_price_20d, 2) if support_price_20d is not None else None,
                "support_reference_px": round(support_reference_px, 2) if support_reference_px is not None else None,
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


async def _fetch_alpha_scanner_signal(
    base_url: str,
    timeout_s: float,
    ticker: str,
) -> AlphaSignalContext | None:
    """Fetch a fresh STRONG_BUY signal from alpha-scanner."""
    if not base_url:
        return None

    url = f"{base_url.rstrip('/')}/kindshot/signals/current"
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, params={"ticker": ticker}) as response:
            response.raise_for_status()
            payload = await response.json()

    if payload.get("status") != "ok" or not payload.get("has_signal"):
        return None

    return AlphaSignalContext(
        ticker=payload.get("ticker") or ticker,
        signal_type=payload.get("signal_type") or "",
        score_current=payload.get("score_current"),
        confidence=payload.get("confidence"),
        size_hint=payload.get("size_hint"),
        score_delta=payload.get("score_delta"),
        regime=payload.get("regime"),
        created_at=payload.get("created_at"),
        age_hours=payload.get("age_hours"),
    )


async def build_context_card(
    ticker: str,
    kis: Optional[KisClient] = None,
    config: Optional[Config] = None,
    sector_snapshot_prefetched: dict | None = None,
) -> tuple[ContextCard, ContextCardData]:
    """Build context card for a ticker.

    Returns (ContextCard, ContextCardData) with additional normalized
    fields needed by quant and guardrail checks.

    Args:
        sector_snapshot_prefetched: pipeline_loop에서 이미 fetch한 sector snapshot.
            전달 시 중복 HTTP 호출 제거 (v77 레이턴시 최적화).
    """
    if config is not None:
        configure_cache(config.pykrx_cache_ttl_s, config.pykrx_cache_max_size)
    hist_task = asyncio.create_task(_pykrx_features(ticker))
    price_task = asyncio.create_task(kis.get_price(ticker)) if kis is not None else None
    alpha_task = None
    sector_task = None
    if config is not None and config.alpha_scanner_api_base_url:
        alpha_task = asyncio.create_task(
            _fetch_alpha_scanner_signal(
                config.alpha_scanner_api_base_url,
                config.alpha_scanner_api_timeout_s,
                ticker,
            )
        )
        # sector_snapshot가 이미 있으면 중복 fetch 생략
        if sector_snapshot_prefetched is None:
            sector_task = asyncio.create_task(
                fetch_alpha_scanner_sector_snapshot(
                    config.alpha_scanner_api_base_url,
                    config.alpha_scanner_api_timeout_s,
                )
            )

    pending_tasks = [hist_task]
    if price_task is not None:
        pending_tasks.append(price_task)
    if alpha_task is not None:
        pending_tasks.append(alpha_task)
    if sector_task is not None:
        pending_tasks.append(sector_task)
    results = await asyncio.gather(*pending_tasks, return_exceptions=True)
    hist_result = results[0]
    if isinstance(hist_result, Exception):
        raise hist_result
    hist = hist_result

    # KIS realtime features (optional)
    spread_bps: Optional[float] = None
    ret_today: Optional[float] = None
    gap: Optional[float] = None
    intraday_value_vs_adv20d: Optional[float] = None
    top_ask_notional: Optional[float] = None
    orderbook_bid_ask_ratio: Optional[float] = None
    quote_temp_stop: Optional[bool] = None
    quote_liquidation_trade: Optional[bool] = None
    alpha_signal: AlphaSignalContext | None = None
    sector_momentum: SectorMomentumContext | None = None
    result_idx = 1
    price_info = None
    if price_task is not None:
        price_result = results[result_idx]
        result_idx += 1
        if isinstance(price_result, Exception):
            raise price_result
        price_info = price_result

    if kis:
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
                if price_info.orderbook.total_ask_size > 0:
                    orderbook_bid_ask_ratio = round(
                        price_info.orderbook.total_bid_size / price_info.orderbook.total_ask_size,
                        4,
                    )
            quote_temp_stop = price_info.risk_state.temp_stop_yn == "Y"
            quote_liquidation_trade = price_info.risk_state.sltr_yn == "Y"

    if alpha_task is not None:
        alpha_result = results[result_idx]
        if isinstance(alpha_result, Exception):
            logger.warning("Alpha-scanner signal fetch failed for %s: %s", ticker, alpha_result)
        else:
            alpha_signal = alpha_result
        result_idx += 1

    if sector_task is not None:
        sector_result = results[result_idx]
        if isinstance(sector_result, Exception):
            logger.warning("Alpha-scanner sector snapshot fetch failed for %s: %s", ticker, sector_result)
        else:
            sector_row = lookup_sector_snapshot_ticker(sector_result, ticker)
            if sector_row is not None:
                rotation_signal = sector_row.get("sector_rotation_signal")
                sector_momentum = SectorMomentumContext(
                    ticker=ticker,
                    sector=sector_row.get("sector"),
                    sector_rotation_signal=rotation_signal,
                    sector_momentum_score=sector_row.get("sector_momentum_score"),
                    sector_rank=sector_row.get("sector_rank"),
                    sector_score_adjustment=sector_row.get("sector_score_adjustment"),
                    priority_score=sector_row.get("priority_score"),
                    generated_at=sector_result.get("generated_at"),
                    is_rising=rotation_signal in {"LEADING", "IMPROVING"},
                )
    elif sector_snapshot_prefetched is not None:
        # pipeline_loop에서 이미 fetch된 sector_snapshot 재사용
        sector_row = lookup_sector_snapshot_ticker(sector_snapshot_prefetched, ticker)
        if sector_row is not None:
            rotation_signal = sector_row.get("sector_rotation_signal")
            sector_momentum = SectorMomentumContext(
                ticker=ticker,
                sector=sector_row.get("sector"),
                sector_rotation_signal=rotation_signal,
                sector_momentum_score=sector_row.get("sector_momentum_score"),
                sector_rank=sector_row.get("sector_rank"),
                sector_score_adjustment=sector_row.get("sector_score_adjustment"),
                priority_score=sector_row.get("priority_score"),
                generated_at=sector_snapshot_prefetched.get("generated_at"),
                is_rising=rotation_signal in {"LEADING", "IMPROVING"},
            )

    # 당일 누적거래량 / 20일 평균거래량 비율
    volume_ratio_vs_avg20d: Optional[float] = None
    avg_volume_20d = hist.get("avg_volume_20d")
    if (
        kis
        and price_info
        and price_info.cum_volume is not None
        and price_info.cum_volume > 0
        and avg_volume_20d
        and avg_volume_20d > 0
    ):
        volume_ratio_vs_avg20d = round(price_info.cum_volume / avg_volume_20d, 4)

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
        volume_ratio_vs_avg20d=volume_ratio_vs_avg20d,
        top_ask_notional=top_ask_notional,
        orderbook_bid_ask_ratio=orderbook_bid_ask_ratio,
        quote_temp_stop=quote_temp_stop,
        quote_liquidation_trade=quote_liquidation_trade,
        prior_volume_rate=price_info.prior_volume_rate if kis and price_info else None,
        rsi_14=hist.get("rsi_14"),
        macd_hist=hist.get("macd_hist"),
        bb_position=hist.get("bb_position"),
        atr_14=hist.get("atr_14"),
        support_price_5d=hist.get("support_price_5d"),
        support_price_20d=hist.get("support_price_20d"),
        support_reference_px=hist.get("support_reference_px"),
        alpha_signal=alpha_signal,
        sector_momentum=sector_momentum,
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
        orderbook_bid_ask_ratio=orderbook_bid_ask_ratio,
        quote_risk_state=price_info.risk_state if kis and price_info else None,
        orderbook_snapshot=price_info.orderbook if kis and price_info else None,
        sector=price_info.sector if kis and price_info else (sector_momentum.sector if sector_momentum else ""),
        avg_volume_20d=avg_volume_20d,
        volume_ratio_vs_avg20d=volume_ratio_vs_avg20d,
        support_price_5d=hist.get("support_price_5d"),
        support_price_20d=hist.get("support_price_20d"),
        support_reference_px=hist.get("support_reference_px"),
        alpha_signal=alpha_signal.model_dump(mode="json") if alpha_signal is not None else None,
        sector_momentum=sector_momentum.model_dump(mode="json") if sector_momentum is not None else None,
    )
    return card, raw


def _runtime_context_card_path(config: Config, ts: datetime) -> Path:
    dt = ts.astimezone(_KST).strftime("%Y%m%d")
    return config.runtime_context_cards_dir / f"{dt}.jsonl"


def _json_safe_value(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_value(item) for item in value]
    if is_dataclass(value):
        return _json_safe_value(asdict(value))
    if hasattr(value, "model_dump"):
        return _json_safe_value(value.model_dump(mode="json"))
    if hasattr(value, "item") and callable(value.item):
        try:
            return _json_safe_value(value.item())
        except (TypeError, ValueError):
            pass
    return value


async def append_runtime_context_card(
    config: Config,
    *,
    run_id: str,
    mode: str,
    event_id: str,
    event_kind: str,
    ticker: str,
    corp_name: str,
    headline: str,
    bucket: str,
    detected_at: datetime,
    disclosed_at: Optional[datetime],
    delay_ms: Optional[int],
    quant_check_passed: Optional[bool],
    skip_stage: Optional[str],
    skip_reason: Optional[str],
    promotion_original_event_id: Optional[str],
    promotion_original_bucket: Optional[str],
    promotion_confidence: Optional[int],
    promotion_policy: Optional[str],
    news_signal: object = None,
    ctx: ContextCard,
    raw: ContextCardData,
    market_ctx: object,
    keyword_hits: Optional[list[str]] = None,
) -> None:
    record = {
        "type": "context_card",
        "run_id": run_id,
        "mode": mode,
        "event_id": event_id,
        "event_kind": event_kind,
        "ticker": ticker,
        "corp_name": corp_name,
        "headline": headline,
        "bucket": bucket,
        "keyword_hits": keyword_hits or [],
        "detected_at": detected_at.isoformat(),
        "disclosed_at": disclosed_at.isoformat() if disclosed_at is not None else None,
        "delay_ms": delay_ms,
        "quant_check_passed": quant_check_passed,
        "skip_stage": skip_stage,
        "skip_reason": skip_reason,
        "promotion_original_event_id": promotion_original_event_id,
        "promotion_original_bucket": promotion_original_bucket,
        "promotion_confidence": promotion_confidence,
        "promotion_policy": promotion_policy,
        "news_signal": _json_safe_value(news_signal),
        "ctx": _json_safe_value(ctx),
        "raw": {
            "adv_value_20d": raw.adv_value_20d,
            "spread_bps": raw.spread_bps,
            "ret_today": raw.ret_today,
            "gap": raw.gap,
            "prev_close": raw.prev_close,
            "cum_volume": raw.cum_volume,
            "listed_shares": raw.listed_shares,
            "volume_turnover_rate": raw.volume_turnover_rate,
            "prior_volume_rate": raw.prior_volume_rate,
            "intraday_value_vs_adv20d": raw.intraday_value_vs_adv20d,
            "avg_volume_20d": raw.avg_volume_20d,
            "volume_ratio_vs_avg20d": raw.volume_ratio_vs_avg20d,
            "orderbook_bid_ask_ratio": raw.orderbook_bid_ask_ratio,
            "quote_risk_state": _json_safe_value(raw.quote_risk_state),
            "orderbook_snapshot": _json_safe_value(raw.orderbook_snapshot),
            "sector": raw.sector,
            "support_price_5d": raw.support_price_5d,
            "support_price_20d": raw.support_price_20d,
            "support_reference_px": raw.support_reference_px,
            "alpha_signal": raw.alpha_signal,
            "sector_momentum": raw.sector_momentum,
        },
        "market_ctx": _json_safe_value(market_ctx),
    }
    path = _runtime_context_card_path(config, detected_at)
    line = json.dumps(_json_safe_value(record), ensure_ascii=False)

    def _write() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    _write()
    await update_runtime_artifact_index(
        config,
        date=detected_at.astimezone(_KST).strftime("%Y%m%d"),
        artifact="context_cards",
        path=path,
        recorded_at=detected_at,
    )
