"""Price fetching and snapshot scheduling."""

from __future__ import annotations

import asyncio
import heapq
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

from kindshot.config import Config
from kindshot.kis_client import KisClient, PriceInfo
from kindshot.logger import JsonlLogger
from kindshot.models import PriceSnapshot, T0Basis

logger = logging.getLogger(__name__)

# Horizon offsets in seconds from t0
HORIZON_OFFSETS: dict[str, float] = {
    "t+1m": 60,
    "t+5m": 300,
    "t+30m": 1800,
}


@dataclass
class PriceFetcher:
    """Fetches price from KIS or returns UNAVAILABLE."""

    kis: Optional[KisClient]

    async def fetch(self, ticker: str) -> Optional[PriceInfo]:
        if self.kis is None:
            return None
        return await self.kis.get_price(ticker)


@dataclass(order=True)
class ScheduledSnapshot:
    fire_at: float
    event_id: str = field(compare=False)
    ticker: str = field(compare=False)
    horizon: str = field(compare=False)
    t0_basis: T0Basis = field(compare=False)
    t0_ts: datetime = field(compare=False)
    t0_px: Optional[float] = field(compare=False, default=None)
    t0_cum_value: Optional[float] = field(compare=False, default=None)
    run_id: str = field(compare=False, default="")
    schema_version: str = field(compare=False, default="0.1.2")


class SnapshotScheduler:
    """Schedules and fires price snapshots at t0, t+1m, t+5m, t+30m, close."""

    def __init__(
        self,
        config: Config,
        fetcher: PriceFetcher,
        log: JsonlLogger,
    ) -> None:
        self._config = config
        self._fetcher = fetcher
        self._logger = log
        self._heap: list[ScheduledSnapshot] = []
        self._running = True
        # Track t0 prices per event_id for return calculation
        self._t0_prices: dict[str, tuple[Optional[float], Optional[float]]] = {}

    def schedule_t0(
        self,
        event_id: str,
        ticker: str,
        t0_basis: T0Basis,
        t0_ts: datetime,
        run_id: str,
    ) -> None:
        """Schedule t0 snapshot immediately + future horizons."""
        now = time.monotonic()

        # t0: fire immediately (will be fetched in the run loop)
        heapq.heappush(self._heap, ScheduledSnapshot(
            fire_at=now,
            event_id=event_id,
            ticker=ticker,
            horizon="t0",
            t0_basis=t0_basis,
            t0_ts=t0_ts,
            run_id=run_id,
            schema_version=self._config.schema_version,
        ))

        # Future horizons
        for horizon, offset_s in HORIZON_OFFSETS.items():
            heapq.heappush(self._heap, ScheduledSnapshot(
                fire_at=now + offset_s,
                event_id=event_id,
                ticker=ticker,
                horizon=horizon,
                t0_basis=t0_basis,
                t0_ts=t0_ts,
                run_id=run_id,
                schema_version=self._config.schema_version,
            ))

        # Close snapshot: 15:30 KST + close_snapshot_delay_s (default 300s = 15:35)
        kst = timezone(timedelta(hours=9))
        now_kst = datetime.now(kst)
        market_close = now_kst.replace(hour=15, minute=30, second=0, microsecond=0)
        close_fire_kst = market_close + timedelta(seconds=self._config.close_snapshot_delay_s)
        seconds_until_close = max(0, (close_fire_kst - now_kst).total_seconds())
        heapq.heappush(self._heap, ScheduledSnapshot(
            fire_at=now + seconds_until_close,
            event_id=event_id,
            ticker=ticker,
            horizon="close",
            t0_basis=t0_basis,
            t0_ts=t0_ts,
            run_id=run_id,
            schema_version=self._config.schema_version,
        ))

    async def _fire(self, snap: ScheduledSnapshot) -> None:
        """Execute a single snapshot fetch and log."""
        price = await self._fetcher.fetch(snap.ticker)

        px: Optional[float] = None
        spread_bps: Optional[float] = None
        cum_value: Optional[float] = None
        latency_ms: Optional[int] = None
        price_source: Optional[str] = None

        if price:
            px = price.px
            spread_bps = price.spread_bps
            cum_value = price.cum_value
            latency_ms = price.fetch_latency_ms
            price_source = "KIS_REST"

        # Calculate returns vs t0
        ret_long: Optional[float] = None
        ret_short: Optional[float] = None
        value_since: Optional[float] = None

        if snap.horizon == "t0":
            ret_long = 0.0
            ret_short = 0.0
            value_since = 0
            # Store t0 values for future snapshots
            self._t0_prices[snap.event_id] = (px, cum_value)
        else:
            t0_px, t0_cum = self._t0_prices.get(snap.event_id, (None, None))
            if px is not None and t0_px and t0_px > 0:
                ret_long = (px - t0_px) / t0_px
                ret_short = -ret_long
                if cum_value is not None and t0_cum is not None:
                    value_since = cum_value - t0_cum

        record = PriceSnapshot(
            schema_version=snap.schema_version,
            run_id=snap.run_id,
            event_id=snap.event_id,
            horizon=snap.horizon,
            ts=datetime.now(timezone.utc),
            t0_basis=snap.t0_basis,
            t0_ts=snap.t0_ts,
            px=px,
            cum_value=cum_value,
            ret_long_vs_t0=ret_long,
            ret_short_vs_t0=ret_short,
            value_since_t0=value_since,
            spread_bps=spread_bps,
            price_source=price_source,
            snapshot_fetch_latency_ms=latency_ms,
        )

        await self._logger.write(record)

    async def run(self) -> None:
        """Main loop: fire snapshots as they become due."""
        while self._running:
            now = time.monotonic()

            while self._heap and self._heap[0].fire_at <= now:
                snap = heapq.heappop(self._heap)
                try:
                    await self._fire(snap)
                except Exception:
                    logger.exception("Snapshot fire failed: %s/%s", snap.event_id, snap.horizon)

            await asyncio.sleep(1.0)

    def stop(self) -> None:
        self._running = False

    @property
    def pending_count(self) -> int:
        return len(self._heap)
