"""Hard guardrails — MVP: interface stub only.

MVP boundary:
    Quant 3-second check (quant.py) runs BEFORE LLM call as a pre-filter.
    Guardrails run AFTER LLM call as the final safety net.
    Both use the same thresholds (spread_bps=25, adv=50억, extreme=20%).

    In MVP, guardrails always pass (stub). Real implementation in v0.4
    when actual order execution is added.

Post-MVP guardrail checklist:
    1. spread_bps > 25 → BLOCK
    2. adv_20d < 50억 → BLOCK
    3. VI / 상한가 근접 (+25%) / 극단과열 (±20%) → BLOCK
    4. 일일 손실 한도 초과 → BLOCK
    5. 동일 종목 당일 재매수 → BLOCK
    6. 동일 섹터 동시 2개 → BLOCK
    7. 포지션 > 계좌 10% → BLOCK
    8. 관리종목 / 투자경고 / 투자위험 → BLOCK
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class GuardrailResult:
    passed: bool
    reason: Optional[str] = None


def check_guardrails(
    ticker: str,
    spread_bps: Optional[float],
    adv_value_20d: Optional[float],
    ret_today: Optional[float],
    **kwargs: object,
) -> GuardrailResult:
    """MVP stub: always passes. Real checks added in v0.4."""
    return GuardrailResult(passed=True)
