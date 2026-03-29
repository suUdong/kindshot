"""Polling TA strategy built on the Strategy protocol."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator, Awaitable, Callable, Optional

from kindshot.config import Config
from kindshot.context_card import _pykrx_features
from kindshot.kis_client import KisClient
from kindshot.models import Action, SizeHint
from kindshot.mtf_analysis import MtfResult, analyze_mtf
from kindshot.strategy import SignalSource, TradeSignal
from kindshot.tz import KST as _KST

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TechnicalSnapshot:
    ticker: str
    corp_name: str
    ret_today: float | None
    volume_ratio_vs_avg20d: float | None
    rsi_14: float | None
    macd_hist: float | None
    bb_position: float | None
    atr_14: float | None
    mtf_alignment_score: int
    mtf_detail: str
    sector: str = ""


class TechnicalStrategy:
    """Conservative momentum TA strategy for explicit watchlist tickers."""

    def __init__(
        self,
        config: Config,
        kis: Optional[KisClient],
        *,
        stop_event: Optional[asyncio.Event] = None,
        hist_fetcher: Callable[[str], Awaitable[dict]] = _pykrx_features,
        mtf_analyzer: Callable[[str, KisClient, Config], Awaitable[MtfResult]] = analyze_mtf,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._kis = kis
        self._stop_event = stop_event
        self._hist_fetcher = hist_fetcher
        self._mtf_analyzer = mtf_analyzer
        self._sleep = sleep_fn
        self._monotonic = monotonic_fn
        self._tickers = config.technical_strategy_tickers
        self._enabled = bool(config.technical_strategy_enabled and self._tickers and kis is not None)
        self._stopped = False
        self._last_emitted_at: dict[str, float] = {}

    @property
    def name(self) -> str:
        return "technical"

    @property
    def source(self) -> SignalSource:
        return SignalSource.TECHNICAL

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def start(self) -> None:
        logger.info("TechnicalStrategy starting (tickers=%d)", len(self._tickers))

    async def stop(self) -> None:
        self._stopped = True
        logger.info("TechnicalStrategy stopping")

    def _should_stop(self) -> bool:
        return self._stopped or (self._stop_event is not None and self._stop_event.is_set())

    async def _load_snapshot(self, ticker: str) -> TechnicalSnapshot | None:
        if self._kis is None:
            return None

        hist, price_info, mtf = await asyncio.gather(
            self._hist_fetcher(ticker),
            self._kis.get_price(ticker),
            self._mtf_analyzer(ticker, self._kis, self._config),
        )

        prev_close = hist.get("prev_close")
        ret_today = None
        volume_ratio_vs_avg20d = None
        sector = ""
        if price_info is not None:
            sector = price_info.sector or ""
            if prev_close and prev_close > 0:
                ret_today = round(((price_info.px / prev_close) - 1) * 100, 2)
            avg_volume_20d = hist.get("avg_volume_20d")
            if (
                price_info.cum_volume is not None
                and price_info.cum_volume > 0
                and avg_volume_20d
                and avg_volume_20d > 0
            ):
                volume_ratio_vs_avg20d = round(price_info.cum_volume / avg_volume_20d, 4)

        return TechnicalSnapshot(
            ticker=ticker,
            corp_name=ticker,
            ret_today=ret_today,
            volume_ratio_vs_avg20d=volume_ratio_vs_avg20d,
            rsi_14=hist.get("rsi_14"),
            macd_hist=hist.get("macd_hist"),
            bb_position=hist.get("bb_position"),
            atr_14=hist.get("atr_14"),
            mtf_alignment_score=mtf.alignment_score,
            mtf_detail=mtf.detail,
            sector=sector,
        )

    def _cooldown_active(self, ticker: str) -> bool:
        cooldown = self._config.technical_strategy_signal_cooldown_s
        if cooldown <= 0:
            return False
        last_emitted_at = self._last_emitted_at.get(ticker)
        if last_emitted_at is None:
            return False
        return (self._monotonic() - last_emitted_at) < cooldown

    def _qualifies(self, snapshot: TechnicalSnapshot) -> bool:
        if snapshot.rsi_14 is None or snapshot.macd_hist is None or snapshot.bb_position is None:
            return False
        if snapshot.ret_today is None or snapshot.volume_ratio_vs_avg20d is None:
            return False
        if snapshot.mtf_alignment_score < self._config.technical_strategy_min_alignment_score:
            return False
        if snapshot.rsi_14 < self._config.technical_strategy_min_rsi:
            return False
        if snapshot.rsi_14 > self._config.technical_strategy_max_rsi:
            return False
        if snapshot.macd_hist < self._config.technical_strategy_min_macd_hist:
            return False
        if snapshot.bb_position > self._config.technical_strategy_max_bb_position:
            return False
        if snapshot.volume_ratio_vs_avg20d < self._config.technical_strategy_min_volume_ratio_vs_avg20d:
            return False
        if snapshot.ret_today < self._config.technical_strategy_min_ret_today:
            return False
        if self._cooldown_active(snapshot.ticker):
            return False
        return True

    def _confidence(self, snapshot: TechnicalSnapshot) -> int:
        confidence = 55
        confidence += max(0, snapshot.mtf_alignment_score - self._config.technical_strategy_min_alignment_score) // 2
        if snapshot.rsi_14 is not None and snapshot.rsi_14 >= 60:
            confidence += 4
        if snapshot.volume_ratio_vs_avg20d is not None and snapshot.volume_ratio_vs_avg20d >= (self._config.technical_strategy_min_volume_ratio_vs_avg20d * 2):
            confidence += 6
        if snapshot.ret_today is not None and snapshot.ret_today >= 1.0:
            confidence += 4
        if snapshot.bb_position is not None and snapshot.bb_position >= 75:
            confidence -= 3
        return max(0, min(90, int(confidence)))

    def _size_hint(self, snapshot: TechnicalSnapshot, confidence: int) -> SizeHint:
        if (
            confidence >= 78
            and snapshot.volume_ratio_vs_avg20d is not None
            and snapshot.volume_ratio_vs_avg20d >= (self._config.technical_strategy_min_volume_ratio_vs_avg20d * 2)
        ):
            return SizeHint.M
        return SizeHint.S

    def _build_signal(self, snapshot: TechnicalSnapshot) -> TradeSignal:
        confidence = self._confidence(snapshot)
        reason = (
            f"mtf={snapshot.mtf_alignment_score} "
            f"rsi={snapshot.rsi_14:.1f} "
            f"macd={snapshot.macd_hist:.2f} "
            f"bb={snapshot.bb_position:.1f} "
            f"vol={snapshot.volume_ratio_vs_avg20d:.2f} "
            f"ret={snapshot.ret_today:.2f}%"
        )
        return TradeSignal(
            strategy_name=self.name,
            source=self.source,
            ticker=snapshot.ticker,
            corp_name=snapshot.corp_name,
            action=Action.BUY,
            confidence=confidence,
            size_hint=self._size_hint(snapshot, confidence),
            reason=reason,
            detected_at=datetime.now(_KST),
            metadata={
                "sector": snapshot.sector,
                "rsi_14": snapshot.rsi_14,
                "macd_hist": snapshot.macd_hist,
                "bb_position": snapshot.bb_position,
                "atr_14": snapshot.atr_14,
                "ret_today": snapshot.ret_today,
                "volume_ratio_vs_avg20d": snapshot.volume_ratio_vs_avg20d,
                "mtf_alignment_score": snapshot.mtf_alignment_score,
                "mtf_detail": snapshot.mtf_detail,
            },
        )

    async def scan_once(self) -> list[TradeSignal]:
        if not self.enabled:
            return []

        signals: list[TradeSignal] = []
        for ticker in self._tickers:
            try:
                snapshot = await self._load_snapshot(ticker)
            except Exception:
                logger.exception("Technical strategy snapshot failed for %s", ticker)
                continue
            if snapshot is None or not self._qualifies(snapshot):
                continue
            signals.append(self._build_signal(snapshot))
            self._last_emitted_at[ticker] = self._monotonic()
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
            await self._sleep(self._config.technical_strategy_poll_interval_s)
