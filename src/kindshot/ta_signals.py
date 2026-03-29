"""Technical Analysis signal generators for kindshot.

Provides TA-based filters to complement news-driven signals:
- Mean Reversion: z-score based oversold detection
- Volume Spike: abnormal volume + bullish candle detection
- Momentum filter: trend confirmation for entry filtering

These can be used as:
1. Standalone signal sources
2. Entry filters (confirm news signals with TA)
3. Risk filters (avoid entering counter-trend)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np


class TASignalType(Enum):
    MEAN_REVERSION = "mean_reversion"
    VOLUME_SPIKE = "volume_spike"
    MOMENTUM = "momentum"


@dataclass(frozen=True)
class TASignal:
    signal_type: TASignalType
    ticker: str
    score: float          # 0~1 신뢰도
    detail: str           # 사람이 읽을 수 있는 설명
    z_score: Optional[float] = None
    volume_ratio: Optional[float] = None
    momentum_pct: Optional[float] = None


@dataclass(frozen=True)
class TAConfig:
    # 평균회귀
    mr_lookback: int = 20
    mr_z_threshold: float = -2.0
    # 거래량 스파이크
    vs_lookback: int = 20
    vs_spike_mult: float = 3.0
    # 모멘텀
    mom_lookback: int = 20
    mom_threshold: float = 0.0  # 양수면 상승 추세만 허용


def check_mean_reversion(
    closes: list[float],
    config: TAConfig | None = None,
) -> Optional[TASignal]:
    """종가 리스트로 평균회귀 시그널 체크.

    Args:
        closes: 최근 N일 종가 (오래된 순 → 최신순)
        config: TA 설정
    Returns:
        TASignal if oversold detected, None otherwise
    """
    cfg = config or TAConfig()
    if len(closes) < cfg.mr_lookback + 1:
        return None

    arr = np.array(closes, dtype=float)
    ma = np.mean(arr[-cfg.mr_lookback - 1:-1])
    std = np.std(arr[-cfg.mr_lookback - 1:-1])
    if std == 0:
        return None

    current = arr[-1]
    z = (current - ma) / std

    if z <= cfg.mr_z_threshold:
        # 점수: z가 낮을수록 높은 점수 (최대 1.0)
        score = min(1.0, abs(z) / 4.0)
        return TASignal(
            signal_type=TASignalType.MEAN_REVERSION,
            ticker="",  # 호출자가 설정
            score=round(score, 2),
            detail=f"과매도 감지: z={z:.2f} (MA{cfg.mr_lookback}={ma:.0f}, 현재={current:.0f})",
            z_score=round(z, 2),
        )
    return None


def check_volume_spike(
    volumes: list[float],
    closes: list[float],
    opens: list[float],
    config: TAConfig | None = None,
) -> Optional[TASignal]:
    """거래량 스파이크 + 양봉 체크.

    Args:
        volumes: 최근 N일 거래량 (오래된 순)
        closes: 최근 N일 종가
        opens: 최근 N일 시가
        config: TA 설정
    Returns:
        TASignal if spike detected, None otherwise
    """
    cfg = config or TAConfig()
    if len(volumes) < cfg.vs_lookback + 1:
        return None

    vol_arr = np.array(volumes, dtype=float)
    vol_ma = np.mean(vol_arr[-cfg.vs_lookback - 1:-1])
    if vol_ma <= 0:
        return None

    current_vol = vol_arr[-1]
    vol_ratio = current_vol / vol_ma
    is_bullish = closes[-1] > opens[-1]

    if vol_ratio >= cfg.vs_spike_mult and is_bullish:
        score = min(1.0, vol_ratio / (cfg.vs_spike_mult * 2))
        return TASignal(
            signal_type=TASignalType.VOLUME_SPIKE,
            ticker="",
            score=round(score, 2),
            detail=f"거래량 스파이크: {vol_ratio:.1f}x (평균 대비), 양봉",
            volume_ratio=round(vol_ratio, 1),
        )
    return None


def check_momentum(
    closes: list[float],
    config: TAConfig | None = None,
) -> Optional[TASignal]:
    """N일 모멘텀 필터. 진입 전 추세 확인용.

    Args:
        closes: 최근 N일 종가
        config: TA 설정
    Returns:
        TASignal with momentum info (always returns if enough data)
    """
    cfg = config or TAConfig()
    if len(closes) < cfg.mom_lookback + 1:
        return None

    current = closes[-1]
    past = closes[-(cfg.mom_lookback + 1)]
    if past == 0:
        return None

    mom_pct = (current / past - 1) * 100

    if mom_pct >= cfg.mom_threshold:
        label = "상승"
    else:
        label = "하락"

    score = min(1.0, abs(mom_pct) / 20.0)  # 20% 이상이면 만점
    return TASignal(
        signal_type=TASignalType.MOMENTUM,
        ticker="",
        score=round(score, 2),
        detail=f"{cfg.mom_lookback}일 모멘텀: {mom_pct:+.1f}% ({label} 추세)",
        momentum_pct=round(mom_pct, 2),
    )


def ta_entry_filter(
    closes: list[float],
    volumes: list[float] | None = None,
    opens: list[float] | None = None,
    config: TAConfig | None = None,
) -> dict[str, TASignal | None]:
    """뉴스 시그널 진입 전 TA 필터 종합 체크.

    Returns dict with keys: mean_reversion, volume_spike, momentum
    """
    cfg = config or TAConfig()
    result: dict[str, TASignal | None] = {
        "mean_reversion": check_mean_reversion(closes, cfg),
        "momentum": check_momentum(closes, cfg),
        "volume_spike": None,
    }
    if volumes and opens and len(volumes) == len(closes):
        result["volume_spike"] = check_volume_spike(volumes, closes, opens, cfg)
    return result
