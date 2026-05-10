"""v86 VolumeBreakoutFeed — 100% rule-based daily-bar 거래량 폭증 + N-day high 돌파 전략.

LLM 호출 없이 pykrx 일봉만으로 동작. circuit breaker OPEN 상황에서도 시그널 생성.

Signal: BUY 발행 조건 (모두 만족)
  1. close_today > prior_high_N (이전 N거래일 최고가 돌파, today 제외)
  2. vol_today >= vol_ratio_threshold × prior_avg_vol_N (거래량 폭증)
  3. ret_today 가 [0, max_ret_today] 범위 (추격매수 방지)
  4. adv_value_N >= adv_threshold (유동성)
  5. 종목별 cooldown 미경과

Confidence: base 70 + vol_ratio 보너스 (최대 +10), breakout strength 보너스 (최대 +5).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator, Awaitable, Callable, Optional

from kindshot.config import Config
from kindshot.models import Action, SizeHint
from kindshot.strategy import SignalSource, TradeSignal
from kindshot.tz import KST as _KST

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BreakoutSnapshot:
    """v86 신호 평가용 데이터."""
    ticker: str
    corp_name: str
    close_today: float
    prior_high_n: float | None
    vol_today: float | None
    prior_avg_vol_n: float | None
    adv_value_n: float | None
    ret_today: float | None


async def _default_pykrx_breakout(ticker: str, lookback_n: int) -> dict:
    """pykrx 일봉으로 breakout 평가용 raw features 반환.

    Returns dict with keys: close_today, prior_high_n, vol_today, prior_avg_vol_n,
    adv_value_n, ret_today. 데이터 부족 시 빈 dict.
    """
    def _fetch() -> dict:
        try:
            from datetime import timedelta as _td

            from pykrx import stock

            today = datetime.now(_KST).strftime("%Y%m%d")
            start = (datetime.now(_KST) - _td(days=lookback_n * 2 + 30)).strftime("%Y%m%d")
            df = stock.get_market_ohlcv(start, today, ticker)
            if df is None or df.empty or len(df) < lookback_n + 2:
                return {}

            close_col = "종가" if "종가" in df.columns else "Close"
            high_col = "고가" if "고가" in df.columns else "High"
            vol_col = "거래량" if "거래량" in df.columns else "Volume"
            val_col = "거래대금" if "거래대금" in df.columns else "Value"

            close = df[close_col]
            high = df[high_col]
            vol = df[vol_col]

            close_today = float(close.iloc[-1])
            prev_close = float(close.iloc[-2])
            prior_window_high = high.iloc[-(lookback_n + 1):-1]
            prior_high_n = float(prior_window_high.max()) if len(prior_window_high) >= 1 else None

            vol_today = float(vol.iloc[-1])
            prior_window_vol = vol.iloc[-(lookback_n + 1):-1]
            prior_avg_vol_n = float(prior_window_vol.mean()) if len(prior_window_vol) >= 1 else None

            if val_col in df.columns:
                value = df[val_col]
                adv_window = value.iloc[-(lookback_n + 1):-1]
                adv_value_n = float(adv_window.mean()) if len(adv_window) >= 1 else None
            else:
                approx = close * vol
                adv_window = approx.iloc[-(lookback_n + 1):-1]
                adv_value_n = float(adv_window.mean()) if len(adv_window) >= 1 else None

            ret_today = ((close_today / prev_close) - 1) * 100 if prev_close > 0 else None

            return {
                "close_today": close_today,
                "prior_high_n": prior_high_n,
                "vol_today": vol_today,
                "prior_avg_vol_n": prior_avg_vol_n,
                "adv_value_n": adv_value_n,
                "ret_today": ret_today,
            }
        except Exception:
            logger.exception("v86 pykrx fetch failed for %s", ticker)
            return {}

    return await asyncio.to_thread(_fetch)


class VolumeBreakoutFeed:
    """v86: 거래량 폭증 + N-day high 돌파 polling strategy (LLM-free)."""

    def __init__(
        self,
        config: Config,
        *,
        stop_event: Optional[asyncio.Event] = None,
        hist_fetcher: Optional[Callable[[str, int], Awaitable[dict]]] = None,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._stop_event = stop_event
        self._hist_fetcher = hist_fetcher or _default_pykrx_breakout
        self._sleep = sleep_fn
        self._monotonic = monotonic_fn
        self._tickers = tuple(config.volume_breakout_feed_tickers)
        self._enabled = bool(config.volume_breakout_feed_enabled and self._tickers)
        self._stopped = False
        self._last_emitted_at: dict[str, float] = {}

    @property
    def name(self) -> str:
        return "volume_breakout"

    @property
    def source(self) -> SignalSource:
        return SignalSource.TECHNICAL

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def start(self) -> None:
        logger.info("VolumeBreakoutFeed starting (tickers=%d)", len(self._tickers))

    async def stop(self) -> None:
        self._stopped = True
        logger.info("VolumeBreakoutFeed stopping")

    def _should_stop(self) -> bool:
        return self._stopped or (self._stop_event is not None and self._stop_event.is_set())

    def _cooldown_active(self, ticker: str) -> bool:
        cooldown = self._config.volume_breakout_feed_signal_cooldown_s
        if cooldown <= 0:
            return False
        last = self._last_emitted_at.get(ticker)
        if last is None:
            return False
        return (self._monotonic() - last) < cooldown

    async def _load_snapshot(self, ticker: str) -> BreakoutSnapshot | None:
        lookback = self._config.volume_breakout_feed_lookback_n
        feats = await self._hist_fetcher(ticker, lookback)
        if not feats:
            return None
        return BreakoutSnapshot(
            ticker=ticker,
            corp_name=ticker,
            close_today=feats.get("close_today") or 0.0,
            prior_high_n=feats.get("prior_high_n"),
            vol_today=feats.get("vol_today"),
            prior_avg_vol_n=feats.get("prior_avg_vol_n"),
            adv_value_n=feats.get("adv_value_n"),
            ret_today=feats.get("ret_today"),
        )

    def _qualifies(self, snap: BreakoutSnapshot) -> bool:
        if snap.prior_high_n is None or snap.prior_avg_vol_n is None or snap.adv_value_n is None:
            return False
        if snap.vol_today is None or snap.ret_today is None:
            return False
        if snap.close_today <= 0 or snap.prior_high_n <= 0 or snap.prior_avg_vol_n <= 0:
            return False
        if snap.adv_value_n < self._config.volume_breakout_feed_min_adv_value:
            return False
        if snap.close_today <= snap.prior_high_n:
            return False
        vol_ratio = snap.vol_today / snap.prior_avg_vol_n
        if vol_ratio < self._config.volume_breakout_feed_min_vol_ratio:
            return False
        if snap.ret_today < self._config.volume_breakout_feed_min_ret_today:
            return False
        if snap.ret_today > self._config.volume_breakout_feed_max_ret_today:
            return False
        if self._cooldown_active(snap.ticker):
            return False
        return True

    def _confidence(self, snap: BreakoutSnapshot) -> int:
        base = 70
        vol_ratio = (snap.vol_today or 0.0) / max(snap.prior_avg_vol_n or 1.0, 1.0)
        # vol_ratio 가 threshold 의 1x → 0, 2x → +6, 3x+ → +10
        threshold = self._config.volume_breakout_feed_min_vol_ratio
        vol_bonus = min(int((vol_ratio - threshold) * 3), 10)
        # breakout strength: close 가 prior high 대비 얼마나 높은지
        prior_high = snap.prior_high_n or snap.close_today
        breakout_pct = (snap.close_today / prior_high - 1) * 100 if prior_high > 0 else 0.0
        breakout_bonus = min(int(breakout_pct * 2), 5)
        return max(0, min(90, base + max(0, vol_bonus) + max(0, breakout_bonus)))

    def _size_hint(self, confidence: int) -> SizeHint:
        if confidence >= 82:
            return SizeHint.M
        return SizeHint.S

    def _build_signal(self, snap: BreakoutSnapshot) -> TradeSignal:
        confidence = self._confidence(snap)
        vol_ratio = (snap.vol_today or 0.0) / max(snap.prior_avg_vol_n or 1.0, 1.0)
        reason = (
            f"v86 breakout close={snap.close_today:.0f} > prior_high_{self._config.volume_breakout_feed_lookback_n}d="
            f"{snap.prior_high_n:.0f} vol_ratio={vol_ratio:.2f}x ret_today={snap.ret_today:.2f}%"
        )
        return TradeSignal(
            strategy_name=self.name,
            source=self.source,
            ticker=snap.ticker,
            corp_name=snap.corp_name,
            action=Action.BUY,
            confidence=confidence,
            size_hint=self._size_hint(confidence),
            reason=reason,
            detected_at=datetime.now(_KST),
            metadata={
                "close_today": snap.close_today,
                "prior_high_n": snap.prior_high_n,
                "vol_today": snap.vol_today,
                "prior_avg_vol_n": snap.prior_avg_vol_n,
                "vol_ratio": round(vol_ratio, 3),
                "ret_today": snap.ret_today,
                "adv_value_n": snap.adv_value_n,
            },
        )

    async def scan_once(self) -> list[TradeSignal]:
        if not self.enabled:
            return []
        signals: list[TradeSignal] = []
        for ticker in self._tickers:
            try:
                snap = await self._load_snapshot(ticker)
            except Exception:
                logger.exception("v86 snapshot failed for %s", ticker)
                continue
            if snap is None or not self._qualifies(snap):
                continue
            signals.append(self._build_signal(snap))
            self._last_emitted_at[snap.ticker] = self._monotonic()
        return signals

    async def stream_signals(self) -> AsyncIterator[TradeSignal]:
        if not self.enabled:
            return
            yield  # pragma: no cover

        while not self._should_stop():
            for signal in await self.scan_once():
                yield signal
            if self._should_stop():
                break
            await self._sleep(self._config.volume_breakout_feed_poll_interval_s)
