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
    "t+10m": 600,
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
        trade_close_callback: Optional[object] = None,
        order_executor: Optional[object] = None,
    ) -> None:
        self._config = config
        self._fetcher = fetcher
        self._logger = log
        self._heap: list[ScheduledSnapshot] = []
        self._stop_event = stop_event or asyncio.Event()
        self._pnl_callback = pnl_callback  # callable(ticker, pnl_won) for guardrail state
        self._trade_close_callback = trade_close_callback
        self._order_executor = order_executor  # OrderExecutor for live sell
        # Track effective t0 prices per event_id for return calculation
        self._t0_prices: dict[str, tuple[Optional[float], Optional[float]]] = {}
        # Track ticker per event_id for pnl callback
        self._event_tickers: dict[str, str] = {}
        # 가상 익절/손절 추적 (event_id → exit horizon)
        self._virtual_exits: dict[str, str] = {}
        self._virtual_exit_reasons: dict[str, str] = {}
        # Trailing stop peak 추적 (event_id → peak return %)
        self._peak_returns: dict[str, float] = {}
        # 진입 시각 추적 (event_id → monotonic time at t0 fire)
        self._entry_times: dict[str, float] = {}
        # 진입 KST 시각 (시간대별 청산 차등용)
        self._entry_times_kst: dict[str, datetime] = {}
        # t+5m 체크포인트 통과 여부 (event_id → profitable at 5m)
        self._t5m_profitable: dict[str, bool] = {}
        # 이벤트별 max_hold_minutes (0=EOD까지)
        self._max_hold_minutes: dict[str, int] = {}
        # 이벤트별 confidence (동적 TP/SL용)
        self._event_confidence: dict[str, int] = {}
        # 이벤트별 실제 포지션 사이즈 (P&L 계산용)
        self._event_order_size: dict[str, float] = {}
        self._remaining_position_pct: dict[str, float] = {}
        self._partial_take_profit_taken: dict[str, bool] = {}
        self._realized_pnl_won: dict[str, float] = {}
        self._realized_closed_size_won: dict[str, float] = {}
        self._realized_exit_notional: dict[str, float] = {}
        self._support_reference_px: dict[str, float] = {}
        self._active_buy_events_by_ticker: dict[str, set[str]] = {}
        # Stale position 감지: 3분 경과 후 모멘텀 소멸 시 exit (5분→3분 타이트닝)
        self._stale_threshold_pct_default: float = 0.2
        self._stale_min_elapsed_s: float = 180.0  # 3분
        # VTS 모드: real API 키 없으면 가격이 항상 stale → 모멘텀 기반 exit 비활성화
        self._using_vts = not config.kis_real_app_key
        if self._using_vts:
            logger.warning("VTS mode detected — stale exit and T5M loss exit disabled (prices are not real-time)")
        # Live sell 추적: 이미 매도 주문한 event_id (close P&L 중복 방지)
        self._sell_triggered: set[str] = set()

    def _get_trailing_stop_pct(self, event_id: str) -> float:
        """시간대 + hold profile별 trailing stop 폭 반환.

        EOD hold(자사주소각 등): 기본 trailing × 1.5 (장기 트렌드 보호)
        수주/공급계약(hold≤20): 기본 trailing × 0.85 (반전 대비)
        """
        entry_time = self._entry_times.get(event_id)
        if entry_time is None:
            return self._config.trailing_stop_pct
        elapsed_s = time.monotonic() - entry_time
        if elapsed_s < 300:  # 0~5분
            base = (
                self._config.trailing_stop_post_partial_early_pct
                if self._partial_take_profit_taken.get(event_id)
                else self._config.trailing_stop_early_pct
            )
        elif elapsed_s < 1800:  # 5~30분
            base = (
                self._config.trailing_stop_post_partial_mid_pct
                if self._partial_take_profit_taken.get(event_id)
                else self._config.trailing_stop_mid_pct
            )
        else:  # 30분+
            base = (
                self._config.trailing_stop_post_partial_late_pct
                if self._partial_take_profit_taken.get(event_id)
                else self._config.trailing_stop_late_pct
            )

        if self._partial_take_profit_taken.get(event_id):
            return base

        # Hold profile 보정
        hold = self._max_hold_minutes.get(event_id, self._config.max_hold_minutes)
        if hold == 0:
            return base * 1.5  # EOD hold: 넓은 trailing (트렌드 보호)
        if hold <= 20:
            return base * 0.85  # 수주/공급계약: trailing (반전 대비, TP와 일관)
        return base

    def _get_session_adjusted_sl(self, event_id: str, base_sl: float) -> float:
        """시간대별 SL 조정. 장 초반(09:00-09:30)은 타이트, 장 후반(14:00+)은 타이트."""
        entry_kst = self._entry_times_kst.get(event_id)
        if entry_kst is None:
            return base_sl
        h, m = entry_kst.hour, entry_kst.minute
        # 장 초반 (09:00-09:30): 변동성 최고, SL 타이트
        if h == 9 and m < 30:
            return base_sl * self._config.session_early_sl_multiplier  # 예: -1.5 * 0.7 = -1.05
        # 장 후반 (14:00+): 회복 시간 부족, SL 타이트
        if h >= 14:
            return base_sl * 0.8  # 20% 타이트
        return base_sl

    def _get_session_adjusted_max_hold(self, event_id: str, base_max_hold: int) -> int:
        """시간대별 max_hold 조정. 장 후반(14:00+)은 축소."""
        if base_max_hold == 0:  # EOD hold는 조정 안 함
            return 0
        entry_kst = self._entry_times_kst.get(event_id)
        if entry_kst is None:
            return base_max_hold
        if entry_kst.hour >= 14:
            return max(5, int(base_max_hold / self._config.session_late_max_hold_divisor))
        return base_max_hold

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
        confidence: int = 0,
        size_hint: str = "M",
        support_reference_px: float | None = None,
    ) -> None:
        """Schedule t0 snapshot immediately + future horizons."""
        now = time.monotonic()

        self._event_tickers[event_id] = ticker
        if max_hold_minutes > 0:
            self._max_hold_minutes[event_id] = max_hold_minutes
        if confidence > 0:
            self._event_confidence[event_id] = confidence
        if is_buy_decision:
            self._event_order_size[event_id] = self._config.order_size_for_hint(size_hint)
            self._remaining_position_pct[event_id] = 1.0
            self._partial_take_profit_taken[event_id] = False
            self._realized_pnl_won[event_id] = 0.0
            self._realized_closed_size_won[event_id] = 0.0
            self._realized_exit_notional[event_id] = 0.0
            if support_reference_px is not None and support_reference_px > 0:
                self._support_reference_px[event_id] = support_reference_px
            self._active_buy_events_by_ticker.setdefault(ticker, set()).add(event_id)

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

    def has_open_position(self, ticker: str) -> bool:
        event_ids = self._active_buy_events_by_ticker.get(ticker, set())
        return any(
            self._remaining_position_pct.get(event_id, 0.0) > 0.0 and event_id not in self._sell_triggered
            for event_id in event_ids
        )

    async def force_exit_ticker(
        self,
        ticker: str,
        *,
        exit_type: str,
        horizon: str,
    ) -> int:
        event_ids = sorted(self._active_buy_events_by_ticker.get(ticker, set()))
        closed = 0
        for event_id in event_ids:
            if self._remaining_position_pct.get(event_id, 0.0) <= 0.0 or event_id in self._sell_triggered:
                continue
            entry_px, _unused = self._t0_prices.get(event_id, (None, None))
            if entry_px is None or entry_px <= 0:
                logger.warning("Forced exit skipped [%s] %s: missing entry price", ticker, event_id[:8])
                continue
            price = await self._fetcher.fetch(ticker)
            if price is None or price.px is None or price.px <= 0:
                logger.warning("Forced exit skipped [%s] %s: current price unavailable", ticker, event_id[:8])
                continue
            ret_long = (price.px - entry_px) / entry_px
            snap = ScheduledSnapshot(
                fire_at=time.monotonic(),
                event_id=event_id,
                ticker=ticker,
                horizon=horizon,
                t0_basis=T0Basis.DECIDED_AT,
                t0_ts=datetime.now(timezone.utc),
                mode="live" if self._order_executor is not None else "paper",
                is_buy_decision=True,
            )
            if self._order_executor is not None:
                try:
                    sell_result = await self._order_executor.sell_position(event_id, ticker)
                    if not sell_result or not sell_result.success:
                        logger.warning("Forced live sell rejected [%s] %s", ticker, event_id[:8])
                        continue
                except Exception:
                    logger.exception("Forced live sell error [%s] %s", ticker, event_id[:8])
                    continue
            self._virtual_exits[event_id] = horizon
            self._virtual_exit_reasons[event_id] = exit_type
            self._emit_trade_close(
                snap=snap,
                exit_px=price.px,
                ret_long=ret_long,
                exit_type=exit_type,
                horizon=horizon,
                close_fraction=self._remaining_position_pct.get(event_id, 1.0),
                position_closed=True,
            )
            logger.info(
                "FORCED EXIT [%s] %s: %s at %s px=%.2f ret=%.2f%%",
                ticker,
                event_id[:8],
                exit_type,
                horizon,
                price.px,
                ret_long * 100,
            )
            closed += 1
        return closed

    def _emit_trade_close(
        self,
        *,
        snap: ScheduledSnapshot,
        exit_px: float,
        ret_long: float,
        exit_type: str,
        horizon: str,
        close_fraction: float | None = None,
        position_closed: bool = True,
        remaining_position_pct: float = 0.0,
    ) -> None:
        logger.info(
            "TRADE_CLOSE_ENTER [%s] %s: exit_type=%s horizon=%s ret=%.4f px=%.2f pos_closed=%s sell_triggered=%s",
            snap.ticker, snap.event_id[:8], exit_type, horizon, ret_long, exit_px,
            position_closed, snap.event_id in self._sell_triggered,
        )
        if position_closed and snap.event_id in self._sell_triggered:
            logger.warning("TRADE_CLOSE_SKIP [%s] %s: already in _sell_triggered", snap.ticker, snap.event_id[:8])
            return
        initial_size = self._event_order_size.get(snap.event_id, self._config.order_size)
        active_fraction = self._remaining_position_pct.get(snap.event_id, 1.0)
        realized_fraction = active_fraction if close_fraction is None else min(active_fraction, max(0.0, close_fraction))
        realized_size = initial_size * realized_fraction
        pnl_won = ret_long * realized_size
        entry_px = self._t0_prices.get(snap.event_id, (None, None))[0]
        hold_seconds = 0
        entry_time = self._entry_times.get(snap.event_id)
        if entry_time is not None:
            hold_seconds = max(0, int(time.monotonic() - entry_time))
        self._realized_pnl_won[snap.event_id] = self._realized_pnl_won.get(snap.event_id, 0.0) + pnl_won
        self._realized_closed_size_won[snap.event_id] = self._realized_closed_size_won.get(snap.event_id, 0.0) + realized_size
        self._realized_exit_notional[snap.event_id] = self._realized_exit_notional.get(snap.event_id, 0.0) + (realized_size * exit_px)
        cumulative_pnl_won = self._realized_pnl_won[snap.event_id]
        cumulative_ret_pct = (cumulative_pnl_won / initial_size) * 100 if initial_size > 0 else ret_long * 100
        average_exit_px = (
            self._realized_exit_notional[snap.event_id] / self._realized_closed_size_won[snap.event_id]
            if self._realized_closed_size_won[snap.event_id] > 0
            else exit_px
        )
        if position_closed:
            self._remaining_position_pct[snap.event_id] = 0.0
            self._sell_triggered.add(snap.event_id)
        else:
            self._remaining_position_pct[snap.event_id] = remaining_position_pct
        if self._pnl_callback:
            self._pnl_callback(snap.ticker, pnl_won)
        if self._trade_close_callback and entry_px is not None:
            logger.info(
                "TRADE_CLOSE_CALLBACK [%s] %s: entry=%.2f exit=%.2f ret=%.2f%% pnl=%.0f size=%.0f",
                snap.ticker, snap.event_id[:8], entry_px, exit_px, ret_long * 100, pnl_won, realized_size,
            )
            try:
                self._trade_close_callback(
                    event_id=snap.event_id,
                    ticker=snap.ticker,
                    entry_px=entry_px,
                    exit_px=exit_px,
                    ret_pct=ret_long * 100,
                    pnl_won=pnl_won,
                    exit_type=exit_type,
                    horizon=horizon,
                    hold_seconds=hold_seconds,
                    size_won=realized_size,
                    confidence=self._event_confidence.get(snap.event_id, 0),
                    mode=snap.mode,
                    position_closed=position_closed,
                    remaining_size_won=initial_size * remaining_position_pct,
                    exit_fraction=realized_fraction,
                    initial_size_won=initial_size,
                    cumulative_pnl_won=cumulative_pnl_won,
                    cumulative_ret_pct=cumulative_ret_pct,
                    average_exit_px=average_exit_px,
                )
            except Exception:
                logger.exception("TRADE_CLOSE_CALLBACK_ERROR [%s] %s", snap.ticker, snap.event_id[:8])
        elif entry_px is None:
            logger.warning(
                "TRADE_CLOSE_NO_ENTRY_PX [%s] %s: t0_prices=%s",
                snap.ticker, snap.event_id[:8], snap.event_id in self._t0_prices,
            )
        elif not self._trade_close_callback:
            logger.warning(
                "TRADE_CLOSE_NO_CALLBACK [%s] %s: callback not set",
                snap.ticker, snap.event_id[:8],
            )

    def _cleanup_event_tracking(self, event_id: str) -> None:
        self._t0_prices.pop(event_id, None)
        ticker = self._event_tickers.pop(event_id, None)
        self._entry_times.pop(event_id, None)
        self._entry_times_kst.pop(event_id, None)
        self._t5m_profitable.pop(event_id, None)
        self._max_hold_minutes.pop(event_id, None)
        self._event_confidence.pop(event_id, None)
        self._event_order_size.pop(event_id, None)
        self._remaining_position_pct.pop(event_id, None)
        self._partial_take_profit_taken.pop(event_id, None)
        self._realized_pnl_won.pop(event_id, None)
        self._realized_closed_size_won.pop(event_id, None)
        self._realized_exit_notional.pop(event_id, None)
        self._peak_returns.pop(event_id, None)
        self._virtual_exits.pop(event_id, None)
        self._virtual_exit_reasons.pop(event_id, None)
        self._support_reference_px.pop(event_id, None)
        if ticker:
            event_ids = self._active_buy_events_by_ticker.get(ticker)
            if event_ids is not None:
                event_ids.discard(event_id)
                if not event_ids:
                    self._active_buy_events_by_ticker.pop(ticker, None)
        self._sell_triggered.discard(event_id)

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
            if snap.t0_ts.tzinfo is None:
                self._entry_times_kst[snap.event_id] = snap.t0_ts.replace(tzinfo=_KST)
            else:
                self._entry_times_kst[snap.event_id] = snap.t0_ts.astimezone(_KST)
        else:
            t0_px, t0_cum = self._t0_prices.get(snap.event_id, (None, None))
            if px is not None and t0_px and t0_px > 0:
                ret_long = (px - t0_px) / t0_px
                ret_short = -ret_long
                if cum_value is not None and t0_cum is not None:
                    value_since = cum_value - t0_cum

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
            # 동적 TP/SL: confidence + hold_profile 차별화
            from kindshot.guardrails import get_dynamic_tp_pct, get_dynamic_stop_loss_pct
            evt_conf = self._event_confidence.get(snap.event_id, 0)
            evt_hold = self._max_hold_minutes.get(snap.event_id, self._config.max_hold_minutes)
            effective_tp = get_dynamic_tp_pct(self._config, evt_conf, evt_hold) if evt_conf > 0 else self._config.paper_take_profit_pct
            effective_sl = get_dynamic_stop_loss_pct(self._config, evt_conf, evt_hold) if evt_conf > 0 else self._config.paper_stop_loss_pct
            # 시간대별 SL 조정 (장 초반/후반 타이트닝)
            effective_sl = self._get_session_adjusted_sl(snap.event_id, effective_sl)
            tp_active = effective_tp > 0
            sl_active = effective_sl < 0

            # Track peak for trailing stop
            if self._config.trailing_stop_enabled:
                prev_peak = self._peak_returns.get(snap.event_id, 0.0)
                self._peak_returns[snap.event_id] = max(prev_peak, ret_pct)
                peak = self._peak_returns[snap.event_id]

            # --- t+5m 체크포인트: 5분 경과 후 손실→즉시 청산, 수익→타이트 trailing ---
            elapsed_s = 0.0
            entry_time = self._entry_times.get(snap.event_id)
            if entry_time is not None:
                elapsed_s = time.monotonic() - entry_time

            is_past_5m = elapsed_s >= 300
            if self._config.t5m_loss_exit_enabled and is_past_5m and snap.event_id not in self._t5m_profitable:
                # v71: threshold 도입 — 미미한 손실(-0.3% 이내)은 수익으로 간주하여 홀드
                self._t5m_profitable[snap.event_id] = ret_pct > self._config.t5m_loss_exit_threshold_pct

            remaining_position_pct = self._remaining_position_pct.get(snap.event_id, 1.0)
            partial_target_pct = effective_tp * self._config.partial_take_profit_target_ratio if tp_active else 0.0
            if (
                tp_active
                and snap.mode == "paper"
                and self._config.partial_take_profit_enabled
                and not self._partial_take_profit_taken.get(snap.event_id, False)
                and ret_pct >= partial_target_pct
            ):
                close_fraction = min(remaining_position_pct, self._config.partial_take_profit_size_pct / 100)
                if 0.0 < close_fraction < remaining_position_pct:
                    post_partial_remaining = max(0.0, remaining_position_pct - close_fraction)
                    self._partial_take_profit_taken[snap.event_id] = True
                    self._emit_trade_close(
                        snap=snap,
                        exit_px=px,
                        ret_long=ret_long,
                        exit_type="partial_take_profit",
                        horizon=snap.horizon,
                        close_fraction=close_fraction,
                        position_closed=False,
                        remaining_position_pct=post_partial_remaining,
                    )
                    logger.info(
                        "PAPER PARTIAL TP [%s] %s: +%.2f%% at %s (target %.1f%%, close %.0f%%, remain %.0f%%)",
                        snap.ticker,
                        snap.event_id[:8],
                        ret_pct,
                        snap.horizon,
                        partial_target_pct,
                        close_fraction * 100,
                        post_partial_remaining * 100,
                    )
                else:
                    self._virtual_exits[snap.event_id] = snap.horizon
                    self._virtual_exit_reasons[snap.event_id] = "take_profit"
                    logger.info(
                        "PAPER TP hit [%s] %s: +%.2f%% at %s (target %.1f%%, conf=%d)",
                        snap.ticker, snap.event_id[:8], ret_pct, snap.horizon,
                        effective_tp, evt_conf,
                    )
            elif tp_active and not self._config.partial_take_profit_enabled and ret_pct >= effective_tp:
                self._virtual_exits[snap.event_id] = snap.horizon
                self._virtual_exit_reasons[snap.event_id] = "take_profit"
                logger.info(
                    "PAPER TP hit [%s] %s: +%.2f%% at %s (target %.1f%%, conf=%d)",
                    snap.ticker, snap.event_id[:8], ret_pct, snap.horizon,
                    effective_tp, evt_conf,
                )
            elif sl_active and ret_pct <= effective_sl:
                self._virtual_exits[snap.event_id] = snap.horizon
                self._virtual_exit_reasons[snap.event_id] = "stop_loss"
                logger.info(
                    "PAPER SL hit [%s] %s: %.2f%% at %s (stop %.1f%%, conf=%d)",
                    snap.ticker, snap.event_id[:8], ret_pct, snap.horizon,
                    effective_sl, evt_conf,
                )
            elif (
                self._config.support_exit_enabled
                and (support_reference_px := self._support_reference_px.get(snap.event_id)) is not None
                and support_reference_px > 0
                and px <= support_reference_px * (1 - max(0.0, self._config.support_exit_buffer_pct) / 100)
            ):
                support_threshold_px = support_reference_px * (1 - max(0.0, self._config.support_exit_buffer_pct) / 100)
                self._virtual_exits[snap.event_id] = snap.horizon
                self._virtual_exit_reasons[snap.event_id] = "support_breach"
                logger.info(
                    "PAPER SUPPORT EXIT [%s] %s: px=%.2f at %s (support %.2f, threshold %.2f)",
                    snap.ticker,
                    snap.event_id[:8],
                    px,
                    snap.horizon,
                    support_reference_px,
                    support_threshold_px,
                )
            elif (
                self._config.t5m_loss_exit_enabled
                and not self._using_vts  # VTS 스테일 가격에서는 T5M 비활성화
                and is_past_5m
                and self._t5m_profitable.get(snap.event_id) is False
                and ret_pct <= self._config.t5m_loss_exit_threshold_pct
                and evt_hold != 0  # EOD hold 제외
            ):
                # t+5m 체크포인트: 손실 포지션 즉시 청산
                self._virtual_exits[snap.event_id] = snap.horizon
                self._virtual_exit_reasons[snap.event_id] = "t5m_loss_exit"
                logger.info(
                    "PAPER T5M LOSS EXIT [%s] %s: %.2f%% at %s (5m checkpoint, cut losers)",
                    snap.ticker, snap.event_id[:8], ret_pct, snap.horizon,
                )
            elif (
                self._config.trailing_stop_enabled
                and peak >= self._config.trailing_stop_activation_pct
            ):
                # t+5m 이후 수익 포지션: 타이트 trailing으로 전환
                if self._t5m_profitable.get(snap.event_id) is True:
                    trail_pct = self._config.t5m_profit_trailing_pct
                else:
                    trail_pct = self._get_trailing_stop_pct(snap.event_id)
                if ret_pct <= peak - trail_pct:
                    self._virtual_exits[snap.event_id] = snap.horizon
                    self._virtual_exit_reasons[snap.event_id] = "trailing_stop"
                    logger.info(
                        "PAPER TRAILING STOP [%s] %s: %.2f%% at %s (peak %.2f%%, trail -%.1f%%%s)",
                        snap.ticker, snap.event_id[:8], ret_pct, snap.horizon,
                        peak, trail_pct,
                        ", t5m-tight" if self._t5m_profitable.get(snap.event_id) else "",
                    )
            else:
                # 이벤트별 또는 전역 max_hold_minutes 체크 (시간대별 조정 적용)
                event_max_raw = self._max_hold_minutes.get(snap.event_id, self._config.max_hold_minutes)
                event_max = self._get_session_adjusted_max_hold(snap.event_id, event_max_raw)
                if event_max > 0 and snap.horizon == f"t+{event_max}m":
                    self._virtual_exits[snap.event_id] = snap.horizon
                    self._virtual_exit_reasons[snap.event_id] = "max_hold"
                    logger.info(
                        "PAPER MAX HOLD [%s] %s: %.2f%% at %s (%dm limit)",
                        snap.ticker, snap.event_id[:8], ret_pct, snap.horizon,
                        event_max,
                    )
                # Stale position exit: 3분+ 경과 후 모멘텀 소멸
                # confidence 기반 동적 threshold: 고확신(85+)은 SL 밴드 내 조기 exit 방지
                # VTS 모드에서는 stale exit 비활성화 (가격이 항상 0%로 보이므로 의미 없음)
                elif event_max_raw != 0 and not self._using_vts:  # EOD hold 및 VTS 제외
                    if entry_time is not None:
                        stale_pct = self._stale_threshold_pct_default
                        if evt_conf >= 85:
                            stale_pct = max(0.5, abs(effective_sl) * 0.4)
                        elif evt_conf >= 80:
                            stale_pct = 0.3
                        if (
                            elapsed_s >= self._stale_min_elapsed_s
                            and abs(ret_pct) < stale_pct
                        ):
                            self._virtual_exits[snap.event_id] = snap.horizon
                            self._virtual_exit_reasons[snap.event_id] = "stale_exit"
                            logger.info(
                                "PAPER STALE EXIT [%s] %s: %.2f%% at %s (%.0fs elapsed, no momentum)",
                                snap.ticker, snap.event_id[:8], ret_pct, snap.horizon,
                                elapsed_s,
                            )

        # 가상 청산: paper는 즉시 close 처리, live는 실매도 성공 시 close 처리
        _ve = snap.event_id in self._virtual_exits
        _st = snap.event_id in self._sell_triggered
        if snap.is_buy_decision and _ve:
            logger.info(
                "VIRTUAL_EXIT_CHECK [%s] %s: virtual_exit=%s sell_triggered=%s ret_long=%s px=%s order_exec=%s horizon=%s",
                snap.ticker, snap.event_id[:8], _ve, _st, ret_long, px,
                self._order_executor is not None, snap.horizon,
            )
        if (
            snap.is_buy_decision
            and _ve
            and not _st
        ):
            exit_type = self._virtual_exit_reasons.get(snap.event_id, "virtual_exit")
            if self._order_executor is not None:
                try:
                    _sell_result = await self._order_executor.sell_position(snap.event_id, snap.ticker)
                    if _sell_result and _sell_result.success and ret_long is not None and px is not None:
                        self._emit_trade_close(
                            snap=snap,
                            exit_px=px,
                            ret_long=ret_long,
                            exit_type=exit_type,
                            horizon=self._virtual_exits[snap.event_id],
                            close_fraction=self._remaining_position_pct.get(snap.event_id, 1.0),
                            position_closed=True,
                        )
                except Exception:
                    logger.exception("LIVE SELL order error [%s]", snap.ticker)
            elif ret_long is not None and px is not None:
                self._emit_trade_close(
                    snap=snap,
                    exit_px=px,
                    ret_long=ret_long,
                    exit_type=exit_type,
                    horizon=self._virtual_exits[snap.event_id],
                    close_fraction=self._remaining_position_pct.get(snap.event_id, 1.0),
                    position_closed=True,
                )

        if (
            snap.is_buy_decision
            and snap.horizon == "close"
            and snap.event_id not in self._sell_triggered
            and ret_long is not None
            and px is not None
        ):
            self._emit_trade_close(
                snap=snap,
                exit_px=px,
                ret_long=ret_long,
                exit_type="close",
                horizon="close",
                close_fraction=self._remaining_position_pct.get(snap.event_id, 1.0),
                position_closed=True,
            )

        if snap.horizon == "close":
            self._cleanup_event_tracking(snap.event_id)

    async def flush_ready_on_shutdown(self) -> int:
        """Fire all snapshots that are already due at shutdown time."""
        now = time.monotonic()
        kept: list[ScheduledSnapshot] = []
        flushed: list[ScheduledSnapshot] = []
        while self._heap:
            snap = heapq.heappop(self._heap)
            if snap.fire_at <= now:
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
