"""Lightweight health check HTTP server for monitoring."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from aiohttp import web

logger = logging.getLogger(__name__)
_KST = timezone(timedelta(hours=9))


class HealthState:
    """Shared mutable state exposed via /health endpoint."""

    def __init__(self) -> None:
        self.started_at: str = datetime.now(_KST).isoformat()
        self.last_poll_at: str = ""
        self.last_event_at: str = ""
        self.events_seen: int = 0
        self.events_processed: int = 0
        self.buy_count: int = 0
        self.skip_count: int = 0
        self.error_count: int = 0
        self.llm_calls: int = 0
        self.llm_total_ms: int = 0
        self.kis_calls: int = 0
        self.kis_errors: int = 0

    def record_poll(self) -> None:
        self.last_poll_at = datetime.now(_KST).isoformat()

    def record_event(self) -> None:
        self.events_seen += 1
        self.last_event_at = datetime.now(_KST).isoformat()

    def record_decision(self, action: str, latency_ms: int = 0) -> None:
        self.events_processed += 1
        self.llm_calls += 1
        self.llm_total_ms += latency_ms
        if action == "BUY":
            self.buy_count += 1
        else:
            self.skip_count += 1

    def record_error(self) -> None:
        self.error_count += 1

    def record_kis_call(self, success: bool = True) -> None:
        self.kis_calls += 1
        if not success:
            self.kis_errors += 1

    def snapshot(self) -> dict[str, Any]:
        uptime_s = (datetime.now(_KST) - datetime.fromisoformat(self.started_at)).total_seconds()
        avg_llm_ms = self.llm_total_ms / self.llm_calls if self.llm_calls > 0 else 0
        return {
            "status": "healthy" if self.error_count < 10 else "degraded",
            "started_at": self.started_at,
            "uptime_seconds": int(uptime_s),
            "last_poll_at": self.last_poll_at,
            "last_event_at": self.last_event_at,
            "events_seen": self.events_seen,
            "events_processed": self.events_processed,
            "buy_count": self.buy_count,
            "skip_count": self.skip_count,
            "error_count": self.error_count,
            "llm_calls": self.llm_calls,
            "llm_avg_ms": int(avg_llm_ms),
            "kis_calls": self.kis_calls,
            "kis_errors": self.kis_errors,
        }


async def _health_handler(request: web.Request) -> web.Response:
    state: HealthState = request.app["health_state"]
    return web.json_response(state.snapshot())


async def start_health_server(
    state: HealthState,
    *,
    host: str = "0.0.0.0",
    port: int = 8080,
) -> tuple[web.AppRunner, asyncio.Task]:
    """Start health check server. Returns (runner, serve_task) for cleanup."""
    app = web.Application()
    app["health_state"] = state
    app.router.add_get("/health", _health_handler)

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    task = asyncio.create_task(site.start())
    logger.info("Health server started on %s:%d", host, port)
    return runner, task
