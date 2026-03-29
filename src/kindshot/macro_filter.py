"""Macro regime filter — gates entries and adjusts confidence based on macro-intelligence data.

Consumes the downstream kindshot payload from macro-intelligence API.
Fail-open: if API is unreachable, trading proceeds without macro filter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MacroFilterResult:
    """Result of macro filter evaluation."""
    blocked: bool
    reason: str = ""
    confidence_adj: int = 0  # additive adjustment to confidence score
    position_multiplier: float = 1.0


@dataclass(frozen=True)
class MacroSnapshot:
    """Lightweight snapshot of macro-intelligence downstream payload for kindshot."""
    overall_regime: str  # "expansionary" | "neutral" | "contractionary"
    overall_confidence: float
    kr_regime: str
    kr_confidence: float
    kr_signals: dict[str, str]  # e.g. {"krw_usd": "1380 (neutral)", "yield_curve": "+0.15 (bullish)"}
    transition_watch: str  # "stable" | "watch_contractionary" | "watch_expansionary"
    transition_probability: float
    strategy: dict[str, Any]  # kindshot-specific strategy map from macro-intelligence

    @classmethod
    def from_downstream_payload(cls, payload: dict[str, Any]) -> Optional["MacroSnapshot"]:
        """Parse macro-intelligence /regime/downstream/kindshot response."""
        if payload.get("status") != "ok":
            return None

        layers = payload.get("layers", {})
        kr_layer = layers.get("kr", {})
        transition = payload.get("transition", {})

        return cls(
            overall_regime=payload.get("overall_regime", "neutral"),
            overall_confidence=payload.get("overall_confidence", 0.0),
            kr_regime=kr_layer.get("regime", "neutral"),
            kr_confidence=kr_layer.get("confidence", 0.0),
            kr_signals=kr_layer.get("signals", {}),
            transition_watch=transition.get("watch", "stable"),
            transition_probability=transition.get("probability", 0.0),
            strategy=payload.get("strategy", {}),
        )


def _parse_signal_value(signal_str: str) -> Optional[float]:
    """Extract numeric value from signal string like '1380 (neutral)' or '+0.15 (bullish)'."""
    if not signal_str:
        return None
    # 첫 번째 토큰에서 숫자 추출
    token = signal_str.split()[0].replace(",", "")
    try:
        return float(token)
    except (ValueError, IndexError):
        return None


class MacroFilter:
    """Macro regime filter for kindshot entry gating and confidence adjustment.

    Rules:
    1. Entry blocking:
       - contractionary overall + confidence >= 0.5 → BLOCK
       - watch_contractionary + probability >= 0.7 → BLOCK
       - KRW/USD > 1400 → BLOCK (외환위기급 원화 약세)
    2. Confidence adjustment:
       - contractionary (but not blocked): -5
       - neutral: 0
       - expansionary: +3
       - watch_contractionary: -3 추가
       - KRW/USD 1350~1400: -3 (원화 약세 경고)
       - KR yield curve 역전 (< -0.5): -3
    3. Position multiplier: delegated to existing MarketMonitor logic
    """

    # Entry blocking thresholds
    BLOCK_CONFIDENCE_THRESHOLD = 0.5
    BLOCK_TRANSITION_PROBABILITY = 0.7
    KRW_USD_BLOCK_THRESHOLD = 1400.0

    # Confidence adjustments by regime
    REGIME_CONFIDENCE_ADJ: dict[str, int] = {
        "expansionary": 3,
        "neutral": 0,
        "contractionary": -5,
    }
    WATCH_CONTRACTIONARY_ADJ = -3
    KRW_WEAKNESS_ADJ = -3
    KRW_WEAKNESS_THRESHOLD = 1350.0
    YIELD_CURVE_INVERSION_ADJ = -3
    YIELD_CURVE_INVERSION_THRESHOLD = -0.5

    def evaluate(self, snapshot: Optional[MacroSnapshot]) -> MacroFilterResult:
        """Evaluate macro conditions and return filter result.

        Fail-open: returns pass-through result when snapshot is None.
        """
        if snapshot is None:
            return MacroFilterResult(blocked=False)

        regime = snapshot.overall_regime
        confidence = snapshot.overall_confidence

        # --- KR layer signal 파싱 ---
        krw_usd = _parse_signal_value(snapshot.kr_signals.get("krw_usd", ""))
        yield_curve = _parse_signal_value(snapshot.kr_signals.get("yield_curve", ""))

        # === Entry blocking checks ===

        # 1. Contractionary regime with sufficient confidence
        if regime == "contractionary" and confidence >= self.BLOCK_CONFIDENCE_THRESHOLD:
            return MacroFilterResult(
                blocked=True,
                reason=f"macro_gate: overall={regime} confidence={confidence:.0%}",
            )

        # 2. Watch contractionary with high transition probability
        if (snapshot.transition_watch == "watch_contractionary"
                and snapshot.transition_probability >= self.BLOCK_TRANSITION_PROBABILITY):
            return MacroFilterResult(
                blocked=True,
                reason=(
                    f"macro_gate: watch_contractionary "
                    f"prob={snapshot.transition_probability:.0%}"
                ),
            )

        # 3. KRW/USD 급등 (외환위기급)
        if krw_usd is not None and krw_usd > self.KRW_USD_BLOCK_THRESHOLD:
            return MacroFilterResult(
                blocked=True,
                reason=f"macro_gate: krw_usd={krw_usd:.0f} > {self.KRW_USD_BLOCK_THRESHOLD:.0f}",
            )

        # === Confidence adjustments (not blocked) ===
        adj = self.REGIME_CONFIDENCE_ADJ.get(regime, 0)

        # Watch contractionary 추가 감점
        if snapshot.transition_watch == "watch_contractionary":
            adj += self.WATCH_CONTRACTIONARY_ADJ

        # KRW 약세 경고 (1350~1400)
        if krw_usd is not None and krw_usd > self.KRW_WEAKNESS_THRESHOLD:
            adj += self.KRW_WEAKNESS_ADJ

        # 금리 역전
        if yield_curve is not None and yield_curve < self.YIELD_CURVE_INVERSION_THRESHOLD:
            adj += self.YIELD_CURVE_INVERSION_ADJ

        return MacroFilterResult(
            blocked=False,
            confidence_adj=adj,
        )
