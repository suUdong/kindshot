"""Hard guardrails — final safety net before order execution.

Runs AFTER LLM call. Uses same thresholds as quant pre-filter (spread, ADV, extreme move)
plus portfolio-level risk controls.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from kindshot.config import Config

logger = logging.getLogger(__name__)
_KST = timezone(timedelta(hours=9))


@dataclass
class GuardrailResult:
    passed: bool
    reason: Optional[str] = None


class GuardrailState:
    """Tracks intra-day trading state for portfolio-level guardrails."""

    def __init__(self, config: Config, *, state_dir: Optional[Path] = None) -> None:
        self._config = config
        self._daily_pnl: float = 0.0  # accumulated realized P&L (won)
        self._bought_tickers: set[str] = set()  # tickers bought today
        self._sector_positions: dict[str, int] = {}  # sector -> count of open positions
        self._position_count: int = 0
        self._last_kst_date: Optional[str] = None  # YYYY-MM-DD
        self._state_dir = state_dir
        if state_dir:
            self._load_state()

    def record_buy(self, ticker: str, sector: str = "") -> None:
        """Record a BUY execution for state tracking."""
        self._bought_tickers.add(ticker)
        self._position_count += 1
        if sector:
            self._sector_positions[sector] = self._sector_positions.get(sector, 0) + 1
        self._persist_state()

    def record_pnl(self, pnl: float) -> None:
        """Record realized P&L."""
        self._daily_pnl += pnl
        self._persist_state()

    def reset_daily(self) -> None:
        """Reset at start of new trading day."""
        self._daily_pnl = 0.0
        self._bought_tickers.clear()
        self._sector_positions.clear()
        self._position_count = 0

    def check_daily_reset(self) -> None:
        """Auto-reset if KST date changed since last check."""
        today = datetime.now(_KST).strftime("%Y-%m-%d")
        if self._last_kst_date is not None and self._last_kst_date != today:
            logger.info("KST date changed %s → %s, resetting guardrail state", self._last_kst_date, today)
            self.reset_daily()
        self._last_kst_date = today

    def record_sell(self, ticker: str, sector: str = "") -> None:
        """Record a position close for state tracking."""
        self._position_count = max(0, self._position_count - 1)
        if sector and self._sector_positions.get(sector, 0) > 0:
            self._sector_positions[sector] -= 1
        self._persist_state()

    def _state_file(self) -> Optional[Path]:
        if not self._state_dir:
            return None
        return self._state_dir / "guardrail_state.json"

    def _load_state(self) -> None:
        path = self._state_file()
        if not path or not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            # Only load if same KST date
            today = datetime.now(_KST).strftime("%Y-%m-%d")
            if data.get("date") != today:
                logger.info("Guardrail state from %s, today is %s — skipping load", data.get("date"), today)
                return
            self._daily_pnl = data.get("daily_pnl", 0.0)
            self._bought_tickers = set(data.get("bought_tickers", []))
            self._position_count = data.get("position_count", 0)
            self._sector_positions = data.get("sector_positions", {})
            self._last_kst_date = today
            logger.info("Loaded guardrail state: pnl=%.0f, positions=%d, bought=%d",
                        self._daily_pnl, self._position_count, len(self._bought_tickers))
        except Exception:
            logger.exception("Failed to load guardrail state")

    def _persist_state(self) -> None:
        path = self._state_file()
        if not path:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "date": datetime.now(_KST).strftime("%Y-%m-%d"),
                "daily_pnl": self._daily_pnl,
                "bought_tickers": sorted(self._bought_tickers),
                "position_count": self._position_count,
                "sector_positions": self._sector_positions,
            }
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            logger.exception("Failed to persist guardrail state")

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    @property
    def bought_tickers(self) -> set[str]:
        return self._bought_tickers

    @property
    def sector_positions(self) -> dict[str, int]:
        return self._sector_positions

    @property
    def position_count(self) -> int:
        return self._position_count


# Well-known restricted stock markers from KRX
_RESTRICTED_MARKERS = frozenset(["관리종목", "투자경고", "투자위험", "투자주의", "거래정지"])


def check_guardrails(
    ticker: str,
    config: Config,
    spread_bps: Optional[float] = None,
    adv_value_20d: Optional[float] = None,
    ret_today: Optional[float] = None,
    *,
    state: Optional[GuardrailState] = None,
    headline: str = "",
    sector: str = "",
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

    # 4-8: Portfolio-level guardrails (require state tracking)
    if state is not None:
        # 4. Daily loss limit
        if state.daily_pnl <= -config.daily_loss_limit:
            return GuardrailResult(passed=False, reason="DAILY_LOSS_LIMIT")

        # 5. Same-stock re-buy today
        if ticker in state.bought_tickers:
            return GuardrailResult(passed=False, reason="SAME_STOCK_REBUY")

        # 6. Sector concentration (max per sector; skipped when sector data unavailable)
        if sector:
            if state.sector_positions.get(sector, 0) >= config.max_sector_positions:
                return GuardrailResult(passed=False, reason="SECTOR_CONCENTRATION")

        # 7. Position count limit
        if state.position_count >= config.max_positions:
            return GuardrailResult(passed=False, reason="MAX_POSITIONS")

    # 8. Restricted stock (관리종목/투자경고/투자위험)
    for marker in _RESTRICTED_MARKERS:
        if marker in headline:
            return GuardrailResult(passed=False, reason="RESTRICTED_STOCK")

    return GuardrailResult(passed=True)
