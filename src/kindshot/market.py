"""Market environment check: KOSPI/KOSDAQ halt + macro snapshot."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from kindshot.config import Config
from kindshot.kis_client import KisClient
from kindshot.models import MarketContext

logger = logging.getLogger(__name__)


async def _fetch_vkospi() -> Optional[float]:
    """Fetch today's VKOSPI close from pykrx (blocking I/O in thread)."""

    def _fetch() -> Optional[float]:
        try:
            from pykrx import stock

            _KST = timezone(timedelta(hours=9))
            today = datetime.now(_KST).strftime("%Y%m%d")
            df = stock.get_index_ohlcv(today, today, "1004")
            if df.empty:
                return None
            return float(df["종가"].iloc[-1])
        except Exception:
            logger.exception("pykrx VKOSPI fetch failed")
            return None

    return await asyncio.to_thread(_fetch)


class MarketMonitor:
    """Monitors KOSPI/KOSDAQ for halt condition and captures macro snapshot.

    When KIS is unavailable, market check is disabled (always allows trading).
    Operator should monitor manually in that case.
    """

    def __init__(self, config: Config, kis: Optional[KisClient] = None) -> None:
        self._config = config
        self._kis = kis
        self._halted = False
        self._kospi_change: Optional[float] = None
        self._kosdaq_change: Optional[float] = None
        self._vkospi: Optional[float] = None
        self._last_ts: Optional[datetime] = None

    @property
    def is_halted(self) -> bool:
        return self._halted

    @property
    def enabled(self) -> bool:
        return self._kis is not None

    @property
    def snapshot(self) -> MarketContext:
        """Return latest macro snapshot for logging."""
        return MarketContext(
            kospi_change_pct=self._kospi_change,
            kosdaq_change_pct=self._kosdaq_change,
            vkospi=self._vkospi,
        )

    async def update(self) -> None:
        """Check KOSPI/KOSDAQ and update halt status + macro snapshot."""
        # KOSPI + KOSDAQ via KIS (parallel)
        if self._kis:
            kospi_task = self._kis.get_index_change("0001")
            kosdaq_task = self._kis.get_index_change("2001")
            kospi, kosdaq = await asyncio.gather(kospi_task, kosdaq_task)

            if kospi is not None:
                self._kospi_change = kospi
                was_halted = self._halted
                self._halted = kospi <= self._config.kospi_halt_pct
                if self._halted and not was_halted:
                    logger.warning("MARKET HALT: KOSPI %.2f%% <= %.1f%%", kospi, self._config.kospi_halt_pct)
                elif not self._halted and was_halted:
                    logger.info("Market halt lifted: KOSPI %.2f%%", kospi)

            if kosdaq is not None:
                self._kosdaq_change = kosdaq

        # VKOSPI via pykrx (independent)
        try:
            vkospi = await _fetch_vkospi()
            if vkospi is not None:
                self._vkospi = vkospi
        except Exception:
            logger.exception("VKOSPI update failed")

        self._last_ts = datetime.now(timezone.utc)
