"""멀티 타임프레임 분석 — 5분/15분/1시간 봉 기반 추세 확인."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass

from kindshot.config import Config
from kindshot.kis_client import KisClient

logger = logging.getLogger(__name__)

# 캐시: ticker -> (MtfResult, expire_time)
_mtf_cache: OrderedDict[str, tuple["MtfResult", float]] = OrderedDict()
_MTF_CACHE_TTL = 120  # 기본 2분
_MTF_CACHE_MAX = 256


@dataclass(frozen=True)
class MtfResult:
    """멀티 타임프레임 분석 결과."""
    trend_5m: str  # "UP", "DOWN", "SIDEWAYS"
    trend_15m: str
    trend_1h: str
    alignment_score: int  # 0~100
    detail: str = ""  # 디버그용 상세


def configure_mtf_cache(ttl_s: int) -> None:
    global _MTF_CACHE_TTL
    _MTF_CACHE_TTL = max(1, ttl_s)


def _determine_trend(candles: list[dict]) -> str:
    """캔들 데이터에서 추세 판단. MA crossover + 최근 3봉 방향."""
    if len(candles) < 5:
        return "SIDEWAYS"

    closes = [c["close"] for c in candles if c.get("close", 0) > 0]
    if len(closes) < 5:
        return "SIDEWAYS"

    # 단기 MA (3) vs 장기 MA (10 or available)
    ma_short = sum(closes[:3]) / 3
    ma_long_n = min(10, len(closes))
    ma_long = sum(closes[:ma_long_n]) / ma_long_n

    # 최근 3봉 방향
    recent_3 = closes[:3]  # 최신이 첫번째
    up_count = sum(1 for i in range(len(recent_3) - 1) if recent_3[i] > recent_3[i + 1])
    down_count = sum(1 for i in range(len(recent_3) - 1) if recent_3[i] < recent_3[i + 1])

    # MA crossover + 방향 조합 (equal은 중립으로 처리)
    ma_threshold = ma_long * 0.001  # 0.1% 이내는 동일로 간주
    ma_bullish = ma_short > ma_long + ma_threshold
    ma_bearish = ma_short < ma_long - ma_threshold

    if ma_bullish and up_count > down_count:
        return "UP"
    elif ma_bearish and down_count > up_count:
        return "DOWN"
    return "SIDEWAYS"


def _calc_alignment_score(trend_5m: str, trend_15m: str, trend_1h: str) -> int:
    """3개 타임프레임 일치도 점수 (0~100)."""
    trends = [trend_5m, trend_15m, trend_1h]
    up_count = trends.count("UP")
    down_count = trends.count("DOWN")

    if up_count == 3:
        return 100  # 전체 상승 정렬
    if down_count == 3:
        return 0    # 전체 하락 정렬
    if up_count == 2:
        return 75   # 2/3 상승
    if down_count == 2:
        return 25   # 2/3 하락
    return 50       # 혼조


async def analyze_mtf(ticker: str, kis: KisClient, config: Config) -> MtfResult:
    """멀티 타임프레임 분석 수행. 캐시 적용."""
    if not config.mtf_enabled:
        return MtfResult("SIDEWAYS", "SIDEWAYS", "SIDEWAYS", 50, "MTF disabled")

    # 캐시 확인
    now = time.monotonic()
    if ticker in _mtf_cache:
        result, expire = _mtf_cache[ticker]
        if expire > now:
            _mtf_cache.move_to_end(ticker)
            return result

    # 3개 타임프레임 병렬 조회
    try:
        candles_5m, candles_15m, candles_1h = await asyncio.gather(
            kis.fetch_minute_candles(ticker, 5),
            kis.fetch_minute_candles(ticker, 15),
            kis.fetch_minute_candles(ticker, 60),
            return_exceptions=True,
        )
    except Exception:
        logger.exception("MTF fetch failed for %s", ticker)
        return MtfResult("SIDEWAYS", "SIDEWAYS", "SIDEWAYS", 50, "fetch error")

    # 예외 처리
    if isinstance(candles_5m, Exception):
        candles_5m = []
    if isinstance(candles_15m, Exception):
        candles_15m = []
    if isinstance(candles_1h, Exception):
        candles_1h = []

    trend_5m = _determine_trend(candles_5m)
    trend_15m = _determine_trend(candles_15m)
    trend_1h = _determine_trend(candles_1h)
    alignment = _calc_alignment_score(trend_5m, trend_15m, trend_1h)

    detail = f"5m={trend_5m}({len(candles_5m)}봉) 15m={trend_15m}({len(candles_15m)}봉) 1h={trend_1h}({len(candles_1h)}봉)"
    result = MtfResult(trend_5m, trend_15m, trend_1h, alignment, detail)

    # 캐시 저장
    _mtf_cache[ticker] = (result, now + _MTF_CACHE_TTL)
    while len(_mtf_cache) > _MTF_CACHE_MAX:
        _mtf_cache.popitem(last=False)

    return result
