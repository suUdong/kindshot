"""Lightweight health check HTTP server for monitoring."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from aiohttp import web

from kindshot.tz import KST as _KST

logger = logging.getLogger(__name__)


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
        self.llm_fallback_count: int = 0
        self.kis_calls: int = 0
        self.kis_errors: int = 0
        # Guardrail state references (set by main.py after init)
        self._guardrail_state: Optional[Any] = None
        self._llm_client: Optional[Any] = None
        self._feed: Optional[Any] = None
        self._performance_tracker: Optional[Any] = None
        self._recent_pattern_profile: Optional[Any] = None
        # Guardrail block tracking
        self.guardrail_blocks: dict[str, int] = {}

    def set_guardrail_state(self, state: Any) -> None:
        self._guardrail_state = state

    def set_llm_client(self, client: Any) -> None:
        self._llm_client = client

    def set_feed(self, feed: Any) -> None:
        self._feed = feed

    def set_performance_tracker(self, tracker: Any) -> None:
        self._performance_tracker = tracker

    def set_recent_pattern_profile(self, profile: Any) -> None:
        self._recent_pattern_profile = profile

    def record_poll(self, polled_at: Optional[datetime | str] = None) -> None:
        if isinstance(polled_at, datetime):
            if polled_at.tzinfo is None:
                polled_at = polled_at.replace(tzinfo=_KST)
            else:
                polled_at = polled_at.astimezone(_KST)
            self.last_poll_at = polled_at.isoformat()
            return
        if isinstance(polled_at, str):
            self.last_poll_at = polled_at
            return
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

    def record_llm_fallback(self) -> None:
        self.llm_fallback_count += 1

    def record_guardrail_block(self, reason: str) -> None:
        self.guardrail_blocks[reason] = self.guardrail_blocks.get(reason, 0) + 1

    def record_error(self) -> None:
        self.error_count += 1

    def record_kis_call(self, success: bool = True) -> None:
        self.kis_calls += 1
        if not success:
            self.kis_errors += 1

    def _resolve_last_poll(self) -> tuple[str, str, int | None]:
        source = "internal"
        poll_iso = self.last_poll_at
        poll_dt: datetime | None = None

        feed_poll = getattr(self._feed, "last_poll_at", None) if self._feed is not None else None
        if isinstance(feed_poll, datetime):
            poll_dt = feed_poll if feed_poll.tzinfo is not None else feed_poll.replace(tzinfo=_KST)
            poll_iso = poll_dt.astimezone(_KST).isoformat()
            source = "feed"
        elif isinstance(feed_poll, str) and feed_poll:
            poll_iso = feed_poll
            source = "feed"
            try:
                poll_dt = datetime.fromisoformat(feed_poll)
            except ValueError:
                poll_dt = None
        elif poll_iso:
            try:
                poll_dt = datetime.fromisoformat(poll_iso)
            except ValueError:
                poll_dt = None
        else:
            source = "unknown"

        age_s = None
        if poll_dt is not None:
            if poll_dt.tzinfo is None:
                poll_dt = poll_dt.replace(tzinfo=_KST)
            age_s = max(0, int((datetime.now(_KST) - poll_dt.astimezone(_KST)).total_seconds()))
        return poll_iso, source, age_s

    def snapshot(self) -> dict[str, Any]:
        uptime_s = (datetime.now(_KST) - datetime.fromisoformat(self.started_at)).total_seconds()
        avg_llm_ms = self.llm_total_ms / self.llm_calls if self.llm_calls > 0 else 0
        last_poll_at, last_poll_source, last_poll_age_seconds = self._resolve_last_poll()

        result: dict[str, Any] = {
            "status": "healthy" if self.error_count < 10 else "degraded",
            "started_at": self.started_at,
            "uptime_seconds": int(uptime_s),
            "last_poll_at": last_poll_at,
            "last_poll_source": last_poll_source,
            "last_poll_age_seconds": last_poll_age_seconds,
            "last_event_at": self.last_event_at,
            "events_seen": self.events_seen,
            "events_processed": self.events_processed,
            "buy_count": self.buy_count,
            "skip_count": self.skip_count,
            "error_count": self.error_count,
            "llm_calls": self.llm_calls,
            "llm_avg_ms": int(avg_llm_ms),
            "llm_fallback_count": self.llm_fallback_count,
            "kis_calls": self.kis_calls,
            "kis_errors": self.kis_errors,
        }

        # Circuit breaker status
        if self._llm_client is not None:
            result["circuit_breaker"] = {
                "nvidia_open": getattr(self._llm_client, "nvidia_circuit_open", False),
                "anthropic_open": getattr(self._llm_client, "circuit_open", False),
            }

        # Guardrail state
        if self._guardrail_state is not None:
            gs = self._guardrail_state
            result["guardrail_state"] = {
                "daily_pnl": getattr(gs, "daily_pnl", 0.0),
                "position_count": getattr(gs, "position_count", 0),
                "consecutive_stop_losses": getattr(gs, "consecutive_stop_losses", 0),
                "bought_tickers_count": len(getattr(gs, "bought_tickers", set())),
                "dynamic_daily_loss_floor_won": getattr(gs, "dynamic_daily_loss_floor_won", 0.0),
                "dynamic_daily_loss_remaining_won": getattr(gs, "dynamic_daily_loss_remaining_won", 0.0),
            }

        if self.guardrail_blocks:
            result["guardrail_blocks"] = dict(self.guardrail_blocks)

        if self._performance_tracker is not None and hasattr(self._performance_tracker, "live_metrics"):
            result["trade_metrics"] = self._performance_tracker.live_metrics()

        if self._recent_pattern_profile is not None and hasattr(self._recent_pattern_profile, "summary"):
            result["recent_pattern_profile"] = self._recent_pattern_profile.summary()

        return result


async def _health_handler(request: web.Request) -> web.Response:
    state: HealthState = request.app["health_state"]
    return web.json_response(state.snapshot())


async def start_health_server(
    state: HealthState,
    *,
    host: str = "127.0.0.1",
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
