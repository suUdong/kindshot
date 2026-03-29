"""공매도 과열 해제 D+2 평균회귀 매매 전략.

KRX에서 공매도 과열종목 해제 데이터를 폴링하고,
해제일로부터 D+2 영업일에 BUY 시그널을 생성한다.

평균회귀 논리: 과열 지정 기간 동안 공매도 금지 → 해제 후
숏 포지션 재진입 압력이 완화되며 가격이 회복되는 패턴.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import AsyncIterator, Optional

import aiohttp

from kindshot.config import Config
from kindshot.krx_short_overheating import (
    OverheatingRecord,
    calc_entry_date,
    fetch_overheating_records,
    filter_released,
    score_overheating_confidence,
)
from kindshot.models import Action, SizeHint
from kindshot.strategy import SignalSource, TradeSignal
from kindshot.tz import KST as _KST

logger = logging.getLogger(__name__)


def _size_hint(confidence: int) -> SizeHint:
    if confidence >= 85:
        return SizeHint.L
    if confidence >= 75:
        return SizeHint.M
    return SizeHint.S


class ShortOverheatingStrategy:
    """공매도 과열 해제 D+2 매수 전략.

    Strategy 프로토콜 구현. 폴링 패턴으로 KRX를 주기적으로 조회하고,
    오늘이 D+2 진입일인 종목에 대해 BUY 시그널을 생성한다.
    """

    def __init__(
        self,
        config: Config,
        session: aiohttp.ClientSession,
        *,
        stop_event: Optional[asyncio.Event] = None,
    ) -> None:
        self._config = config
        self._session = session
        self._stop_event = stop_event or asyncio.Event()
        self._enabled = config.short_overheating_enabled
        self._signaled: set[str] = set()  # "ticker_releasedate" 중복 방지

    @property
    def name(self) -> str:
        return "short_overheating"

    @property
    def source(self) -> SignalSource:
        return SignalSource.TECHNICAL

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def start(self) -> None:
        logger.info("ShortOverheatingStrategy started (enabled=%s)", self._enabled)

    async def stop(self) -> None:
        logger.info("ShortOverheatingStrategy stopping (signaled=%d)", len(self._signaled))

    async def stream_signals(self) -> AsyncIterator[TradeSignal]:
        """주기적으로 KRX를 폴링하고 D+2 진입 시그널 생성."""
        poll_interval = self._config.short_overheating_poll_interval_s

        while not self._stop_event.is_set():
            try:
                signals = await self._poll_once()
                for signal in signals:
                    yield signal
            except asyncio.CancelledError:
                return
            except Exception:
                logger.warning("ShortOverheating poll failed", exc_info=True)

            # 다음 폴링까지 대기
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=poll_interval)
                return  # stop_event set
            except asyncio.TimeoutError:
                pass

    async def _poll_once(self) -> list[TradeSignal]:
        """1회 폴링: KRX 조회 → D+2 필터 → 시그널 생성."""
        today = datetime.now(_KST).date()
        lookback = self._config.short_overheating_lookback_days
        start_date = today - timedelta(days=lookback)

        records = await fetch_overheating_records(
            self._session, start_date, today,
        )
        if not records:
            logger.debug("ShortOverheating: no records from KRX")
            return []

        released = filter_released(records)
        logger.info("ShortOverheating: %d released records in lookback", len(released))

        signals: list[TradeSignal] = []
        for record in released:
            if not self._is_entry_today(record, today):
                continue
            if record.overheating_days < self._config.short_overheating_min_overheating_days:
                continue
            signal = self._build_signal(record, drop_pct=0.0)
            signals.append(signal)
            self._signaled.add(self._signal_key(record))
            logger.info(
                "ShortOverheating signal: %s %s (confidence=%d, days=%d)",
                record.ticker, record.corp_name, signal.confidence, record.overheating_days,
            )

        return signals

    def _is_entry_today(self, record: OverheatingRecord, today: date) -> bool:
        """오늘이 해당 레코드의 D+N 진입일인지 판별."""
        if self._signal_key(record) in self._signaled:
            return False
        entry = calc_entry_date(record.release_date, self._config.short_overheating_d_offset)
        return entry == today

    def _build_signal(self, record: OverheatingRecord, drop_pct: float = 0.0) -> TradeSignal:
        """OverheatingRecord → TradeSignal 변환."""
        confidence = score_overheating_confidence(
            overheating_days=record.overheating_days,
            drop_pct=drop_pct,
            base=self._config.short_overheating_base_confidence,
        )
        reason = f"공매도 과열 해제 D+{self._config.short_overheating_d_offset} ({record.overheating_days}일 지정)"
        return TradeSignal(
            strategy_name="short_overheating",
            source=SignalSource.TECHNICAL,
            ticker=record.ticker,
            corp_name=record.corp_name,
            action=Action.BUY,
            confidence=confidence,
            size_hint=_size_hint(confidence),
            reason=reason,
            headline=f"공매도 과열 해제: {record.corp_name}",
            event_id=f"soh_{record.ticker}_{record.release_date.strftime('%Y%m%d')}",
            detected_at=datetime.now(_KST),
            metadata={
                "overheating_days": record.overheating_days,
                "designation_date": record.designation_date.isoformat(),
                "release_date": record.release_date.isoformat(),
                "drop_pct": drop_pct,
            },
        )

    @staticmethod
    def _signal_key(record: OverheatingRecord) -> str:
        return f"{record.ticker}_{record.release_date.strftime('%Y%m%d')}"
