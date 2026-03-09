"""Hard guardrails — final safety net before order execution.

Runs AFTER LLM call. Uses same thresholds as quant pre-filter (spread, ADV, extreme move).

Post-MVP guardrail checklist (not yet implemented):
    4. 일일 손실 한도 초과 → BLOCK
    5. 동일 종목 당일 재매수 → BLOCK
    6. 동일 섹터 동시 2개 → BLOCK
    7. 포지션 > 계좌 10% → BLOCK
    8. 관리종목 / 투자경고 / 투자위험 → BLOCK
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from kindshot.config import Config


@dataclass
class GuardrailResult:
    passed: bool
    reason: Optional[str] = None


def check_guardrails(
    ticker: str,
    config: Config,
    spread_bps: Optional[float] = None,
    adv_value_20d: Optional[float] = None,
    ret_today: Optional[float] = None,
    **kwargs: object,
) -> GuardrailResult:
    """Final safety checks before order execution."""

    # 1. Spread check
    if config.spread_check_enabled:
        if spread_bps is None:
            return GuardrailResult(passed=False, reason="SPREAD_DATA_MISSING")
        if spread_bps > config.spread_bps_limit:
            return GuardrailResult(passed=False, reason="SPREAD_TOO_WIDE")

    # 2. ADV check
    if adv_value_20d is None:
        return GuardrailResult(passed=False, reason="ADV_DATA_MISSING")
    if adv_value_20d < config.adv_threshold:
        return GuardrailResult(passed=False, reason="ADV_TOO_LOW")

    # 3. Extreme move check
    if ret_today is None:
        return GuardrailResult(passed=False, reason="RET_TODAY_DATA_MISSING")
    if abs(ret_today) > config.extreme_move_pct:
        return GuardrailResult(passed=False, reason="EXTREME_MOVE")

    return GuardrailResult(passed=True)
