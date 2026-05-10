from __future__ import annotations

import asyncio
import logging
from datetime import datetime, date, timedelta
from typing import AsyncIterator, Optional

from kindshot.config import Config
from kindshot.models import Action, SizeHint
from kindshot.strategy import SignalSource, TradeSignal
from kindshot.tz import KST as _KST

logger = logging.getLogger(__name__)

class RebalanceFeed:
    """Institutional Rebalancing & Index Flow Strategy.
    
    Detects KOSPI 200 / KOSDAQ 150 rebalancing events (June/December)
    and generates BUY signals for added tickers during the T-5 to T-0 window.
    """
    
    def __init__(self, config: Config) -> None:
        self._config = config
        self._stop_event = asyncio.Event()
        self._last_poll_at: Optional[datetime] = None
        self._seen_event_ids: set[str] = set()

    @property
    def name(self) -> str:
        return "rebalance"

    @property
    def source(self) -> SignalSource:
        # Use COMPOSITE as a placeholder for Institutional/Flow signals
        return SignalSource.COMPOSITE

    @property
    def enabled(self) -> bool:
        return getattr(self._config, "rebalance_feed_enabled", True)

    async def start(self) -> None:
        logger.info("RebalanceFeed starting")

    async def stop(self) -> None:
        self._stop_event.set()

    def _get_rebalance_day(self, year: int, month: int) -> date:
        """Calculate the 2nd Thursday of the month (Option/Futures Expiration & Rebalance Day)."""
        # Start at the 1st day of the month
        d = date(year, month, 1)
        # Find first Thursday (weekday 3)
        while d.weekday() != 3:
            d += timedelta(days=1)
        # 2nd Thursday
        return d + timedelta(days=7)

    def _is_in_window(self, now: datetime) -> bool:
        """Check if current date is within T-5 to T-0 of a rebalance event."""
        if now.month not in (6, 12):
            return False
            
        rebalance_day = self._get_rebalance_day(now.year, now.month)
        # Window: T-5 (inclusive) to T-0 (inclusive)
        start_window = rebalance_day - timedelta(days=5)
        return start_window <= now.date() <= rebalance_day

    async def _fetch_target_tickers(self) -> list[dict]:
        """Fetch tickers to be added to the index.
        In a full implementation, this would scrape KRX or read from a validated research file.
        """
        # Placeholder for dynamic loading (e.g. from data/rebalance_candidates.json)
        # For now, return an empty list until the research loop populates the candidates.
        return []

    async def poll_once(self) -> list[TradeSignal]:
        self._last_poll_at = datetime.now(_KST)
        
        if not self._is_in_window(self._last_poll_at):
            return []
            
        targets = await self._fetch_target_tickers()
        signals = []
        
        base_confidence = getattr(self._config, "rebalance_feed_base_confidence", 75)
        
        for t in targets:
            ticker = t.get('ticker')
            if not ticker:
                continue
                
            event_id = f"rebalance_{self._last_poll_at.strftime('%Y%m')}_{ticker}"
            if event_id in self._seen_event_ids:
                continue
                
            signals.append(TradeSignal(
                strategy_name=self.name,
                source=self.source,
                ticker=ticker,
                corp_name=t.get('corp_name', ticker),
                action=Action.BUY,
                confidence=base_confidence,
                size_hint=SizeHint.M,
                reason=f"Index Rebalancing Addition Flow (T-window: {self._last_poll_at.date()})",
                headline=f"[REBALANCE] {t.get('corp_name', ticker)} Index Addition Flow",
                event_id=event_id,
                detected_at=self._last_poll_at
            ))
            self._seen_event_ids.add(event_id)
            
        return signals

    async def stream_signals(self) -> AsyncIterator[TradeSignal]:
        """Main loop yielding signals."""
        while not self._stop_event.is_set():
            if self.enabled:
                try:
                    for signal in await self.poll_once():
                        yield signal
                except Exception:
                    logger.exception("Error in RebalanceFeed polling")
            
            # Poll every 4 hours for rebalancing events
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=14400)
            except asyncio.TimeoutError:
                pass
