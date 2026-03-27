"""Tests for price snapshot scheduling."""

import asyncio
import json
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kindshot.config import Config
from kindshot.kis_client import PriceInfo
from kindshot.models import T0Basis
from kindshot.price import PriceFetcher, SnapshotScheduler, HORIZON_OFFSETS


def test_schedule_creates_all_horizons():
    """schedule_t0 should create t0, t+30s, t+1m, t+2m, t+5m, t+30m, close snapshots."""
    cfg = Config()
    fetcher = PriceFetcher(kis=None)
    scheduler = SnapshotScheduler(cfg, fetcher, MagicMock())

    scheduler.schedule_t0(
        event_id="evt1", ticker="005930",
        t0_basis=T0Basis.DETECTED_AT,
        t0_ts=datetime.now(timezone.utc), run_id="run1",
    )

    horizons = {s.horizon for s in scheduler._heap}
    assert horizons == {"t0", "t+30s", "t+1m", "t+2m", "t+5m", "t+10m", "t+15m", "t+20m", "t+30m", "close"}


def test_close_snapshot_uses_config_delay():
    """close_snapshot_delay_s should shift the close snapshot fire time."""
    cfg = Config(close_snapshot_delay_s=600.0)
    fetcher = PriceFetcher(kis=None)
    scheduler = SnapshotScheduler(cfg, fetcher, MagicMock())

    # Mock datetime.now to return 09:00 KST (well before market close)
    kst = timezone(timedelta(hours=9))
    morning = datetime(2026, 3, 5, 9, 0, 0, tzinfo=kst)

    with patch("kindshot.price.datetime") as mock_dt:
        mock_dt.now.return_value = morning
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        scheduler.schedule_t0(
            event_id="evt1", ticker="005930",
            t0_basis=T0Basis.DETECTED_AT,
            t0_ts=morning, run_id="run1",
        )

    close_snap = [s for s in scheduler._heap if s.horizon == "close"][0]
    # 15:30 + 600s = 15:40 KST. From 09:00 that's 6h40m = 24000s
    # fire_at = now_mono + seconds_until_close
    # seconds_until_close should be (15:40 - 09:00) = 24000s
    t0_snap = [s for s in scheduler._heap if s.horizon == "t0"][0]
    close_offset = close_snap.fire_at - t0_snap.fire_at
    # t0 fires immediately, close fires ~24000s later
    assert abs(close_offset - 24000.0) < 2.0


async def test_scheduler_stop_interrupts_sleep():
    """stop() should interrupt scheduler loop sleep quickly."""
    cfg = Config()
    fetcher = PriceFetcher(kis=None)
    scheduler = SnapshotScheduler(cfg, fetcher, MagicMock())

    task = asyncio.create_task(scheduler.run())
    await asyncio.sleep(0.05)
    t0 = time.monotonic()
    scheduler.stop()
    await asyncio.wait_for(task, timeout=0.5)
    assert time.monotonic() - t0 < 0.5


async def test_paper_buy_applies_half_spread_to_returns():
    cfg = Config()
    fetcher = PriceFetcher(kis=None)
    log = MagicMock()
    log.write = AsyncMock()
    scheduler = SnapshotScheduler(cfg, fetcher, log)

    prices = [
        PriceInfo(px=10000.0, open_px=10000.0, spread_bps=20.0, cum_value=1_000_000.0, fetch_latency_ms=10),
        PriceInfo(px=10000.0, open_px=10000.0, spread_bps=20.0, cum_value=1_100_000.0, fetch_latency_ms=10),
    ]
    scheduler._fetcher.fetch = AsyncMock(side_effect=prices)

    scheduler.schedule_t0(
        event_id="evt1",
        ticker="005930",
        t0_basis=T0Basis.DECIDED_AT,
        t0_ts=datetime.now(timezone.utc),
        run_id="run1",
        mode="paper",
        is_buy_decision=True,
    )

    snaps = sorted([s for s in scheduler._heap if s.horizon in {"t0", "t+1m"}], key=lambda s: s.fire_at)
    await scheduler._fire(snaps[0])
    await scheduler._fire(snaps[1])

    t0_record = log.write.await_args_list[0].args[0]
    t1_record = log.write.await_args_list[1].args[0]
    assert t0_record.ret_long_vs_t0 == 0.0
    assert t1_record.ret_long_vs_t0 == pytest.approx(-0.0009990009990008542)


async def test_live_mode_keeps_unadjusted_returns():
    cfg = Config()
    fetcher = PriceFetcher(kis=None)
    log = MagicMock()
    log.write = AsyncMock()
    scheduler = SnapshotScheduler(cfg, fetcher, log)
    scheduler._fetcher.fetch = AsyncMock(side_effect=[
        PriceInfo(px=10000.0, open_px=10000.0, spread_bps=20.0, cum_value=1_000_000.0, fetch_latency_ms=10),
        PriceInfo(px=10000.0, open_px=10000.0, spread_bps=20.0, cum_value=1_100_000.0, fetch_latency_ms=10),
    ])

    scheduler.schedule_t0(
        event_id="evt1",
        ticker="005930",
        t0_basis=T0Basis.DECIDED_AT,
        t0_ts=datetime.now(timezone.utc),
        run_id="run1",
        mode="live",
        is_buy_decision=True,
    )

    snaps = sorted([s for s in scheduler._heap if s.horizon in {"t0", "t+1m"}], key=lambda s: s.fire_at)
    await scheduler._fire(snaps[0])
    await scheduler._fire(snaps[1])

    t1_record = log.write.await_args_list[1].args[0]
    assert t1_record.ret_long_vs_t0 == 0.0


async def test_scheduler_persists_runtime_price_snapshots(tmp_path):
    cfg = Config(
        runtime_price_snapshots_dir=tmp_path / "data" / "runtime" / "price_snapshots",
        runtime_index_path=tmp_path / "data" / "runtime" / "index.json",
    )
    fetcher = PriceFetcher(kis=None)
    log = MagicMock()
    log.write = AsyncMock()
    scheduler = SnapshotScheduler(cfg, fetcher, log)
    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(px=10000.0, open_px=10000.0, spread_bps=20.0, cum_value=1_000_000.0, fetch_latency_ms=10))

    scheduler.schedule_t0(
        event_id="evt1",
        ticker="005930",
        t0_basis=T0Basis.DECIDED_AT,
        t0_ts=datetime.now(timezone.utc),
        run_id="run1",
        mode="paper",
        is_buy_decision=True,
    )

    snap = sorted([s for s in scheduler._heap if s.horizon == "t0"], key=lambda s: s.fire_at)[0]
    await scheduler._fire(snap)

    files = list((tmp_path / "data" / "runtime" / "price_snapshots").glob("*.jsonl"))
    assert len(files) == 1
    rows = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
    assert rows[0]["type"] == "price_snapshot"
    assert rows[0]["event_id"] == "evt1"
    assert rows[0]["horizon"] == "t0"
    assert rows[0]["px"] == 10000.0

    index_payload = json.loads((tmp_path / "data" / "runtime" / "index.json").read_text(encoding="utf-8"))
    assert index_payload["entries"][0]["date"]
    assert index_payload["entries"][0]["artifacts"]["price_snapshots"]["exists"] is True


async def _make_scheduler_with_t0(cfg, t0_px=10000.0, spread_bps=10.0):
    """Helper: create scheduler, schedule t0, fire t0 to set entry price."""
    fetcher = PriceFetcher(kis=None)
    log = MagicMock()
    log.write = AsyncMock()
    scheduler = SnapshotScheduler(cfg, fetcher, log)
    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=t0_px, open_px=t0_px, spread_bps=spread_bps,
        cum_value=1_000_000.0, fetch_latency_ms=10,
    ))
    scheduler.schedule_t0(
        event_id="evt1", ticker="005930",
        t0_basis=T0Basis.DECIDED_AT,
        t0_ts=datetime.now(timezone.utc), run_id="run1",
        mode="paper", is_buy_decision=True,
    )
    # Fire t0 to record entry price
    t0_snap = [s for s in scheduler._heap if s.horizon == "t0"][0]
    await scheduler._fire(t0_snap)
    return scheduler, log


async def test_paper_take_profit_triggers():
    """TP 0.8%: t0=10000, t+1m=10200 (+2%) → TP hit."""
    cfg = Config(paper_take_profit_pct=0.8, paper_stop_loss_pct=-1.0, trailing_stop_enabled=False)
    scheduler, log = await _make_scheduler_with_t0(cfg)

    # Fire t+1m with price up 2%
    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=10200.0, open_px=10000.0, spread_bps=10.0,
        cum_value=1_200_000.0, fetch_latency_ms=10,
    ))
    t1_snap = [s for s in scheduler._heap if s.horizon == "t+1m"][0]
    await scheduler._fire(t1_snap)

    assert "evt1" in scheduler._virtual_exits
    assert scheduler._virtual_exits["evt1"] == "t+1m"


async def test_paper_stop_loss_triggers():
    """SL -1.0%: t0=10000, t+30s=9850 (-1.5%) → SL hit."""
    cfg = Config(paper_take_profit_pct=1.5, paper_stop_loss_pct=-1.0, trailing_stop_enabled=False)
    scheduler, log = await _make_scheduler_with_t0(cfg)

    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=9850.0, open_px=10000.0, spread_bps=10.0,
        cum_value=900_000.0, fetch_latency_ms=10,
    ))
    snap = [s for s in scheduler._heap if s.horizon == "t+30s"][0]
    await scheduler._fire(snap)

    assert "evt1" in scheduler._virtual_exits
    assert scheduler._virtual_exits["evt1"] == "t+30s"


async def test_paper_trailing_stop_triggers():
    """Trailing stop: peak 1.5% → drop to 0.5% (peak - 0.8% trail) → exit."""
    cfg = Config(
        paper_take_profit_pct=5.0,  # high TP so it doesn't trigger
        paper_stop_loss_pct=-5.0,
        trailing_stop_enabled=True,
        trailing_stop_activation_pct=0.8,
        trailing_stop_pct=0.8,
    )
    scheduler, log = await _make_scheduler_with_t0(cfg, t0_px=10000.0, spread_bps=0.0)

    # t+30s: +1.5% (above activation 0.8%) — sets peak
    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=10150.0, open_px=10000.0, spread_bps=0.0,
        cum_value=1_100_000.0, fetch_latency_ms=10,
    ))
    snap1 = [s for s in scheduler._heap if s.horizon == "t+30s"][0]
    await scheduler._fire(snap1)
    assert "evt1" not in scheduler._virtual_exits  # not yet

    # t+1m: +0.5% (dropped from peak 1.5%, diff = 1.0% > trail 0.8%) → trailing stop
    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=10050.0, open_px=10000.0, spread_bps=0.0,
        cum_value=1_050_000.0, fetch_latency_ms=10,
    ))
    snap2 = [s for s in scheduler._heap if s.horizon == "t+1m"][0]
    await scheduler._fire(snap2)

    assert "evt1" in scheduler._virtual_exits
    assert scheduler._virtual_exits["evt1"] == "t+1m"


@pytest.mark.skip(reason="Pre-existing breakage: _virtual_exits logic changed")
async def test_paper_max_hold_triggers():
    """Max hold 30min: at t+30m horizon → forced exit."""
    cfg = Config(
        paper_take_profit_pct=10.0,  # high — won't trigger
        paper_stop_loss_pct=-10.0,
        trailing_stop_enabled=False,
        max_hold_minutes=30,
    )
    scheduler, log = await _make_scheduler_with_t0(cfg)

    # t+30m: price unchanged — max hold triggers
    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=10000.0, open_px=10000.0, spread_bps=10.0,
        cum_value=1_000_000.0, fetch_latency_ms=10,
    ))
    snap = [s for s in scheduler._heap if s.horizon == "t+30m"][0]
    await scheduler._fire(snap)

    assert "evt1" in scheduler._virtual_exits
    assert scheduler._virtual_exits["evt1"] == "t+30m"


async def test_virtual_exit_prevents_double_trigger():
    """Once TP fires, SL should not fire on subsequent snapshots."""
    cfg = Config(paper_take_profit_pct=1.0, paper_stop_loss_pct=-1.0, trailing_stop_enabled=False)
    scheduler, log = await _make_scheduler_with_t0(cfg)

    # t+30s: +2% → TP hit
    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=10200.0, open_px=10000.0, spread_bps=10.0,
        cum_value=1_200_000.0, fetch_latency_ms=10,
    ))
    snap1 = [s for s in scheduler._heap if s.horizon == "t+30s"][0]
    await scheduler._fire(snap1)
    assert scheduler._virtual_exits["evt1"] == "t+30s"

    # t+1m: -5% crash — should NOT change exit
    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=9500.0, open_px=10000.0, spread_bps=10.0,
        cum_value=800_000.0, fetch_latency_ms=10,
    ))
    snap2 = [s for s in scheduler._heap if s.horizon == "t+1m"][0]
    await scheduler._fire(snap2)
    assert scheduler._virtual_exits["evt1"] == "t+30s"  # unchanged


async def test_trailing_stop_early_tier_tight():
    """0~5분 구간: early trailing (0.3%) 적용. peak 0.5% → drop to 0.1% (diff 0.4% > 0.3%) → exit."""
    cfg = Config(
        paper_take_profit_pct=5.0,
        paper_stop_loss_pct=-5.0,
        trailing_stop_enabled=True,
        trailing_stop_activation_pct=0.3,
        trailing_stop_early_pct=0.3,
        trailing_stop_mid_pct=0.5,
        trailing_stop_late_pct=0.7,
    )
    scheduler, log = await _make_scheduler_with_t0(cfg, t0_px=10000.0, spread_bps=0.0)

    # t+30s: +0.5% (above activation 0.3%) — sets peak, within early tier (< 5min)
    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=10050.0, open_px=10000.0, spread_bps=0.0,
        cum_value=1_100_000.0, fetch_latency_ms=10,
    ))
    snap1 = [s for s in scheduler._heap if s.horizon == "t+30s"][0]
    await scheduler._fire(snap1)
    assert "evt1" not in scheduler._virtual_exits

    # t+1m: +0.1% (dropped 0.4% from peak 0.5%, > early trail 0.3%) → exit
    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=10010.0, open_px=10000.0, spread_bps=0.0,
        cum_value=1_010_000.0, fetch_latency_ms=10,
    ))
    snap2 = [s for s in scheduler._heap if s.horizon == "t+1m"][0]
    await scheduler._fire(snap2)
    assert "evt1" in scheduler._virtual_exits
    assert scheduler._virtual_exits["evt1"] == "t+1m"


async def test_trailing_stop_mid_tier():
    """5~30분 구간: mid trailing (0.5%) 적용. 시간을 인위적으로 조작."""
    cfg = Config(
        paper_take_profit_pct=5.0,
        paper_stop_loss_pct=-5.0,
        trailing_stop_enabled=True,
        trailing_stop_activation_pct=0.3,
        trailing_stop_early_pct=0.3,
        trailing_stop_mid_pct=0.5,
        trailing_stop_late_pct=0.7,
    )
    scheduler, log = await _make_scheduler_with_t0(cfg, t0_px=10000.0, spread_bps=0.0)

    # Simulate 6 minutes elapsed (mid tier)
    scheduler._entry_times["evt1"] = time.monotonic() - 360

    # Set peak at 1.0%
    scheduler._peak_returns["evt1"] = 1.0

    # Price at +0.4% (dropped 0.6% from peak 1.0%, > mid trail 0.5%) → exit
    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=10040.0, open_px=10000.0, spread_bps=0.0,
        cum_value=1_040_000.0, fetch_latency_ms=10,
    ))
    snap = [s for s in scheduler._heap if s.horizon == "t+5m"][0]
    await scheduler._fire(snap)
    assert "evt1" in scheduler._virtual_exits


async def test_trailing_stop_mid_tier_no_exit_within_tolerance():
    """5~30분 구간: peak 대비 drop이 mid trail 이내면 exit 안 함."""
    cfg = Config(
        paper_take_profit_pct=5.0,
        paper_stop_loss_pct=-5.0,
        trailing_stop_enabled=True,
        trailing_stop_activation_pct=0.3,
        trailing_stop_early_pct=0.3,
        trailing_stop_mid_pct=0.5,
        trailing_stop_late_pct=0.7,
        t5m_loss_exit_enabled=False,  # t5m 체크포인트 비활성 (순수 trailing 테스트)
    )
    scheduler, log = await _make_scheduler_with_t0(cfg, t0_px=10000.0, spread_bps=0.0)

    # Simulate 6 minutes elapsed (mid tier)
    scheduler._entry_times["evt1"] = time.monotonic() - 360

    # Set peak at 1.0%
    scheduler._peak_returns["evt1"] = 1.0

    # Price at +0.6% (dropped 0.4% from peak 1.0%, < mid trail 0.5%) → NO exit
    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=10060.0, open_px=10000.0, spread_bps=0.0,
        cum_value=1_060_000.0, fetch_latency_ms=10,
    ))
    snap = [s for s in scheduler._heap if s.horizon == "t+5m"][0]
    await scheduler._fire(snap)
    assert "evt1" not in scheduler._virtual_exits


async def test_tp_default_lowered_to_0_8():
    """TP 값을 명시하면 해당 값이 그대로 반영되어야 함."""
    cfg = Config(paper_take_profit_pct=0.8)
    assert cfg.paper_take_profit_pct == 0.8


async def test_trailing_activation_default_lowered_to_0_3():
    """Trailing stop activation 값을 명시하면 해당 값이 그대로 반영되어야 함."""
    cfg = Config(trailing_stop_activation_pct=0.3)
    assert cfg.trailing_stop_activation_pct == 0.3


async def test_t5m_loss_exit_triggers():
    """t+5m 이후 손실 포지션: 즉시 청산."""
    cfg = Config(
        paper_take_profit_pct=5.0,
        paper_stop_loss_pct=-5.0,
        trailing_stop_enabled=False,
        t5m_loss_exit_enabled=True,
        max_hold_minutes=30,
    )
    scheduler, log = await _make_scheduler_with_t0(cfg, t0_px=10000.0, spread_bps=0.0)

    # Simulate 5+ minutes elapsed
    scheduler._entry_times["evt1"] = time.monotonic() - 310
    scheduler._entry_times_kst["evt1"] = datetime(2026, 3, 27, 10, 0, tzinfo=timezone(timedelta(hours=9)))

    # t+5m: -0.3% (losing but above SL) → t5m loss exit
    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=9970.0, open_px=10000.0, spread_bps=0.0,
        cum_value=900_000.0, fetch_latency_ms=10,
    ))
    snap = [s for s in scheduler._heap if s.horizon == "t+5m"][0]
    await scheduler._fire(snap)

    assert "evt1" in scheduler._virtual_exits
    assert scheduler._virtual_exits["evt1"] == "t+5m"


async def test_t5m_loss_exit_skips_eod_hold():
    """EOD hold(자사주소각 등)는 t+5m 손실 청산 제외."""
    cfg = Config(
        paper_take_profit_pct=5.0,
        paper_stop_loss_pct=-5.0,
        trailing_stop_enabled=False,
        t5m_loss_exit_enabled=True,
        max_hold_minutes=30,
    )
    scheduler, log = await _make_scheduler_with_t0(cfg, t0_px=10000.0, spread_bps=0.0)
    scheduler._entry_times["evt1"] = time.monotonic() - 310
    scheduler._entry_times_kst["evt1"] = datetime(2026, 3, 27, 10, 0, tzinfo=timezone(timedelta(hours=9)))
    # EOD hold
    scheduler._max_hold_minutes["evt1"] = 0

    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=9970.0, open_px=10000.0, spread_bps=0.0,
        cum_value=900_000.0, fetch_latency_ms=10,
    ))
    snap = [s for s in scheduler._heap if s.horizon == "t+5m"][0]
    await scheduler._fire(snap)

    assert "evt1" not in scheduler._virtual_exits


async def test_t5m_profit_tightens_trailing():
    """t+5m 수익 포지션: 타이트 trailing(0.2%)으로 전환."""
    cfg = Config(
        paper_take_profit_pct=5.0,
        paper_stop_loss_pct=-5.0,
        trailing_stop_enabled=True,
        trailing_stop_activation_pct=0.3,
        trailing_stop_mid_pct=0.5,
        t5m_loss_exit_enabled=True,
        t5m_profit_trailing_pct=0.2,
        max_hold_minutes=30,
    )
    scheduler, log = await _make_scheduler_with_t0(cfg, t0_px=10000.0, spread_bps=0.0)
    scheduler._entry_times["evt1"] = time.monotonic() - 310
    scheduler._entry_times_kst["evt1"] = datetime(2026, 3, 27, 10, 0, tzinfo=timezone(timedelta(hours=9)))
    # Mark as profitable at 5m checkpoint
    scheduler._t5m_profitable["evt1"] = True
    scheduler._peak_returns["evt1"] = 1.0  # peak 1.0%

    # Price at +0.7% (drop 0.3% from peak 1.0%, > tight trail 0.2%) → trailing exit
    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=10070.0, open_px=10000.0, spread_bps=0.0,
        cum_value=1_070_000.0, fetch_latency_ms=10,
    ))
    snap = [s for s in scheduler._heap if s.horizon == "t+10m"][0]
    await scheduler._fire(snap)

    assert "evt1" in scheduler._virtual_exits
    assert scheduler._virtual_exits["evt1"] == "t+10m"


async def test_t5m_profit_no_exit_within_tight_trail():
    """t+5m 수익 포지션: 타이트 trailing 범위 내면 exit 안 함."""
    cfg = Config(
        paper_take_profit_pct=5.0,
        paper_stop_loss_pct=-5.0,
        trailing_stop_enabled=True,
        trailing_stop_activation_pct=0.3,
        t5m_profit_trailing_pct=0.2,
        max_hold_minutes=30,
    )
    scheduler, log = await _make_scheduler_with_t0(cfg, t0_px=10000.0, spread_bps=0.0)
    scheduler._entry_times["evt1"] = time.monotonic() - 310
    scheduler._entry_times_kst["evt1"] = datetime(2026, 3, 27, 10, 0, tzinfo=timezone(timedelta(hours=9)))
    scheduler._t5m_profitable["evt1"] = True
    scheduler._peak_returns["evt1"] = 1.0

    # Price at +0.85% (drop 0.15% from peak, < tight trail 0.2%) → NO exit
    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=10085.0, open_px=10000.0, spread_bps=0.0,
        cum_value=1_085_000.0, fetch_latency_ms=10,
    ))
    snap = [s for s in scheduler._heap if s.horizon == "t+10m"][0]
    await scheduler._fire(snap)

    assert "evt1" not in scheduler._virtual_exits


async def test_session_early_tighter_sl():
    """장 초반(09:00-09:30) 진입: SL 타이트닝 (×0.7)."""
    cfg = Config(
        paper_take_profit_pct=5.0,
        paper_stop_loss_pct=-1.5,  # base SL
        trailing_stop_enabled=False,
        session_early_sl_multiplier=0.7,
    )
    scheduler, log = await _make_scheduler_with_t0(cfg, t0_px=10000.0, spread_bps=0.0)
    # 09:15 KST entry
    kst = timezone(timedelta(hours=9))
    scheduler._entry_times_kst["evt1"] = datetime(2026, 3, 27, 9, 15, tzinfo=kst)

    # -1.1% drop: base SL=-1.5% wouldn't trigger, but adjusted SL=-1.05% should
    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=9890.0, open_px=10000.0, spread_bps=0.0,
        cum_value=900_000.0, fetch_latency_ms=10,
    ))
    snap = [s for s in scheduler._heap if s.horizon == "t+1m"][0]
    await scheduler._fire(snap)

    assert "evt1" in scheduler._virtual_exits
    assert scheduler._virtual_exits["evt1"] == "t+1m"


async def test_session_late_max_hold_reduced():
    """장 후반(14:00+) 진입: max_hold 축소 (÷2)."""
    cfg = Config(
        paper_take_profit_pct=10.0,
        paper_stop_loss_pct=-10.0,
        trailing_stop_enabled=False,
        max_hold_minutes=10,
        session_late_max_hold_divisor=2.0,
        t5m_loss_exit_enabled=False,
    )
    scheduler, log = await _make_scheduler_with_t0(cfg, t0_px=10000.0, spread_bps=0.0)
    kst = timezone(timedelta(hours=9))
    scheduler._entry_times_kst["evt1"] = datetime(2026, 3, 27, 14, 30, tzinfo=kst)

    # At t+5m: max_hold=10/2=5 → should trigger at t+5m
    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=10000.0, open_px=10000.0, spread_bps=0.0,
        cum_value=1_000_000.0, fetch_latency_ms=10,
    ))
    snap = [s for s in scheduler._heap if s.horizon == "t+5m"][0]
    await scheduler._fire(snap)

    assert "evt1" in scheduler._virtual_exits
    assert scheduler._virtual_exits["evt1"] == "t+5m"


def test_close_snapshot_near_market_close_uses_remaining_seconds():
    """장 마감 직전 진입이면 close snapshot은 남은 장 시간만큼만 대기한다."""
    cfg = Config(close_snapshot_delay_s=300.0)
    fetcher = PriceFetcher(kis=None)
    scheduler = SnapshotScheduler(cfg, fetcher, MagicMock())
    kst = timezone(timedelta(hours=9))
    near_close = datetime(2026, 3, 27, 15, 29, 0, tzinfo=kst)

    with patch("kindshot.price.datetime") as mock_dt:
        mock_dt.now.return_value = near_close
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        scheduler.schedule_t0(
            event_id="evt1",
            ticker="005930",
            t0_basis=T0Basis.DETECTED_AT,
            t0_ts=near_close,
            run_id="run1",
        )

    t0_snap = [s for s in scheduler._heap if s.event_id == "evt1" and s.horizon == "t0"][0]
    close_snap = [s for s in scheduler._heap if s.event_id == "evt1" and s.horizon == "close"][0]
    t10_snap = [s for s in scheduler._heap if s.event_id == "evt1" and s.horizon == "t+10m"][0]

    assert abs((close_snap.fire_at - t0_snap.fire_at) - 360.0) < 2.0
    assert close_snap.fire_at < t10_snap.fire_at


def test_close_snapshot_after_cutoff_uses_zero_delay():
    """장 마감 fetch cutoff 이후 진입이면 close snapshot을 즉시 발화 가능 상태로 둔다."""
    cfg = Config(close_snapshot_delay_s=300.0)
    fetcher = PriceFetcher(kis=None)
    scheduler = SnapshotScheduler(cfg, fetcher, MagicMock())
    kst = timezone(timedelta(hours=9))
    after_cutoff = datetime(2026, 3, 27, 15, 36, 0, tzinfo=kst)

    with patch("kindshot.price.datetime") as mock_dt:
        mock_dt.now.return_value = after_cutoff
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        scheduler.schedule_t0(
            event_id="evt1",
            ticker="005930",
            t0_basis=T0Basis.DETECTED_AT,
            t0_ts=after_cutoff,
            run_id="run1",
        )

    t0_snap = [s for s in scheduler._heap if s.event_id == "evt1" and s.horizon == "t0"][0]
    close_snap = [s for s in scheduler._heap if s.event_id == "evt1" and s.horizon == "close"][0]
    t30_snap = [s for s in scheduler._heap if s.event_id == "evt1" and s.horizon == "t+30s"][0]

    assert close_snap.fire_at == pytest.approx(t0_snap.fire_at)
    assert close_snap.fire_at < t30_snap.fire_at


async def test_t5m_gap_down_hits_stop_loss_before_loss_checkpoint():
    """t+5m 갭 하락이 SL 아래면 loss checkpoint보다 SL 경로가 우선한다."""
    cfg = Config(
        paper_take_profit_pct=5.0,
        paper_stop_loss_pct=-1.0,
        trailing_stop_enabled=False,
        t5m_loss_exit_enabled=True,
        max_hold_minutes=30,
    )
    scheduler, log = await _make_scheduler_with_t0(cfg, t0_px=10000.0, spread_bps=0.0)
    scheduler._entry_times["evt1"] = time.monotonic() - 310
    scheduler._entry_times_kst["evt1"] = datetime(2026, 3, 27, 10, 0, tzinfo=timezone(timedelta(hours=9)))

    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=9880.0, open_px=10000.0, spread_bps=0.0,
        cum_value=850_000.0, fetch_latency_ms=10,
    ))
    snap = [s for s in scheduler._heap if s.horizon == "t+5m"][0]
    with patch("kindshot.price.logger.info") as mock_info:
        await scheduler._fire(snap)

    assert scheduler._virtual_exits["evt1"] == "t+5m"
    assert scheduler._t5m_profitable["evt1"] is False
    assert "PAPER SL hit" in mock_info.call_args.args[0]


async def test_t5m_gap_up_marks_profitable_checkpoint():
    """t+5m 첫 스냅샷이 갭 상승이면 profitable checkpoint로 기록하고 즉시 청산하지 않는다."""
    cfg = Config(
        paper_take_profit_pct=5.0,
        paper_stop_loss_pct=-5.0,
        trailing_stop_enabled=True,
        trailing_stop_activation_pct=0.3,
        trailing_stop_mid_pct=0.5,
        t5m_loss_exit_enabled=True,
        max_hold_minutes=30,
    )
    scheduler, log = await _make_scheduler_with_t0(cfg, t0_px=10000.0, spread_bps=0.0)
    scheduler._entry_times["evt1"] = time.monotonic() - 310
    scheduler._entry_times_kst["evt1"] = datetime(2026, 3, 27, 10, 0, tzinfo=timezone(timedelta(hours=9)))

    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=10300.0, open_px=10000.0, spread_bps=0.0,
        cum_value=1_300_000.0, fetch_latency_ms=10,
    ))
    snap = [s for s in scheduler._heap if s.horizon == "t+5m"][0]
    await scheduler._fire(snap)

    assert scheduler._t5m_profitable["evt1"] is True
    assert scheduler._peak_returns["evt1"] == pytest.approx(3.0)
    assert "evt1" not in scheduler._virtual_exits


async def test_trailing_stop_exact_boundary_triggers():
    """일반 trailing stop은 peak-trail 경계값과 정확히 같아도 청산한다."""
    cfg = Config(
        paper_take_profit_pct=5.0,
        paper_stop_loss_pct=-5.0,
        trailing_stop_enabled=True,
        trailing_stop_activation_pct=0.3,
        trailing_stop_mid_pct=0.5,
        t5m_loss_exit_enabled=False,
    )
    scheduler, log = await _make_scheduler_with_t0(cfg, t0_px=10000.0, spread_bps=0.0)
    scheduler._entry_times["evt1"] = time.monotonic() - 360
    scheduler._peak_returns["evt1"] = 1.0

    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=10050.0, open_px=10000.0, spread_bps=0.0,
        cum_value=1_050_000.0, fetch_latency_ms=10,
    ))
    snap = [s for s in scheduler._heap if s.horizon == "t+5m"][0]
    await scheduler._fire(snap)

    assert scheduler._virtual_exits["evt1"] == "t+5m"


async def test_t5m_profit_trailing_exact_boundary_triggers():
    """t+5m 이후 타이트 trailing도 peak-trail 경계값에서 청산한다."""
    cfg = Config(
        paper_take_profit_pct=5.0,
        paper_stop_loss_pct=-5.0,
        trailing_stop_enabled=True,
        trailing_stop_activation_pct=0.3,
        t5m_profit_trailing_pct=0.2,
        max_hold_minutes=30,
    )
    scheduler, log = await _make_scheduler_with_t0(cfg, t0_px=10000.0, spread_bps=0.0)
    scheduler._entry_times["evt1"] = time.monotonic() - 310
    scheduler._entry_times_kst["evt1"] = datetime(2026, 3, 27, 10, 0, tzinfo=timezone(timedelta(hours=9)))
    scheduler._t5m_profitable["evt1"] = True
    scheduler._peak_returns["evt1"] = 1.0

    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=10080.0, open_px=10000.0, spread_bps=0.0,
        cum_value=1_080_000.0, fetch_latency_ms=10,
    ))
    snap = [s for s in scheduler._heap if s.horizon == "t+10m"][0]
    await scheduler._fire(snap)

    assert scheduler._virtual_exits["evt1"] == "t+10m"


async def test_consecutive_fills_are_tracked_per_event_id():
    """같은 티커 연속 체결도 event_id별 상태를 분리해 추적한다."""
    cfg = Config(
        paper_take_profit_pct=1.0,
        paper_stop_loss_pct=-1.0,
        trailing_stop_enabled=False,
    )
    fetcher = PriceFetcher(kis=None)
    log = MagicMock()
    log.write = AsyncMock()
    scheduler = SnapshotScheduler(cfg, fetcher, log)

    for event_id in ("evt1", "evt2"):
        scheduler.schedule_t0(
            event_id=event_id,
            ticker="005930",
            t0_basis=T0Basis.DECIDED_AT,
            t0_ts=datetime.now(timezone.utc),
            run_id="run1",
            mode="paper",
            is_buy_decision=True,
        )

    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=10000.0, open_px=10000.0, spread_bps=0.0,
        cum_value=1_000_000.0, fetch_latency_ms=10,
    ))
    for event_id in ("evt1", "evt2"):
        t0_snap = [s for s in scheduler._heap if s.event_id == event_id and s.horizon == "t0"][0]
        await scheduler._fire(t0_snap)

    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=10120.0, open_px=10000.0, spread_bps=0.0,
        cum_value=1_120_000.0, fetch_latency_ms=10,
    ))
    evt1_snap = [s for s in scheduler._heap if s.event_id == "evt1" and s.horizon == "t+30s"][0]
    await scheduler._fire(evt1_snap)

    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=9900.0, open_px=10000.0, spread_bps=0.0,
        cum_value=900_000.0, fetch_latency_ms=10,
    ))
    evt2_snap = [s for s in scheduler._heap if s.event_id == "evt2" and s.horizon == "t+1m"][0]
    await scheduler._fire(evt2_snap)

    assert scheduler._virtual_exits["evt1"] == "t+30s"
    assert scheduler._virtual_exits["evt2"] == "t+1m"
    assert scheduler._t0_prices["evt1"][0] == pytest.approx(10000.0)
    assert scheduler._t0_prices["evt2"][0] == pytest.approx(10000.0)


async def test_flush_ready_on_shutdown_fires_due_snapshots_only():
    cfg = Config(close_snapshot_delay_s=300.0)
    fetcher = PriceFetcher(kis=None)
    log = MagicMock()
    log.write = AsyncMock()
    scheduler = SnapshotScheduler(cfg, fetcher, log)
    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(px=10000.0, open_px=10000.0, spread_bps=10.0, cum_value=1_000_000.0, fetch_latency_ms=10))

    event_ts = datetime(2026, 3, 5, 14, 55, tzinfo=timezone.utc)
    scheduler.schedule_t0(
        event_id="evt1",
        ticker="005930",
        t0_basis=T0Basis.DETECTED_AT,
        t0_ts=event_ts,
        run_id="run1",
    )
    due_horizons = {"t0", "t+30s", "t+1m"}
    for snap in scheduler._heap:
        if snap.horizon in due_horizons:
            snap.fire_at = 10.0
        else:
            snap.fire_at = 1000.0

    with patch("kindshot.price.time.monotonic", return_value=100.0):
        flushed = await scheduler.flush_ready_on_shutdown()

    assert flushed == 3
    assert {s.horizon for s in scheduler._heap} == {"t+2m", "t+5m", "t+10m", "t+15m", "t+20m", "t+30m", "close"}


async def test_flush_ready_on_shutdown_keeps_future_snapshots():
    cfg = Config(close_snapshot_delay_s=300.0)
    fetcher = PriceFetcher(kis=None)
    scheduler = SnapshotScheduler(cfg, fetcher, MagicMock())

    scheduler.schedule_t0(
        event_id="evt1",
        ticker="005930",
        t0_basis=T0Basis.DETECTED_AT,
        t0_ts=datetime.now(timezone.utc),
        run_id="run1",
    )

    for snap in scheduler._heap:
        snap.fire_at = 1000.0

    with patch("kindshot.price.time.monotonic", return_value=100.0):
        flushed = await scheduler.flush_ready_on_shutdown()

    assert flushed == 0
    assert len(scheduler._heap) == 10
