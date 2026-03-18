"""Quant 3-second check: ADV, spread, extreme move."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Optional

from kindshot.config import Config
from kindshot.models import QuantCheckDetail


_KST = timezone(timedelta(hours=9))
_CONTINUOUS_SESSION_START = time(9, 0)
_CONTINUOUS_SESSION_END = time(15, 30)


@dataclass
class QuantResult:
    passed: bool
    detail: QuantCheckDetail
    skip_reason: Optional[str]
    should_track_price: bool  # True if failed but sampled for tracking
    analysis_tag: Optional[str]


def _is_continuous_session(observed_at: Optional[datetime]) -> bool:
    if observed_at is None:
        return True
    if observed_at.tzinfo is None:
        localized = observed_at.replace(tzinfo=_KST)
    else:
        localized = observed_at.astimezone(_KST)
    current = localized.timetz().replace(tzinfo=None)
    return _CONTINUOUS_SESSION_START <= current <= _CONTINUOUS_SESSION_END


def quant_check(
    adv_value_20d: float,
    spread_bps: Optional[float],
    ret_today: Optional[float],
    config: Config,
    *,
    observed_at: Optional[datetime] = None,
) -> QuantResult:
    """Run 3 quant filters. Returns result with pass/fail and skip reason."""

    adv_ok = adv_value_20d >= config.adv_threshold

    if config.spread_check_enabled:
        if spread_bps is None:
            # spread_missing_policy: "pass" = fail-open (통과), "fail" = fail-close (차단)
            spread_ok = config.spread_missing_policy == "pass"
        else:
            spread_ok = spread_bps <= config.spread_bps_limit
    else:
        spread_ok = True  # skip check when disabled

    if ret_today is None:
        extreme_ok = False  # fail-close: no data means don't trade
    else:
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
            if config.spread_check_enabled and spread_bps is None:
                if _is_continuous_session(observed_at):
                    skip_reason = "SPREAD_DATA_MISSING"
                else:
                    skip_reason = "SPREAD_DATA_MISSING_OFF_HOURS"
            else:
                skip_reason = "SPREAD_TOO_WIDE"
        elif not extreme_ok:
            if ret_today is None:
                skip_reason = "RET_TODAY_DATA_MISSING"
            else:
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
