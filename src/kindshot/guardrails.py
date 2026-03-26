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

from kindshot.tz import KST as _KST

logger = logging.getLogger(__name__)


@dataclass
class GuardrailResult:
    passed: bool
    reason: Optional[str] = None


class GuardrailState:
    """Tracks intra-day trading state for portfolio-level guardrails."""

    def __init__(self, config: Config, *, state_dir: Optional[Path] = None, account_balance: float = 0.0) -> None:
        self._config = config
        self._daily_pnl: float = 0.0  # accumulated realized P&L (won)
        self._bought_tickers: set[str] = set()  # tickers bought today
        self._sector_positions: dict[str, int] = {}  # sector -> count of open positions
        self._position_count: int = 0
        self._consecutive_stop_losses: int = 0  # 연속 손절 카운터
        self._last_kst_date: Optional[str] = None  # YYYY-MM-DD
        self._state_dir = state_dir
        self._account_balance: float = account_balance  # 계좌 잔고 (비율 기반 손실 제한용)
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
            # Atomic write: tmp file + rename to prevent corruption on crash
            tmp_path = path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp_path.replace(path)
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

    @property
    def account_balance(self) -> float:
        return self._account_balance


# Well-known restricted stock markers from KRX
_RESTRICTED_MARKERS = frozenset(["관리종목", "투자경고", "투자위험", "투자주의", "거래정지"])


def _resolve_decision_time_kst(decision_time_kst: Optional[datetime]) -> datetime:
    """Return the effective decision time in KST for deterministic time-based checks."""
    if decision_time_kst is None:
        return datetime.now(_KST)
    if decision_time_kst.tzinfo is None:
        return decision_time_kst.replace(tzinfo=_KST)
    return decision_time_kst.astimezone(_KST)


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
    decision_time_kst: Optional[datetime] = None,
    decision_hold_minutes: Optional[int] = None,
    adv_threshold: Optional[float] = None,
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
    effective_adv_threshold = config.adv_threshold if adv_threshold is None else adv_threshold
    if adv_value_20d < effective_adv_threshold:
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

    # 5a. Fast-decay hold profile late-entry block.
    if (
        decision_action == Action.BUY
        and decision_hold_minutes is not None
        and decision_hold_minutes == config.fast_profile_hold_minutes
        and config.fast_profile_no_buy_after_kst_hour < 24
    ):
        now_kst = _resolve_decision_time_kst(decision_time_kst)
        fast_cutoff = now_kst.replace(
            hour=config.fast_profile_no_buy_after_kst_hour,
            minute=config.fast_profile_no_buy_after_kst_minute,
            second=0,
            microsecond=0,
        )
        if now_kst >= fast_cutoff:
            return GuardrailResult(passed=False, reason="FAST_PROFILE_LATE_ENTRY")

    # 5b. Minimum confidence for BUY
    if decision_action == Action.BUY and decision_confidence is not None:
        if decision_confidence < config.min_buy_confidence:
            return GuardrailResult(passed=False, reason="LOW_CONFIDENCE")

    # 5c. No BUY after cutoff time (장 마감 임박 시 진입 차단)
    #     hour >= 24 disables the check (used in tests / off-hours configs)
    if decision_action == Action.BUY and config.no_buy_after_kst_hour < 24:
        now_kst = _resolve_decision_time_kst(decision_time_kst)
        cutoff = now_kst.replace(
            hour=config.no_buy_after_kst_hour,
            minute=config.no_buy_after_kst_minute,
            second=0, microsecond=0,
        )
        if now_kst >= cutoff:
            return GuardrailResult(passed=False, reason="MARKET_CLOSE_CUTOFF")

    # 5d. 비유동 시간대(11:00~14:00) spread 강화: spread 기준 70% 적용
    if decision_action == Action.BUY and config.no_buy_after_kst_hour < 24:
        now_kst = _resolve_decision_time_kst(decision_time_kst)
        hour = now_kst.hour
        if 11 <= hour < 14 and spread_bps is not None:
            midday_spread_limit = config.spread_bps_limit * 0.7
            if spread_bps > midday_spread_limit:
                return GuardrailResult(passed=False, reason="MIDDAY_SPREAD_TOO_WIDE")

    # 5e. 시간대별 confidence 문턱 (개장 직후 / 마감 임박)
    if decision_action == Action.BUY and decision_confidence is not None and config.no_buy_after_kst_hour < 24:
        now_kst = _resolve_decision_time_kst(decision_time_kst)
        h, m = now_kst.hour, now_kst.minute
        # 09:00~09:30: 변동성 최고, 높은 확신만 진입
        if h == 9 and m < 30 and decision_confidence < config.opening_min_confidence:
            return GuardrailResult(passed=False, reason="OPENING_LOW_CONFIDENCE")
        # 13:00~14:30: 오후 회복기, 승률 저조 구간 — 높은 확신만
        if (h == 13 or (h == 14 and m < 30)) and decision_confidence < config.afternoon_min_confidence:
            return GuardrailResult(passed=False, reason="AFTERNOON_LOW_CONFIDENCE")
        # 14:30~15:00: 마감 임박, 확실한 촉매만
        if (h == 14 and m >= 30) and decision_confidence < config.closing_min_confidence:
            return GuardrailResult(passed=False, reason="CLOSING_LOW_CONFIDENCE")

    # 5f. Chase-buy prevention: 당일 이미 크게 상승한 종목은 BUY 차단
    if decision_action == Action.BUY and ret_today is not None:
        if ret_today > config.chase_buy_pct:
            return GuardrailResult(passed=False, reason="CHASE_BUY_BLOCKED")

    # 6. BUY-side top-of-book liquidity gate.
    # size_hint별 실제 주문 크기 사용 (S=3M, M=5M, L=7M)
    _size_hint = str(kwargs.get("decision_size_hint", "M"))
    _effective_order_size = config.order_size_for_hint(_size_hint)
    if decision_action == Action.BUY and orderbook_snapshot is not None:
        best_ask_notional = orderbook_snapshot.ask_price1 * orderbook_snapshot.ask_size1
        if best_ask_notional < _effective_order_size:
            return GuardrailResult(passed=False, reason="ORDERBOOK_TOP_LEVEL_LIQUIDITY")
    if decision_action == Action.BUY and top_ask_notional is not None:
        if top_ask_notional < _effective_order_size:
            return GuardrailResult(passed=False, reason="ORDERBOOK_TOP_LEVEL_LIQUIDITY")

    # 7. Participation confirmation (시간 보정: 장 초반은 누적 거래대금 자연히 낮음).
    if decision_action == Action.BUY and intraday_value_vs_adv20d is not None:
        now_kst = _resolve_decision_time_kst(decision_time_kst)
        h, m = now_kst.hour, now_kst.minute
        # 06:00~09:00: 장전 공시 → 거래대금 0이므로 체크 비활성
        # 09:00~09:30: 개장 직후 → 임계값 1/5로 완화
        # 09:30~10:00: 초반 → 임계값 1/2로 완화
        if h < 9:
            effective_threshold = 0.0  # 장전: participation check 비활성
        elif h == 9 and m < 30:
            effective_threshold = config.min_intraday_value_vs_adv20d * 0.2
        elif h == 9:
            effective_threshold = config.min_intraday_value_vs_adv20d * 0.5
        else:
            effective_threshold = config.min_intraday_value_vs_adv20d
        if intraday_value_vs_adv20d < effective_threshold:
            return GuardrailResult(passed=False, reason="INTRADAY_VALUE_TOO_THIN")

    # 8-11: Portfolio-level guardrails (require state tracking)
    if state is not None:
        # 8. Daily loss limit (won 기반)
        if state.daily_pnl <= -config.daily_loss_limit:
            return GuardrailResult(passed=False, reason="DAILY_LOSS_LIMIT")

        # 8b. Daily loss limit (비율 기반 — account_balance가 state에 있을 때)
        if hasattr(state, 'account_balance') and state.account_balance > 0:
            loss_pct = (state.daily_pnl / state.account_balance) * 100
            if loss_pct <= config.daily_loss_limit_pct:
                return GuardrailResult(passed=False, reason="DAILY_LOSS_LIMIT_PCT")

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

        # 12a. Consecutive stop-loss circuit breaker (N연속 손절 시 BUY 차단)
        if decision_action == Action.BUY and state.consecutive_stop_losses >= config.consecutive_loss_halt:
            return GuardrailResult(passed=False, reason="CONSECUTIVE_STOP_LOSS")

    # 12. Restricted stock (관리종목/투자경고/투자위험)
    for marker in _RESTRICTED_MARKERS:
        if marker in headline:
            return GuardrailResult(passed=False, reason="RESTRICTED_STOCK")

    return GuardrailResult(passed=True)


def get_dynamic_stop_loss_pct(config: Config, confidence: int, hold_minutes: int = 0) -> float:
    """confidence + hold_profile 기반 동적 손절 비율.

    hold_minutes=0 (EOD, 자사주소각 등 장기 촉매): SL 넓게 (-3.0%)
    hold_minutes<=20 (수주/공급계약, 반전 리스크): SL 표준
    hold_minutes>15 (특허/임상 등): confidence 기반 표준

    base=-1.5% 기준:
      conf>=85: -2.5%  /  80-84: -1.5%  /  75-79: -1.0%
      EOD hold: 위 값 * 1.3 (추가 여유)
    """
    base = config.paper_stop_loss_pct  # default: -1.5
    if confidence >= 85:
        sl = base * 1.7  # -2.55%
    elif confidence >= 80:
        sl = base  # -1.5%
    else:
        sl = max(base * 0.67, -1.0)  # -1.0%

    # EOD hold (자사주소각, 공개매수 등): 장기 트렌드이므로 SL 넓게
    if hold_minutes == 0:
        sl = min(sl * 1.3, -1.5)  # 최소 -1.5%, 최대 약 -3.3%

    return sl


def get_dynamic_tp_pct(config: Config, confidence: int, hold_minutes: int = 0) -> float:
    """confidence + hold_profile 기반 동적 익절 비율.

    hold_minutes=0 (EOD, 자사주소각 등): TP 넓게 — 장기 트렌드 수익 극대화
    hold_minutes<=15 (수주/공급계약): TP 타이트 — 빠른 반전 전 이익 확보
    hold_minutes>15 (특허/임상 등): 표준
    """
    # 기본 confidence 기반 TP
    if confidence >= 85:
        tp = 1.5
    elif confidence >= 80:
        tp = 1.0
    elif confidence >= 75:
        tp = 0.5
    else:
        tp = config.paper_take_profit_pct

    # Hold profile 보정
    if hold_minutes == 0:
        # EOD hold: 트렌드 수익 극대화 — TP 1.5배
        tp = tp * 1.5
    elif hold_minutes <= 20:
        # 수주/공급계약: 반전 리스크 — TP 0.85배 (적정 익절, 0.7은 너무 타이트)
        tp = tp * 0.85

    return tp


def apply_market_confidence_adjustment(
    confidence: int,
    kospi_change_pct: float | None,
    kosdaq_change_pct: float | None,
    *,
    breadth_ratio: float | None = None,
) -> int:
    """시장 환경 기반 confidence 조정. 하락 감점 + 상승 부스트.

    하락장: 지수 하락폭에 비례해 BUY 문턱을 높임.
    상승장: KOSPI>+1% and breadth_ratio>0.6이면 +3 부스트.
    """
    if kospi_change_pct is None and kosdaq_change_pct is None:
        return confidence

    # 두 지수 중 더 좋은 쪽 / 나쁜 쪽
    best = max(
        kospi_change_pct if kospi_change_pct is not None else 0.0,
        kosdaq_change_pct if kosdaq_change_pct is not None else 0.0,
    )
    worst = min(
        kospi_change_pct if kospi_change_pct is not None else 0.0,
        kosdaq_change_pct if kosdaq_change_pct is not None else 0.0,
    )

    # 상승장 부스트: 지수 +1%+ and breadth_ratio > 0.6
    if best >= 1.0 and breadth_ratio is not None and breadth_ratio > 0.6:
        return min(confidence + 3, 100)

    if worst >= -0.5:
        return confidence
    if worst >= -1.0:
        return max(0, confidence - 2)
    if worst >= -2.0:
        return max(0, confidence - 3)
    if worst >= -3.0:
        return max(0, confidence - 4)
    # -3% 이하: 강한 하락장 — 최대 -5 (기존 -8에서 완화)
    return max(0, confidence - 5)


def apply_trend_confidence_adjustment(confidence: int, ret_3d: float | None, pos_20d: float | None) -> int:
    """3일 수익률 + 20일 위치 기반 추세 감점.

    ret_3d > +10%: -10 (과열 종목)
    pos_20d < 20: -5 (극저점, 추세 약세)
    프롬프트 trend_filter 규칙 구현.
    """
    if ret_3d is not None and ret_3d > 10.0:
        confidence = max(0, confidence - 10)
    if pos_20d is not None and pos_20d < 20:
        confidence = max(0, confidence - 5)
    return confidence


def apply_price_reaction_adjustment(confidence: int, ret_today: float | None) -> int:
    """뉴스 감지 시점의 당일 수익률 기반 시장 반응 확인.

    주가가 소폭 상승 중(0.3~1.5%)이면 뉴스에 시장이 반응 → +2.
    하락 중(-0.5% 이하)이면 시장 불신 → -2.
    chase_buy_pct(3%)는 별도 guardrail에서 차단하므로 여기서는 확인만.
    """
    if ret_today is None:
        return confidence
    if 0.3 <= ret_today <= 1.5:
        return min(confidence + 2, 100)  # 시장 반응 확인 → 소폭 부스트
    if ret_today < -0.5:
        return max(0, confidence - 2)  # 시장 무반응/하락 → 불신
    return confidence


def apply_delay_confidence_adjustment(confidence: int, delay_ms: int | None) -> int:
    """Detection delay 기반 confidence 감점.

    뉴스 감지가 늦을수록 가격에 이미 반영됐을 가능성 높음.
    - <30초: 조정 없음 (빠른 감지)
    - 30~60초: -1
    - 60~120초: -2
    - 120초+: -3
    """
    if delay_ms is None or delay_ms < 30_000:
        return confidence
    if delay_ms < 60_000:
        return max(0, confidence - 1)
    if delay_ms < 120_000:
        return max(0, confidence - 2)
    return max(0, confidence - 3)


_DISCLOSURE_DORGS = ("거래소", "금감원", "한국거래소", "코스닥", "KIND")


def apply_dorg_confidence_adjustment(confidence: int, dorg: str) -> int:
    """dorg(공시 제공기관) 기반 confidence 조정.

    KIND/거래소/금감원 공시 = 신뢰도 높음 → 조정 없음.
    뉴스 기사 출처(매경, 한경 등) = false positive 리스크 → -5.
    dorg 비어있으면 조정 없음 (KIND RSS 등 dorg 정보 없는 소스).
    """
    if not dorg:
        return confidence
    if any(dorg.startswith(prefix) for prefix in _DISCLOSURE_DORGS):
        return confidence
    # 뉴스 출처 → 감점
    return max(0, confidence - 5)


def apply_volume_confidence_adjustment(confidence: int, prior_volume_rate: float | None) -> int:
    """전일대비 거래량 비율 기반 confidence 조정.

    거래량 급증 = 시장이 뉴스에 반응 중 → 부스트.
    거래량 평이 = 시장 무관심 → 감점.

    prior_volume_rate: 전일 대비 % (e.g. 200.0 = 전일 2배)
    - >=300%: +3 (거래량 폭증, 강한 반응)
    - >=150%: +1 (거래량 증가, 반응 확인)
    - <50%: -3 (거래량 급감, 무관심)
    - <80%: -1 (거래량 감소)
    """
    if prior_volume_rate is None or prior_volume_rate <= 0:
        return confidence
    if prior_volume_rate >= 300.0:
        return min(confidence + 3, 100)
    if prior_volume_rate >= 150.0:
        return min(confidence + 1, 100)
    if prior_volume_rate < 50.0:
        return max(0, confidence - 3)
    if prior_volume_rate < 80.0:
        return max(0, confidence - 1)
    return confidence


def apply_time_session_confidence_adjustment(confidence: int, decision_time_kst: datetime | None) -> int:
    """시간대별 confidence 조정 (프롬프트 time_session_rules 구현).

    06:00~08:30 장전 공시: +5 (가격 미반영, 최고 기회)
    11:00~13:00 비유동 시간대: -3 (유동성 부족, 승률 저조)
    시간 정보 없으면 조정 없음.
    """
    if decision_time_kst is None:
        return confidence
    now_kst = _resolve_decision_time_kst(decision_time_kst)
    h, m = now_kst.hour, now_kst.minute
    # 장전 공시 (06:00~08:30): 최고 기회 — 가격 미반영
    if (6 <= h < 8) or (h == 8 and m <= 30):
        return min(confidence + 5, 100)
    # 비유동 시간대 (11:00~13:00): 승률 저조
    if 11 <= h < 13:
        return max(0, confidence - 3)
    return confidence


def apply_adv_confidence_adjustment(confidence: int, adv_value_20d: float) -> int:
    """ADV 기반 confidence 캡/페널티/보너스. 소형주 집중 전략."""
    if adv_value_20d >= 500_000_000_000:  # 5000억+: 초대형주 → cap 65 (sell the news)
        return min(confidence, 65)
    if adv_value_20d >= 200_000_000_000:  # 2000~5000억: 대형주 → -5, cap 72
        return min(max(0, confidence - 5), 72)
    if adv_value_20d >= 50_000_000_000:  # 500~2000억: 소형주 최적 구간 → +3 보너스
        return min(confidence + 3, 100)
    # <500억: 초소형주, 조정 없음 (ADV 필터에서 대부분 걸림)
    return confidence


def calculate_position_size(
    config: Config,
    size_hint: str,
    *,
    account_balance: float = 0.0,
    minute_volume: float = 0.0,
    ask_depth_notional: float = 0.0,
) -> float:
    """포지션 사이즈 계산: min(hint 기반, 계좌리스크, 거래대금, 호가잔량).

    Returns:
        주문 금액 (won). 0이면 진입 불가.
    """
    hint_size = config.order_size_for_hint(size_hint)
    candidates = [hint_size]

    if account_balance > 0 and config.account_risk_pct > 0:
        # 계좌 잔고의 N%를 1건 최대 리스크로 → SL 기준 포지션 산출
        # paper_stop_loss_pct는 퍼센트 단위 (e.g. -0.7 = -0.7%)
        sl_pct = abs(config.paper_stop_loss_pct) / 100
        if sl_pct > 0:
            risk_amount = account_balance * (config.account_risk_pct / 100)
            account_based = risk_amount / sl_pct
            candidates.append(account_based)

    if minute_volume > 0 and config.minute_volume_cap_pct > 0:
        candidates.append(minute_volume * (config.minute_volume_cap_pct / 100))

    if ask_depth_notional > 0 and config.ask_depth_cap_pct > 0:
        candidates.append(ask_depth_notional * (config.ask_depth_cap_pct / 100))

    return min(candidates)


def downgrade_size_hint(size_hint: str) -> str:
    """size_hint를 한 단계 낮춤: L→M, M→S, S→S."""
    if size_hint == "L":
        return "M"
    if size_hint == "M":
        return "S"
    return "S"


def get_kill_switch_size_hint(
    config: Config,
    state: Optional[GuardrailState],
    original_hint: str,
) -> str:
    """연패 상태에 따라 size_hint 다운그레이드. 2연패 시 한단계 축소."""
    if state is None:
        return original_hint
    if state.consecutive_stop_losses >= config.consecutive_loss_size_down:
        return downgrade_size_hint(original_hint)
    return original_hint
