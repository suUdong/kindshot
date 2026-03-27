"""Tests for health check server and state."""

from datetime import datetime, timedelta, timezone

import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from kindshot.health import HealthState, _health_handler, start_health_server
from kindshot.guardrails import GuardrailState
from kindshot.models import PipelineLatencyProfile
from kindshot.performance import PerformanceTracker
from kindshot.pattern_profile import RecentPatternProfile

_KST = timezone(timedelta(hours=9))


def test_health_state_defaults():
    state = HealthState()
    snap = state.snapshot()
    assert snap["status"] == "healthy"
    assert snap["events_seen"] == 0
    assert snap["error_count"] == 0
    assert snap["llm_avg_ms"] == 0


def test_health_state_records():
    state = HealthState()
    state.record_poll()
    state.record_event()
    state.record_event()
    state.record_decision("BUY", latency_ms=100)
    state.record_decision("SKIP", latency_ms=200)
    state.record_error()
    state.record_kis_call(success=True)
    state.record_kis_call(success=False)

    snap = state.snapshot()
    assert snap["events_seen"] == 2
    assert snap["events_processed"] == 2
    assert snap["buy_count"] == 1
    assert snap["skip_count"] == 1
    assert snap["error_count"] == 1
    assert snap["llm_calls"] == 2
    assert snap["llm_avg_ms"] == 150
    assert snap["kis_calls"] == 2
    assert snap["kis_errors"] == 1
    assert snap["last_poll_at"] != ""


def test_health_snapshot_prefers_feed_last_poll_and_exposes_trade_metrics(tmp_path):
    state = HealthState()
    tracker = PerformanceTracker(tmp_path)
    tracker.record_trade("evt1", "005930", 50000, 50500, 1.0, size_won=5_000_000)
    tracker.record_trade("evt2", "035420", 30000, 29400, -2.0, size_won=5_000_000)
    tracker.record_trade("evt3", "000660", 80000, 80400, 0.5, size_won=5_000_000)

    feed_poll_at = datetime.now(_KST) - timedelta(seconds=7)
    feed = type("FeedStub", (), {"last_poll_at": feed_poll_at})()

    state.record_poll()
    state.set_feed(feed)
    state.set_performance_tracker(tracker)

    snap = state.snapshot()

    assert snap["last_poll_at"] == feed_poll_at.isoformat()
    assert snap["last_poll_source"] == "feed"
    assert snap["last_poll_age_seconds"] >= 0
    assert snap["trade_metrics"]["total_trades"] == 3
    assert snap["trade_metrics"]["wins"] == 2
    assert snap["trade_metrics"]["losses"] == 1
    assert snap["trade_metrics"]["total_pnl_pct"] == -0.5
    assert snap["trade_metrics"]["mdd_pct"] == -2.0


def test_health_state_degraded():
    state = HealthState()
    for _ in range(10):
        state.record_error()
    snap = state.snapshot()
    assert snap["status"] == "degraded"


def test_health_snapshot_exposes_recent_pattern_profile():
    state = HealthState()
    profile = RecentPatternProfile(
        enabled=True,
        analysis_dates=("20260320", "20260327"),
        total_trades=6,
        boost_patterns=(),
        loss_guardrail_patterns=(),
    )
    state.set_recent_pattern_profile(profile)

    snap = state.snapshot()
    assert snap["recent_pattern_profile"]["enabled"] is True
    assert snap["recent_pattern_profile"]["analysis_dates"] == ["20260320", "20260327"]


def test_health_snapshot_exposes_extended_guardrail_state():
    from kindshot.config import Config

    cfg = Config(
        dynamic_daily_loss_recent_trade_window=4,
        dynamic_daily_loss_recent_trade_min_samples=3,
    )
    guardrail_state = GuardrailState(cfg)
    guardrail_state.record_buy("005930", sector="반도체")
    guardrail_state.record_profitable_exit()
    guardrail_state.record_stop_loss()
    guardrail_state.record_stop_loss()

    state = HealthState()
    state.set_guardrail_state(guardrail_state)
    snap = state.snapshot()

    assert snap["guardrail_state"]["configured_max_positions"] == cfg.max_positions
    assert snap["guardrail_state"]["sector_positions"] == {"반도체": 1}
    assert snap["guardrail_state"]["recent_closed_trades"] == 3
    assert snap["guardrail_state"]["recent_win_rate"] == pytest.approx(1 / 3)
    assert snap["guardrail_state"]["consecutive_loss_halt_threshold"] == cfg.consecutive_loss_halt


def test_health_snapshot_exposes_latency_profile_and_llm_cache():
    class EngineStub:
        def cache_stats(self):
            return {
                "memory_entries": 2,
                "memory_hits": 3,
                "disk_hits": 1,
                "inflight_hits": 0,
                "misses": 4,
                "writes": 4,
                "disk_errors": 0,
            }

    state = HealthState(latency_window_size=4)
    state.set_decision_engine(EngineStub())
    state.record_pipeline_profile(
        PipelineLatencyProfile(
            news_to_pipeline_ms=180,
            context_card_ms=40,
            decision_total_ms=90,
            guardrail_ms=5,
            pipeline_total_ms=120,
            llm_latency_ms=70,
            llm_cache_layer="disk",
            bottleneck_stage="decision",
        ),
        decision_source="CACHE",
    )

    snap = state.snapshot()

    assert snap["latency_profile"]["stages"]["pipeline_total_ms"]["avg_ms"] == 120
    assert snap["latency_profile"]["bottlenecks"]["decision"] == 1
    assert snap["latency_profile"]["cache_layers"]["disk"] == 1
    assert snap["llm_cache"]["disk_hits"] == 1


def test_health_server_default_bind_is_localhost():
    """start_health_server defaults to 127.0.0.1 (not 0.0.0.0)."""
    import inspect
    sig = inspect.signature(start_health_server)
    assert sig.parameters["host"].default == "127.0.0.1"


@pytest.mark.asyncio
async def test_health_endpoint():
    state = HealthState()
    state.record_event()

    app = web.Application()
    app["health_state"] = state
    app.router.add_get("/health", _health_handler)

    from aiohttp.test_utils import TestClient, TestServer
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["events_seen"] == 1
        assert data["status"] == "healthy"
