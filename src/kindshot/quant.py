"""Quant 3-second check: ADV, spread, extreme move."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

from kindshot.config import Config
from kindshot.models import QuantCheckDetail


@dataclass
class QuantResult:
    passed: bool
    detail: QuantCheckDetail
    skip_reason: Optional[str]
    should_track_price: bool  # True if failed but sampled for tracking
    analysis_tag: Optional[str]


def quant_check(
    adv_value_20d: float,
    spread_bps: Optional[float],
    ret_today: float,
    config: Config,
) -> QuantResult:
    """Run 3 quant filters. Returns result with pass/fail and skip reason."""

    adv_ok = adv_value_20d >= config.adv_threshold

    if config.spread_check_enabled:
        if spread_bps is None:
            spread_ok = False  # fail-close: data unavailable means don't trade
        else:
            spread_ok = spread_bps <= config.spread_bps_limit
    else:
        spread_ok = True  # skip check when disabled

    extreme_ok = abs(ret_today) <= config.extreme_move_pct

    detail = QuantCheckDetail(
        adv_value_20d_ok=adv_ok,
        spread_bps_ok=spread_ok,
        extreme_move_ok=extreme_ok,
    )

    passed = adv_ok and spread_ok and extreme_ok

    # Determine skip reason (first failure wins)
    skip_reason: Optional[str] = None
    if not passed:
        if not adv_ok:
            skip_reason = "ADV_TOO_LOW"
        elif not spread_ok:
            skip_reason = "SPREAD_TOO_WIDE"
        elif not extreme_ok:
            skip_reason = "EXTREME_MOVE"

    # 10% sampling of quant fails for price tracking
    should_track = False
    analysis_tag: Optional[str] = None
    if not passed and random.random() < config.quant_fail_sample_rate:
        should_track = True
        analysis_tag = "QUANT_FAIL_SAMPLE"

    return QuantResult(
        passed=passed,
        detail=detail,
        skip_reason=skip_reason,
        should_track_price=should_track,
        analysis_tag=analysis_tag,
    )
