"""v80 _sell_triggered 수정 검증 — 콜백 실패 시 영구 차단 방지."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from unittest.mock import MagicMock

import pytest

from kindshot.config import Config
from kindshot.price import SnapshotScheduler


def _make_scheduler(*, callback_raises: bool = False) -> tuple[SnapshotScheduler, MagicMock]:
    """테스트용 SnapshotScheduler 생성."""
    config = Config()
    fetcher = MagicMock()
    log = MagicMock()

    trade_close_cb = MagicMock()
    if callback_raises:
        trade_close_cb.side_effect = RuntimeError("callback failed")

    pnl_cb = MagicMock()

    scheduler = SnapshotScheduler(
        config=config,
        fetcher=fetcher,
        log=log,
        pnl_callback=pnl_cb,
        trade_close_callback=trade_close_cb,
    )
    return scheduler, trade_close_cb


def _register_event(scheduler: SnapshotScheduler, event_id: str, ticker: str, entry_px: float) -> None:
    """이벤트를 스케줄러에 등록 (t0 가격 + entry time 설정)."""
    scheduler._t0_prices[event_id] = (entry_px, entry_px)
    scheduler._entry_times[event_id] = time.monotonic() - 60
    scheduler._event_tickers[event_id] = ticker
    scheduler._event_confidence[event_id] = 80
    scheduler._event_order_size[event_id] = 5_000_000
    scheduler._remaining_position_pct[event_id] = 1.0


@dataclass
class FakeSnap:
    """_emit_trade_close에 필요한 ScheduledSnapshot 호환 객체."""
    event_id: str
    ticker: str
    price: float = 0.0
    mode: str = "paper"
    horizon: str = "close"
    fire_at: float = 0.0
    t0_px: Optional[float] = None
    t0_cum_value: Optional[float] = None
    run_id: str = ""
    schema_version: str = "0.1.3"
    is_buy_decision: bool = True


class TestSellTriggeredFix:
    """v80: 콜백 성공/실패에 따른 _sell_triggered 동작 검증."""

    def test_callback_success_adds_to_sell_triggered(self):
        """콜백 성공 시 _sell_triggered에 event_id 추가."""
        scheduler, cb = _make_scheduler(callback_raises=False)
        event_id = "test_event_001"
        _register_event(scheduler, event_id, "005930", 70000.0)

        snap = FakeSnap(event_id=event_id, ticker="005930", price=71000.0)
        scheduler._emit_trade_close(
            snap=snap,
            exit_px=71000.0,
            ret_long=0.0143,
            exit_type="TAKE_PROFIT",
            horizon="t+5m",
            position_closed=True,
        )

        assert event_id in scheduler._sell_triggered
        assert cb.call_count == 1

    def test_callback_failure_does_not_add_to_sell_triggered(self):
        """콜백 실패(예외) 시 _sell_triggered에 추가되지 않음."""
        scheduler, cb = _make_scheduler(callback_raises=True)
        event_id = "test_event_002"
        _register_event(scheduler, event_id, "005930", 70000.0)

        snap = FakeSnap(event_id=event_id, ticker="005930", price=69000.0)
        scheduler._emit_trade_close(
            snap=snap,
            exit_px=69000.0,
            ret_long=-0.0143,
            exit_type="STOP_LOSS",
            horizon="t+5m",
            position_closed=True,
        )

        assert event_id not in scheduler._sell_triggered
        assert cb.call_count == 1

    def test_retry_after_callback_failure(self):
        """콜백 실패 후 재시도하면 정상 동작."""
        scheduler, cb = _make_scheduler(callback_raises=True)
        event_id = "test_event_003"
        _register_event(scheduler, event_id, "005930", 70000.0)

        snap = FakeSnap(event_id=event_id, ticker="005930", price=69000.0)

        # 첫 시도: 콜백 실패 → _sell_triggered에 없음
        scheduler._emit_trade_close(
            snap=snap,
            exit_px=69000.0,
            ret_long=-0.0143,
            exit_type="STOP_LOSS",
            horizon="close",
            position_closed=True,
        )
        assert event_id not in scheduler._sell_triggered

        # 콜백 수리
        cb.side_effect = None
        cb.reset_mock()

        # 재시도: 이제 성공
        scheduler._emit_trade_close(
            snap=snap,
            exit_px=69000.0,
            ret_long=-0.0143,
            exit_type="STOP_LOSS",
            horizon="close",
            position_closed=True,
        )
        assert event_id in scheduler._sell_triggered
        assert cb.call_count == 1

    def test_partial_close_does_not_trigger_sell(self):
        """부분 청산(position_closed=False) 시 _sell_triggered에 추가되지 않음."""
        scheduler, cb = _make_scheduler(callback_raises=False)
        event_id = "test_event_004"
        _register_event(scheduler, event_id, "005930", 70000.0)

        snap = FakeSnap(event_id=event_id, ticker="005930", price=71000.0)
        scheduler._emit_trade_close(
            snap=snap,
            exit_px=71000.0,
            ret_long=0.0143,
            exit_type="PARTIAL_TP",
            horizon="t+5m",
            close_fraction=0.5,
            position_closed=False,
        )

        assert event_id not in scheduler._sell_triggered
        assert cb.call_count == 1
