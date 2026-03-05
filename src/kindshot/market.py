"""Market environment check: KOSPI -1% halt rule."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from kindshot.config import Config
from kindshot.kis_client import KisClient

logger = logging.getLogger(__name__)


class MarketMonitor:
    """Monitors KOSPI for halt condition.

    When KIS is unavailable, market check is disabled (always allows trading).
    Operator should monitor manually in that case.
    """

    def __init__(self, config: Config, kis: Optional[KisClient] = None) -> None:
        self._config = config
        self._kis = kis
        self._halted = False
        self._last_check: Optional[float] = None
        self._kospi_change: Optional[float] = None

    @property
    def is_halted(self) -> bool:
        return self._halted

    @property
    def enabled(self) -> bool:
        return self._kis is not None

    async def update(self) -> None:
        """Check KOSPI and update halt status."""
        if not self._kis:
            return

        change = await self._kis.get_kospi_index()
        if change is not None:
            self._kospi_change = change
            was_halted = self._halted
            self._halted = change <= self._config.kospi_halt_pct
            if self._halted and not was_halted:
                logger.warning("MARKET HALT: KOSPI %.2f%% <= %.1f%%", change, self._config.kospi_halt_pct)
            elif not self._halted and was_halted:
                logger.info("Market halt lifted: KOSPI %.2f%%", change)
