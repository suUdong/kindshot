"""Price fetching and snapshot scheduling."""

from __future__ import annotations

import asyncio
import heapq
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from kindshot.config import Config
from kindshot.kis_client import KisClient, PriceInfo
from kindshot.logger import JsonlLogger, LogWriteError
from kindshot.models import PriceSnapshot, T0Basis
from kindshot.runtime_artifacts import update_runtime_artifact_index

from kindshot.tz import KST as _KST

logger = logging.getLogger(__name__)

# Horizon offsets in seconds from t0
HORIZON_OFFSETS: dict[str, float] = {
    "t+30s": 30,
    "t+1m": 60,
    "t+2m": 120,
    "t+5m": 300,
    "t+15m": 900,
    "t+20m": 1200,
    "t+30m": 1800,
}


def _apply_entry_slippage(px: Optional[float], spread_bps: Optional[float], *, mode: str, is_buy_decision: bool) -> Optional[float]:
    """Apply a conservative half-spread entry penalty for paper BUY tracking."""
    if mode != "paper" or not is_buy_decision or px is None or px <= 0 or spread_bps is None or spread_bps <= 0:
        return px
    return px * (1 + (spread_bps / 2) / 10000)


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
    mode: str = field(compare=False, default="live")
    is_buy_decision: bool = field(compare=False, default=False)


class SnapshotScheduler:
    """Schedules and fires price snapshots at t0, t+1m, t+5m, t+30m, close."""

    def __init__(
        self,
        config: Config,
        fetcher: PriceFetcher,
        log: JsonlLogger,
        *,
        stop_event: Optional[asyncio.Event] = None,
        pnl_callback: Optional[object] = None,
    ) -> None:
        self._config = config
        self._fetcher = fetcher
        self._logger = log
        self._heap: list[ScheduledSnapshot] = []
        self._stop_event = stop_event or asyncio.Event()
        self._pnl_callback = pnl_callback  # callable(ticker, pnl_won) for guardrail state
        # Track effective t0 prices per event_id for return calculation
        self._t0_prices: dict[str, tuple[Optional[float], Optional[float]]] = {}
        # Track ticker per event_id for pnl callback
        self._event_tickers: dict[str, str] = {}
        # 가상 익절/손절 추적 (event_id → exit horizon)
        self._virtual_exits: dict[str, str] = {}
        # Trailing stop peak 추적 (event_id → peak return %)
        self._peak_returns: dict[str, float] = {}
        # 진입 시각 추적 (event_id → monotonic time at t0 fire)
        self._entry_times: dict[str, float] = {}
        # 이벤트별 max_hold_minutes (0=EOD까지)
        self._max_hold_minutes: dict[str, int] = {}

    def _get_trailing_stop_pct(self, event_id: str) -> float:
        """시간대별 trailing stop 폭 반환: 0~5분 early, 5~30분 mid, 30분+ late."""
        entry_time = self._entry_times.get(event_id)
        if entry_time is None:
            return self._config.trailing_stop_pct
        elapsed_s = time.monotonic() - entry_time
        if elapsed_s < 300:  # 0~5분
            return self._config.trailing_stop_early_pct
        elif elapsed_s < 1800:  # 5~30분
            return self._config.trailing_stop_mid_pct
        else:  # 30분+
            return self._config.trailing_stop_late_pct

    def _runtime_snapshot_path(self, ts: datetime) -> Path:
        dt = ts.astimezone(_KST).strftime("%Y%m%d")
        return self._config.runtime_price_snapshots_dir / f"{dt}.jsonl"

    async def _append_runtime_price_snapshot(self, record: PriceSnapshot) -> None:
        path = self._runtime_snapshot_path(record.ts)
        line = json.dumps(record.model_dump(mode="json"), ensure_ascii=False)
        kst_date = record.ts.astimezone(_KST).strftime("%Y%m%d")

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

        _write()
        await update_runtime_artifact_index(
            self._config,
            date=kst_date,
            artifact="price_snapshots",
            path=path,
            recorded_at=record.ts,
        )

    def _close_fire_kst(self, now_kst: Optional[datetime] = None) -> datetime:
        base = now_kst or datetime.now(_KST)
        market_close = base.replace(hour=15, minute=30, second=0, microsecond=0)
        return market_close + timedelta(seconds=self._config.close_snapshot_delay_s)

    def schedule_t0(
        self,
        event_id: str,
        ticker: str,
        t0_basis: T0Basis,
        t0_ts: datetime,
        run_id: str,
        mode: str = "live",
        is_buy_decision: bool = False,
        max_hold_minutes: int = 0,
    ) -> None:
        """Schedule t0 snapshot immediately + future horizons."""
        now = time.monotonic()

        self._event_tickers[event_id] = ticker
        if max_hold_minutes > 0:
            self._max_hold_minutes[event_id] = max_hold_minutes

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
            mode=mode,
            is_buy_decision=is_buy_decision,
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
                mode=mode,
                is_buy_decision=is_buy_decision,
            ))

        # Close snapshot: 15:30 KST + close_snapshot_delay_s (default 300s = 15:35)
        now_kst = datetime.now(_KST)
        close_fire_kst = self._close_fire_kst(now_kst)
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
            mode=mode,
            is_buy_decision=is_buy_decision,
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
            # Store effective entry values for future snapshots.
            effective_entry_px = _apply_entry_slippage(
                px,
                spread_bps,
                mode=snap.mode,
                is_buy_decision=snap.is_buy_decision,
            )
            self._t0_prices[snap.event_id] = (effective_entry_px, cum_value)
            self._entry_times[snap.event_id] = time.monotonic()
        else:
            t0_px, t0_cum = self._t0_prices.get(snap.event_id, (None, None))
            if px is not None and t0_px and t0_px > 0:
                ret_long = (px - t0_px) / t0_px
                ret_short = -ret_long
                if cum_value is not None and t0_cum is not None:
                    value_since = cum_value - t0_cum
            # Clean up t0 reference after final snapshot + report P&L
            if snap.horizon == "close":
                self._t0_prices.pop(snap.event_id, None)
                self._event_tickers.pop(snap.event_id, None)
                self._entry_times.pop(snap.event_id, None)
                self._max_hold_minutes.pop(snap.event_id, None)
                # Report close P&L to guardrail state (BUY decisions only)
                if snap.is_buy_decision and ret_long is not None and self._pnl_callback and t0_px:
                    pnl_won = ret_long * self._config.order_size
                    self._pnl_callback(snap.ticker, pnl_won)

        record = PriceSnapshot(
            mode=snap.mode,
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
        await self._append_runtime_price_snapshot(record)

        # 가상 익절/손절/trailing stop 판정 (BUY, t0 이후, 아직 exit 안 한 경우)
        if (
            snap.is_buy_decision
            and snap.horizon != "t0"
            and ret_long is not None
            and snap.event_id not in self._virtual_exits
        ):
            ret_pct = ret_long * 100
            tp_active = self._config.paper_take_profit_pct > 0
            sl_active = self._config.paper_stop_loss_pct < 0

            # Track peak for trailing stop
            if self._config.trailing_stop_enabled:
                prev_peak = self._peak_returns.get(snap.event_id, 0.0)
                self._peak_returns[snap.event_id] = max(prev_peak, ret_pct)
                peak = self._peak_returns[snap.event_id]

            if tp_active and ret_pct >= self._config.paper_take_profit_pct:
                self._virtual_exits[snap.event_id] = snap.horizon
                logger.info(
                    "PAPER TP hit [%s] %s: +%.2f%% at %s (target %.1f%%)",
                    snap.ticker, snap.event_id[:8], ret_pct, snap.horizon,
                    self._config.paper_take_profit_pct,
                )
            elif sl_active and ret_pct <= self._config.paper_stop_loss_pct:
                self._virtual_exits[snap.event_id] = snap.horizon
                logger.info(
                    "PAPER SL hit [%s] %s: %.2f%% at %s (stop %.1f%%)",
                    snap.ticker, snap.event_id[:8], ret_pct, snap.horizon,
                    self._config.paper_stop_loss_pct,
                )
            elif (
                self._config.trailing_stop_enabled
                and peak >= self._config.trailing_stop_activation_pct
                and ret_pct <= peak - self._get_trailing_stop_pct(snap.event_id)
            ):
                trail_pct = self._get_trailing_stop_pct(snap.event_id)
                self._virtual_exits[snap.event_id] = snap.horizon
                logger.info(
                    "PAPER TRAILING STOP [%s] %s: %.2f%% at %s (peak %.2f%%, trail -%.1f%%)",
                    snap.ticker, snap.event_id[:8], ret_pct, snap.horizon,
                    peak, trail_pct,
                )
            else:
                # 이벤트별 또는 전역 max_hold_minutes 체크
                event_max = self._max_hold_minutes.get(snap.event_id, self._config.max_hold_minutes)
                if event_max > 0 and snap.horizon == f"t+{event_max}m":
                    self._virtual_exits[snap.event_id] = snap.horizon
                    logger.info(
                        "PAPER MAX HOLD [%s] %s: %.2f%% at %s (%dm limit)",
                        snap.ticker, snap.event_id[:8], ret_pct, snap.horizon,
                        event_max,
                    )

    async def flush_close_on_shutdown(self) -> int:
        """Fire pending close snapshots if shutdown happens after close fetch time."""
        now_kst = datetime.now(_KST)
        if now_kst < self._close_fire_kst(now_kst):
            return 0

        kept: list[ScheduledSnapshot] = []
        flushed: list[ScheduledSnapshot] = []
        while self._heap:
            snap = heapq.heappop(self._heap)
            if snap.horizon == "close":
                flushed.append(snap)
            else:
                kept.append(snap)
        for snap in kept:
            heapq.heappush(self._heap, snap)

        flushed_count = 0
        for snap in flushed:
            try:
                await self._fire(snap)
                flushed_count += 1
            except LogWriteError:
                raise
            except Exception:
                logger.exception("Shutdown close snapshot flush failed: %s/%s", snap.event_id, snap.horizon)
                heapq.heappush(self._heap, snap)
        return flushed_count

    async def run(self) -> None:
        """Main loop: fire snapshots as they become due."""
        while not self._stop_event.is_set():
            now = time.monotonic()

            while self._heap and self._heap[0].fire_at <= now:
                snap = heapq.heappop(self._heap)
                try:
                    await self._fire(snap)
                except LogWriteError:
                    logger.critical("Snapshot log write failed — stopping runtime")
                    self._stop_event.set()
                    return
                except Exception:
                    logger.exception("Snapshot fire failed: %s/%s", snap.event_id, snap.horizon)

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass  # Normal wakeup — interruptible sleep via stop_event

    def stop(self) -> None:
        self._stop_event.set()

    @property
    def pending_count(self) -> int:
        return len(self._heap)
