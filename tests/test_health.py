"""Tests for health check server and state."""

import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from kindshot.health import HealthState, _health_handler


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


def test_health_state_degraded():
    state = HealthState()
    for _ in range(10):
        state.record_error()
    snap = state.snapshot()
    assert snap["status"] == "degraded"


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
