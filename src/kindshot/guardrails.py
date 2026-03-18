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
from kindshot.kis_client import OrderbookSnapshot, QuoteRiskState
from kindshot.models import Action

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
        self._consecutive_stop_losses: int = 0  # 연속 손절 카운터
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

    def record_stop_loss(self) -> None:
        """Record a stop-loss exit. Increments consecutive counter."""
        self._consecutive_stop_losses += 1
        self._persist_state()

    def record_profitable_exit(self) -> None:
        """Record a profitable exit. Resets consecutive stop-loss counter."""
        self._consecutive_stop_losses = 0
        self._persist_state()

    def reset_daily(self) -> None:
        """Reset at start of new trading day."""
        self._daily_pnl = 0.0
        self._bought_tickers.clear()
        self._sector_positions.clear()
        self._position_count = 0
        self._consecutive_stop_losses = 0

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
            self._consecutive_stop_losses = data.get("consecutive_stop_losses", 0)
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
                "consecutive_stop_losses": self._consecutive_stop_losses,
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

    @property
    def consecutive_stop_losses(self) -> int:
        return self._consecutive_stop_losses


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
    quote_risk_state: Optional[QuoteRiskState] = None,
    orderbook_snapshot: Optional[OrderbookSnapshot] = None,
    intraday_value_vs_adv20d: Optional[float] = None,
    quote_temp_stop: Optional[bool] = None,
    quote_liquidation_trade: Optional[bool] = None,
    top_ask_notional: Optional[float] = None,
    decision_action: Optional[Action] = None,
    decision_confidence: Optional[int] = None,
    **kwargs: object,
) -> GuardrailResult:
    """Final safety checks before order execution."""

    # 1. Spread check
    if config.spread_check_enabled:
        if spread_bps is None:
            if config.spread_missing_policy != "pass":
                return GuardrailResult(passed=False, reason="SPREAD_DATA_MISSING")
        elif spread_bps > config.spread_bps_limit:
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

    # 4. Quote status hard stops from KIS inquire-price. Keep this limited to
    # explicit non-tradable states until other codes are validated.
    if quote_risk_state is not None:
        if quote_risk_state.temp_stop_yn == "Y":
            return GuardrailResult(passed=False, reason="TEMP_STOP")
        if quote_risk_state.sltr_yn == "Y":
            return GuardrailResult(passed=False, reason="LIQUIDATION_TRADE")
    if quote_temp_stop is True:
        return GuardrailResult(passed=False, reason="TEMP_STOP")
    if quote_liquidation_trade is True:
        return GuardrailResult(passed=False, reason="LIQUIDATION_TRADE")

    # 5a. Minimum confidence for BUY
    if decision_action == Action.BUY and decision_confidence is not None:
        if decision_confidence < config.min_buy_confidence:
            return GuardrailResult(passed=False, reason="LOW_CONFIDENCE")

    # 5b. No BUY after cutoff time (장 마감 임박 시 진입 차단)
    if decision_action == Action.BUY:
        now_kst = datetime.now(_KST)
        cutoff = now_kst.replace(
            hour=config.no_buy_after_kst_hour,
            minute=config.no_buy_after_kst_minute,
            second=0, microsecond=0,
        )
        if now_kst >= cutoff:
            return GuardrailResult(passed=False, reason="MARKET_CLOSE_CUTOFF")

    # 5c. Chase-buy prevention: 당일 이미 크게 상승한 종목은 BUY 차단
    if decision_action == Action.BUY and ret_today is not None:
        if ret_today > config.chase_buy_pct:
            return GuardrailResult(passed=False, reason="CHASE_BUY_BLOCKED")

    # 6. BUY-side top-of-book liquidity gate.
    if decision_action == Action.BUY and orderbook_snapshot is not None:
        best_ask_notional = orderbook_snapshot.ask_price1 * orderbook_snapshot.ask_size1
        if best_ask_notional < config.order_size:
            return GuardrailResult(passed=False, reason="ORDERBOOK_TOP_LEVEL_LIQUIDITY")
    if decision_action == Action.BUY and top_ask_notional is not None:
        if top_ask_notional < config.order_size:
            return GuardrailResult(passed=False, reason="ORDERBOOK_TOP_LEVEL_LIQUIDITY")

    # 7. Participation confirmation.
    if decision_action == Action.BUY and intraday_value_vs_adv20d is not None:
        if intraday_value_vs_adv20d < config.min_intraday_value_vs_adv20d:
            return GuardrailResult(passed=False, reason="INTRADAY_VALUE_TOO_THIN")

    # 8-11: Portfolio-level guardrails (require state tracking)
    if state is not None:
        # 8. Daily loss limit
        if state.daily_pnl <= -config.daily_loss_limit:
            return GuardrailResult(passed=False, reason="DAILY_LOSS_LIMIT")

        # 9. Same-stock re-buy today
        if ticker in state.bought_tickers:
            return GuardrailResult(passed=False, reason="SAME_STOCK_REBUY")

        # 10. Sector concentration
        if sector:
            if state.sector_positions.get(sector, 0) >= config.max_sector_positions:
                return GuardrailResult(passed=False, reason="SECTOR_CONCENTRATION")

        # 11. Position count limit
        if state.position_count >= config.max_positions:
            return GuardrailResult(passed=False, reason="MAX_POSITIONS")

        # 12a. Consecutive stop-loss circuit breaker (3연속 손절 시 BUY 차단)
        if decision_action == Action.BUY and state.consecutive_stop_losses >= 3:
            return GuardrailResult(passed=False, reason="CONSECUTIVE_STOP_LOSS")

    # 12. Restricted stock (관리종목/투자경고/투자위험)
    for marker in _RESTRICTED_MARKERS:
        if marker in headline:
            return GuardrailResult(passed=False, reason="RESTRICTED_STOCK")

    return GuardrailResult(passed=True)


def get_dynamic_stop_loss_pct(config: Config, confidence: int) -> float:
    """confidence 기반 동적 손절 비율. 고확신 포지션은 SL 완화."""
    if confidence >= 85:
        return min(config.paper_stop_loss_pct * 1.33, -2.0)  # -1.5% → -2.0%
    return config.paper_stop_loss_pct
