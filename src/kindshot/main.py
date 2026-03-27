"""Main entry point: asyncio supervisor orchestrating all components."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiohttp

from kindshot.config import Config, load_config
from kindshot.order import OrderExecutor
from kindshot.context_card import configure_cache as configure_context_card_cache
from kindshot.decision import DecisionEngine
from kindshot.event_registry import EventRegistry
from kindshot.feed import AnalystFeed, DartFeed, KindFeed, KisFeed, MultiFeed
from kindshot.guardrails import GuardrailState
from kindshot.kis_client import KisClient
from kindshot.logger import JsonlLogger, LogWriteError
from kindshot.market import MarketMonitor
from kindshot.performance import PerformanceTracker
from kindshot.pattern_profile import build_recent_pattern_profile
from kindshot.pipeline import (
    RuntimeCounters,
    counter_snapshot,
    pipeline_loop,
    process_unknown_promotion,
)
from kindshot.poll_trace import init_tracer
from kindshot.price import PriceFetcher, SnapshotScheduler
from kindshot.sd_notify import notify_ready, notify_watchdog
from kindshot.tz import KST as _KST
from kindshot.health import HealthState, start_health_server
from kindshot.telegram_ops import DailySummaryNotifier, telegram_configured, try_send_daily_summary, try_send_sell_signal
from kindshot.unknown_review import (
    UnknownReviewEngine,
    append_unknown_review,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-20s %(levelname)-5s %(message)s",
)
logger = logging.getLogger(__name__)


def _run_mode(config: Config) -> str:
    """Return the run mode string for log records."""
    if config.dry_run:
        return "dry_run"
    if config.paper:
        return "paper"
    return "live"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="kindshot MVP")
    p.add_argument("--dry-run", action="store_true", help="Skip LLM calls, log events only")
    p.add_argument("--paper", action="store_true", help="Full pipeline (incl. LLM) but no order execution")
    p.add_argument("--replay", type=str, default=None, metavar="JSONL_PATH",
                   help="Replay mode: re-run LLM decisions on logged events")
    p.add_argument("--replay-runtime-date", type=str, default=None, metavar="YYYYMMDD",
                   help="Replay mode: re-run decisions from runtime artifacts for a KST date")
    p.add_argument("--replay-day", type=str, default=None, metavar="YYYYMMDD",
                   help="Replay mode: re-run decisions from the combined collector/runtime day bundle")
    p.add_argument("--replay-report-out", type=str, default=None, metavar="JSON_PATH",
                   help="Optional path to write a machine-readable replay report JSON")
    p.add_argument("--replay-day-status", type=str, default=None, metavar="YYYYMMDD",
                   help="Replay mode: inspect combined collector/runtime day inputs before execution")
    p.add_argument("--replay-status-out", type=str, default=None, metavar="JSON_PATH",
                   help="Optional path to write a machine-readable replay day status JSON")
    p.add_argument("--replay-ops-summary", action="store_true",
                   help="Replay mode: summarize replay readiness across multiple dates")
    p.add_argument("--replay-ops-limit", type=int, default=10, metavar="N",
                   help="Replay ops summary: number of latest dates to include in printed rows")
    p.add_argument("--replay-ops-out", type=str, default=None, metavar="JSON_PATH",
                   help="Optional path to write a machine-readable replay ops summary JSON")
    p.add_argument("--replay-ops-queue-ready", action="store_true",
                   help="Replay mode: build a policy-controlled ready queue without executing replay-day")
    p.add_argument("--replay-ops-run-ready", action="store_true",
                   help="Replay mode: execute replay-day for ready dates without existing day reports")
    p.add_argument("--replay-ops-cycle-ready", action="store_true",
                   help="Replay mode: queue, execute, and summarize ready dates in one batch")
    p.add_argument("--replay-ops-run-limit", type=int, default=5, metavar="N",
                   help="Replay ops queue/run: max number of ready dates to select")
    p.add_argument("--replay-ops-include-reported", action="store_true",
                   help="Replay ops queue/run: include dates that already have persisted day reports")
    p.add_argument("--replay-ops-require-runtime", action="store_true",
                   help="Replay ops queue/run: require runtime artifacts to be present")
    p.add_argument("--replay-ops-require-collector", action="store_true",
                   help="Replay ops queue/run: require collector artifacts to be present")
    p.add_argument("--replay-ops-min-merged-events", type=int, default=1, metavar="N",
                   help="Replay ops queue/run: minimum merged replayable events required for selection")
    p.add_argument("--replay-ops-queue-out", type=str, default=None, metavar="JSON_PATH",
                   help="Optional path to write a machine-readable replay ops queue JSON")
    p.add_argument("--replay-ops-run-out", type=str, default=None, metavar="JSON_PATH",
                   help="Optional path to write a machine-readable replay ops run JSON")
    p.add_argument("--replay-ops-cycle-out", type=str, default=None, metavar="JSON_PATH",
                   help="Optional path to write a machine-readable replay ops cycle JSON")
    p.add_argument("--replay-ops-continue-on-error", action="store_true",
                   help="Replay ops cycle: continue executing later selected dates after a replay-day failure")
    p.add_argument("--unknown-review-summary", action="store_true",
                   help="Summarize UNKNOWN inbox/review/promotion activity across recent dates")
    p.add_argument("--unknown-review-limit", type=int, default=10, metavar="N",
                   help="UNKNOWN review summary: number of latest dates to include in printed rows")
    p.add_argument("--unknown-review-out", type=str, default=None, metavar="JSON_PATH",
                   help="Optional path to write a machine-readable UNKNOWN review summary JSON")
    p.add_argument("--unknown-review-rule-report", action="store_true",
                   help="Build a rule-curation report from UNKNOWN review and promotion logs")
    p.add_argument("--unknown-review-rule-limit", type=int, default=10, metavar="N",
                   help="UNKNOWN review rule report: number of latest dates to include in printed rows")
    p.add_argument("--unknown-review-rule-out", type=str, default=None, metavar="JSON_PATH",
                   help="Optional path to write a machine-readable UNKNOWN review rule report JSON")
    p.add_argument("--unknown-review-rule-queue", action="store_true",
                   help="Build a filtered rule-candidate queue from UNKNOWN review reports")
    p.add_argument("--unknown-review-rule-queue-limit", type=int, default=10, metavar="N",
                   help="UNKNOWN review rule queue: number of selected rows to include in printed output")
    p.add_argument("--unknown-review-rule-queue-out", type=str, default=None, metavar="JSON_PATH",
                   help="Optional path to write a machine-readable UNKNOWN review rule queue JSON")
    p.add_argument("--unknown-review-rule-patch", action="store_true",
                   help="Build an editable deterministic bucket patch draft from the UNKNOWN rule queue")
    p.add_argument("--unknown-review-rule-patch-limit", type=int, default=20, metavar="N",
                   help="UNKNOWN review rule patch: number of draft rows to include in printed output")
    p.add_argument("--unknown-review-rule-patch-out", type=str, default=None, metavar="JSON_PATH",
                   help="Optional path to write a machine-readable UNKNOWN rule patch JSON")
    return p.parse_args()


async def _wait_or_stop(stop_event: asyncio.Event, timeout_s: float) -> None:
    """Sleep until timeout or until stop_event is set."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=timeout_s)
    except asyncio.TimeoutError:
        pass


async def _watchdog_loop(
    feed, counters, config: Config, stop_event: asyncio.Event
) -> None:
    """Periodically notify systemd watchdog and log heartbeat."""
    while not stop_event.is_set():
        now = datetime.now(_KST)
        last_poll = feed.last_poll_at
        if last_poll and (now - last_poll).total_seconds() < config.watchdog_stale_threshold_s:
            notify_watchdog()
            events = counters.totals.get("events_seen", 0) if counters else 0
            logger.info(
                "Heartbeat: last_poll=%s, events_seen=%d",
                last_poll.strftime("%H:%M:%S"), events,
            )
        else:
            stale_s = (now - last_poll).total_seconds() if last_poll else -1
            logger.warning("Watchdog: feed stale (%.0fs), NOT notifying systemd", stale_s)
        await _wait_or_stop(stop_event, config.watchdog_interval_s)


def _build_feed(config, feed_source: str, kis, session, state_dir):
    """Build feed instance(s) from config. Returns (feed, feed_source_label)."""
    sources = [s.strip() for s in feed_source.split(",") if s.strip()]
    if not sources:
        sources = ["KIS"]

    feeds = []
    labels = []

    for src in sources:
        if src == "KIS" and kis:
            feeds.append(KisFeed(config, kis, state_dir=state_dir / "feed"))
            labels.append("KIS")
        elif src == "DART" and config.dart_api_key:
            feeds.append(DartFeed(config, session, state_dir=state_dir / "feed_dart"))
            labels.append("DART")
        elif src == "KIND":
            feeds.append(KindFeed(config, session))
            labels.append("KIND")
        else:
            if src == "KIS" and not kis:
                logger.warning("KIS feed requested but KIS client disabled — skipping")
            elif src == "DART" and not config.dart_api_key:
                logger.warning("DART feed requested but DART_API_KEY not set — skipping")

    # v68: AnalystFeed 보조 피드 추가 (KIS 필요)
    if config.analyst_feed_enabled and kis:
        feeds.append(AnalystFeed(config, kis))
        labels.append("AnalystFeed")
        logger.info("AnalystFeed enabled (interval=%.0fs)", config.analyst_feed_interval_s)
    elif config.analyst_feed_enabled and not kis:
        logger.warning("AnalystFeed requested but KIS client disabled — skipping")

    if not feeds:
        logger.warning("No valid feed sources — falling back to KIND RSS")
        feeds.append(KindFeed(config, session))
        labels = ["KIND"]

    if len(feeds) == 1:
        feed = feeds[0]
    else:
        feed = MultiFeed(feeds, config)

    label = ",".join(labels)
    logger.info("Feed source: %s", label)
    return feed, label


async def run() -> None:
    args = _parse_args()
    if getattr(args, "dry_run", False) and getattr(args, "paper", False):
        logger.error("--dry-run and --paper are mutually exclusive")
        raise SystemExit(1)
    config = load_config(dry_run=args.dry_run, paper=getattr(args, "paper", False))
    mode = _run_mode(config)
    run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    logger.info("kindshot %s starting (run_id=%s, mode=%s)", config.schema_version, run_id, mode)

    log = JsonlLogger(config.log_dir, run_id=run_id)
    tracer = init_tracer(config.log_dir)
    counters = RuntimeCounters()

    async with aiohttp.ClientSession() as session:
        # KIS client (optional)
        kis: Optional[KisClient] = None
        if config.kis_enabled:
            kis = KisClient(config, session)
            logger.info("KIS client enabled")
            if config.kis_is_paper and not config.kis_real_app_key:
                logger.warning(
                    "⚠ Paper mode WITHOUT real API keys — price snapshots will use VTS (stale prices). "
                    "Set KIS_REAL_APP_KEY and KIS_REAL_APP_SECRET for real-time market data."
                )
        else:
            logger.warning("KIS client disabled — market monitor will block trading (fail-close), price snapshots UNAVAILABLE")

        state_dir = config.log_dir / "state" / mode
        feed_source = config.feed_source.upper()
        feed, feed_source = _build_feed(config, feed_source, kis, session, state_dir)
        registry = EventRegistry(state_dir=state_dir)
        decision_engine = DecisionEngine(config)
        unknown_review_engine = UnknownReviewEngine(config) if config.unknown_shadow_review_enabled else None
        market = MarketMonitor(config, kis)
        fetcher = PriceFetcher(kis=kis)
        configure_context_card_cache(config.pykrx_cache_ttl_s, config.pykrx_cache_max_size)

        # Graceful shutdown
        stop_event = asyncio.Event()

        def _signal_handler() -> None:
            stop_event.set()
            feed.stop()

        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            signal.signal(signal.SIGINT, lambda *_: _signal_handler())
            try:
                signal.signal(signal.SIGTERM, lambda *_: _signal_handler())
            except (AttributeError, ValueError):
                # SIGTERM may be unavailable on some Windows runtimes.
                pass

        guardrail_state = GuardrailState(config, state_dir=state_dir)
        performance_tracker = PerformanceTracker(config.data_dir)
        recent_pattern_profile = build_recent_pattern_profile(config)
        recent_pattern_path = config.recent_pattern_profile_path
        recent_pattern_path.parent.mkdir(parents=True, exist_ok=True)
        recent_pattern_path.write_text(
            json.dumps(recent_pattern_profile.summary(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        daily_summary_notifier = (
            DailySummaryNotifier(state_dir / "daily_summary_telegram_state.json", close_delay_s=config.close_snapshot_delay_s)
            if telegram_configured()
            else None
        )

        # Order executor (live mode only)
        order_executor: Optional[OrderExecutor] = None
        if mode == "live" and kis is not None:
            order_executor = OrderExecutor(kis, config)
            logger.info("OrderExecutor enabled (micro_live_max=%.0f won)", config.micro_live_max_order_won)

        def _on_trade_close(
            *,
            event_id: str,
            ticker: str,
            entry_px: float,
            exit_px: float,
            ret_pct: float,
            pnl_won: float,
            exit_type: str,
            horizon: str,
            hold_seconds: int,
            size_won: float,
            confidence: int,
            mode: str,
            position_closed: bool = True,
            remaining_size_won: float = 0.0,
            exit_fraction: float = 1.0,
            initial_size_won: float = 0.0,
            cumulative_pnl_won: float | None = None,
            cumulative_ret_pct: float | None = None,
            average_exit_px: float | None = None,
        ) -> None:
            guardrail_state.record_pnl(pnl_won)
            final_pnl_won = cumulative_pnl_won if cumulative_pnl_won is not None else pnl_won
            final_ret_pct = cumulative_ret_pct if cumulative_ret_pct is not None else ret_pct
            final_exit_px = average_exit_px if average_exit_px is not None else exit_px
            final_size_won = initial_size_won if initial_size_won > 0 else size_won
            if position_closed:
                guardrail_state.record_sell(ticker)
                if final_pnl_won < 0:
                    guardrail_state.record_stop_loss()
                else:
                    guardrail_state.record_profitable_exit()
                try:
                    performance_tracker.record_trade(
                        ticker,
                        entry_px,
                        final_exit_px,
                        final_ret_pct,
                        event_id=event_id,
                        size_won=final_size_won,
                        hold_seconds=hold_seconds,
                        exit_type=exit_type,
                        confidence=confidence,
                        position_closed=position_closed,
                        remaining_size_won=remaining_size_won,
                        initial_size_won=initial_size_won,
                        exit_fraction=exit_fraction,
                        cumulative_pnl_won=final_pnl_won,
                        cumulative_ret_pct=final_ret_pct,
                    )
                except Exception:
                    logger.warning("Failed to record trade for %s", ticker, exc_info=True)
            try_send_sell_signal(
                ticker=ticker,
                exit_type=exit_type,
                horizon=horizon,
                ret_pct=ret_pct,
                pnl_won=pnl_won,
                confidence=confidence,
                size_won=size_won,
                hold_seconds=hold_seconds,
                mode=mode,
                open_positions=guardrail_state.position_count,
                position_closed=position_closed,
                remaining_size_won=remaining_size_won,
                exit_fraction=exit_fraction,
                cumulative_pnl_won=final_pnl_won if position_closed else 0.0,
                cumulative_ret_pct=final_ret_pct if position_closed else 0.0,
            )
            logger.info(
                "Trade close event: %s %s %.0f won (ret=%.2f%%, final=%s, remain=%.0f, daily total: %.0f, positions: %d)",
                ticker,
                exit_type,
                pnl_won,
                ret_pct,
                position_closed,
                remaining_size_won,
                guardrail_state.daily_pnl,
                guardrail_state.position_count,
            )

        scheduler = SnapshotScheduler(
            config, fetcher, log,
            stop_event=stop_event,
            trade_close_callback=_on_trade_close,
            order_executor=order_executor,
        )
        unknown_review_queue: Optional[asyncio.Queue] = None
        if config.unknown_shadow_review_enabled:
            unknown_review_queue = asyncio.Queue(maxsize=max(1, config.unknown_review_queue_maxsize))

        async def _unknown_review_loop() -> None:
            if unknown_review_engine is None or unknown_review_queue is None:
                return
            while not stop_event.is_set():
                try:
                    item = await asyncio.wait_for(unknown_review_queue.get(), timeout=2.0)
                except asyncio.TimeoutError:
                    continue
                try:
                    if item is None:
                        return
                    reviews = await unknown_review_engine.review_with_optional_article(item)
                    for review in reviews:
                        append_unknown_review(config, item.detected_at, review)
                    latest_review = reviews[-1]
                    if config.unknown_paper_promotion_enabled:
                        await process_unknown_promotion(
                            request=item,
                            review=latest_review,
                            decision_engine=decision_engine,
                            market=market,
                            scheduler=scheduler,
                            log=log,
                            config=config,
                            run_id=run_id,
                            kis=kis,
                            counters=counters,
                            guardrail_state=guardrail_state,
                        )
                except Exception:
                    logger.warning("UNKNOWN review worker failed", exc_info=True)
                finally:
                    unknown_review_queue.task_done()

        # Market monitor task (update every 60s)
        async def _market_loop() -> None:
            while not stop_event.is_set():
                try:
                    guardrail_state.check_daily_reset()
                    await market.update()
                    await market.append_runtime_snapshot()
                except Exception:
                    logger.exception("Market monitor error")
                await _wait_or_stop(stop_event, 60)

        async def _daily_summary_loop() -> None:
            if daily_summary_notifier is None:
                return
            while not stop_event.is_set():
                try:
                    if daily_summary_notifier.should_send():
                        summary = performance_tracker.daily_summary()
                        report_path = str(performance_tracker.summary_path()) if summary.total_trades > 0 else ""
                        sent = try_send_daily_summary(
                            summary,
                            open_positions=guardrail_state.position_count,
                            daily_pnl_won=guardrail_state.daily_pnl,
                            consecutive_stop_losses=guardrail_state.consecutive_stop_losses,
                            report_path=report_path,
                        )
                        if sent:
                            performance_tracker.flush()
                            daily_summary_notifier.mark_sent(summary.date)
                            logger.info("Daily summary telegram sent for %s", summary.date)
                except Exception:
                    logger.exception("Daily summary loop error")
                await _wait_or_stop(stop_event, 60)

        # Health check server
        health_state = HealthState(latency_window_size=config.health_latency_window_size)
        health_state.set_guardrail_state(guardrail_state)
        health_state.set_llm_client(decision_engine._llm)
        health_state.set_decision_engine(decision_engine)
        health_state.set_feed(feed)
        health_state.set_performance_tracker(performance_tracker)
        health_state.set_recent_pattern_profile(recent_pattern_profile)
        health_runner = None
        try:
            health_runner, _health_task = await start_health_server(
                health_state, host=config.health_host, port=config.health_port,
            )
        except Exception:
            logger.warning("Health server failed to start", exc_info=True)

        notify_ready()

        tasks = [
            asyncio.create_task(pipeline_loop(
                feed, registry, decision_engine, market, scheduler, log, config, run_id, kis, counters, mode,
                stop_event=stop_event,
                guardrail_state=guardrail_state,
                feed_source=feed_source,
                unknown_review_queue=unknown_review_queue,
                health_state=health_state,
                order_executor=order_executor,
                recent_pattern_profile=recent_pattern_profile,
            ), name="pipeline"),
            asyncio.create_task(scheduler.run(), name="snapshots"),
            asyncio.create_task(_market_loop(), name="market"),
            asyncio.create_task(_watchdog_loop(feed, counters, config, stop_event), name="watchdog"),
        ]
        if daily_summary_notifier is not None:
            tasks.append(asyncio.create_task(_daily_summary_loop(), name="daily-summary"))
        if unknown_review_queue is not None:
            tasks.append(asyncio.create_task(_unknown_review_loop(), name="unknown-review"))

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            # Idempotent shutdown to cover both signaled and non-signaled exits.
            stop_event.set()
            feed.stop()
            flushed_ready = 0
            try:
                flushed_ready = await scheduler.flush_ready_on_shutdown()
            except LogWriteError:
                logger.critical("Ready snapshot shutdown flush failed — stopping runtime")
            if unknown_review_queue is not None:
                # Sentinel 투입 후 최대 5초 대기 (무한 블로킹 방지)
                await unknown_review_queue.put(None)
                try:
                    await asyncio.wait_for(unknown_review_queue.join(), timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning("Unknown review queue drain timed out (5s)")
            scheduler.stop()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if kis is not None:
                logger.info("KIS client stats: %s", kis.stats_snapshot())
            logger.info("Runtime counters: %s", counter_snapshot(counters))
            if flushed_ready:
                logger.info("Shutdown flushed ready snapshots: %d", flushed_ready)
            if health_runner is not None:
                await health_runner.cleanup()
            logger.info("Shutdown complete. Pending future snapshots lost: %d", scheduler.pending_count)
