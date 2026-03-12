"""Market environment check: KOSPI/KOSDAQ halt + macro snapshot."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from kindshot.config import Config
from kindshot.kis_client import IndexInfo, KisClient
from kindshot.models import MarketContext

logger = logging.getLogger(__name__)


async def _fetch_vkospi() -> Optional[float]:
    """VKOSPI fetch stub. pykrx removed (KRX blocks AWS IPs).

    VKOSPI is optional context data; returning None is safe.
    """
    return None


class MarketMonitor:
    """Monitors KOSPI/KOSDAQ for halt condition and captures macro snapshot.

    When KIS is unavailable, market monitor cannot initialize and
    is_halted remains True (fail-close). Trading is blocked until
    KIS credentials are provided and the first update succeeds.
    """

    _MAX_INIT_FAILURES = 5  # After this many consecutive failures, force-initialize

    def __init__(self, config: Config, kis: Optional[KisClient] = None) -> None:
        self._config = config
        self._kis = kis
        self._halted = True  # fail-close: block trading until first successful update
        self._initialized = False
        self._init_failures = 0
        self._kospi_change: Optional[float] = None
        self._kosdaq_change: Optional[float] = None
        self._kospi_breadth_ratio: Optional[float] = None
        self._kosdaq_breadth_ratio: Optional[float] = None
        self._vkospi: Optional[float] = None
        self._last_ts: Optional[datetime] = None

    @property
    def is_halted(self) -> bool:
        if not self._initialized:
            return True
        return self._halted

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def enabled(self) -> bool:
        return self._kis is not None

    @property
    def snapshot(self) -> MarketContext:
        """Return latest macro snapshot for logging."""
        return MarketContext(
            kospi_change_pct=self._kospi_change,
            kosdaq_change_pct=self._kosdaq_change,
            kospi_breadth_ratio=self._kospi_breadth_ratio,
            kosdaq_breadth_ratio=self._kosdaq_breadth_ratio,
            vkospi=self._vkospi,
        )

    @staticmethod
    def _breadth_ratio(info: Optional[IndexInfo]) -> Optional[float]:
        if info is None or info.up_issue_count is None or info.down_issue_count is None:
            return None
        if info.down_issue_count <= 0:
            return float(info.up_issue_count) if info.up_issue_count > 0 else 1.0
        return round(info.up_issue_count / info.down_issue_count, 3)

    async def update(self) -> None:
        """Check KOSPI/KOSDAQ and update halt status + macro snapshot."""
        # KOSPI + KOSDAQ via KIS (parallel)
        if self._kis:
            kospi_task = self._kis.get_index_info("0001")
            kosdaq_task = self._kis.get_index_info("2001")
            kospi_info, kosdaq_info = await asyncio.gather(kospi_task, kosdaq_task)
            kospi = kospi_info.change_pct if kospi_info is not None else None
            kosdaq = kosdaq_info.change_pct if kosdaq_info is not None else None
            self._kospi_breadth_ratio = self._breadth_ratio(kospi_info)
            self._kosdaq_breadth_ratio = self._breadth_ratio(kosdaq_info)

            if kospi is not None:
                self._kospi_change = kospi
                self._init_failures = 0
                was_halted = self._halted
                self._halted = kospi <= self._config.kospi_halt_pct
                if not self._initialized:
                    self._initialized = True
                    logger.info("Market monitor initialized: KOSPI %.2f%%", kospi)
                if self._halted and not was_halted:
                    logger.warning("MARKET HALT: KOSPI %.2f%% <= %.1f%%", kospi, self._config.kospi_halt_pct)
                elif not self._halted and was_halted:
                    logger.info("Market halt lifted: KOSPI %.2f%%", kospi)
            elif not self._initialized:
                self._init_failures += 1
                logger.warning(
                    "Market monitor init failed (%d/%d): KOSPI index unavailable",
                    self._init_failures, self._MAX_INIT_FAILURES,
                )
                if self._init_failures >= self._MAX_INIT_FAILURES:
                    self._initialized = True
                    self._halted = False
                    logger.warning(
                        "Market monitor force-initialized after %d failures — trading allowed without KOSPI data",
                        self._init_failures,
                    )

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
