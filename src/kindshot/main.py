"""Main entry point: asyncio supervisor orchestrating all components."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass, field
import hashlib
import json
import logging
import signal
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import aiohttp

from kindshot.bucket import classify
from kindshot.config import Config, load_config
from kindshot.context_card import ContextCardData, build_context_card, configure_cache as configure_context_card_cache
from kindshot.decision import DecisionEngine, LlmCallError, LlmTimeoutError, LlmParseError
from kindshot.event_registry import EventRegistry, ProcessedEvent
from kindshot.feed import KindFeed, KisFeed, RawDisclosure
from kindshot.guardrails import GuardrailState, check_guardrails
from kindshot.kis_client import KisClient
from kindshot.logger import JsonlLogger, LogWriteError
from kindshot.market import MarketMonitor
from kindshot.models import (
    Action,
    Bucket,
    ContextCard,
    EventIdMethod,
    EventKind,
    EventRecord,
    MarketContext,
    SkipStage,
    T0Basis,
)
from kindshot.price import PriceFetcher, SnapshotScheduler
from kindshot.quant import quant_check
from kindshot.poll_trace import init_tracer, get_tracer
from kindshot.sd_notify import notify_ready, notify_watchdog

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-20s %(levelname)-5s %(message)s",
)
logger = logging.getLogger(__name__)


_KST = timezone(timedelta(hours=9))


def _append_unknown_headline(log_dir: Path, headline: str, ticker: str) -> None:
    """Append UNKNOWN-bucket headline to daily file for keyword review."""
    try:
        today = datetime.now(_KST).strftime("%Y-%m-%d")
        path = log_dir / "unknown_headlines" / f"{today}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"headline": headline, "ticker": ticker}, ensure_ascii=False)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        logger.debug("Failed to write unknown headline")


@dataclass
class RuntimeCounters:
    totals: Counter[str] = field(default_factory=Counter)
    skip_stage: Counter[str] = field(default_factory=Counter)
    skip_reason: Counter[str] = field(default_factory=Counter)
    errors: Counter[str] = field(default_factory=Counter)


def _mark_skip(
    counters: Optional[RuntimeCounters],
    *,
    stage: Optional[str] = None,
    reason: Optional[str] = None,
) -> None:
    if counters is None:
        return
    counters.totals["events_skipped"] += 1
    if stage:
        counters.skip_stage[stage] += 1
    if reason:
        counters.skip_reason[reason] += 1


def _counter_snapshot(counters: RuntimeCounters) -> dict[str, dict[str, int]]:
    return {
        "totals": dict(counters.totals),
        "skip_stage": dict(counters.skip_stage),
        "skip_reason": dict(counters.skip_reason),
        "errors": dict(counters.errors),
    }


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
    _KST = timezone(timedelta(hours=9))
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


async def _process_registered_event(
    raw: RawDisclosure,
    processed: ProcessedEvent,
    decision_engine: DecisionEngine,
    market: MarketMonitor,
    scheduler: SnapshotScheduler,
    log: JsonlLogger,
    config: Config,
    run_id: str,
    kis: Optional[KisClient],
    counters: Optional[RuntimeCounters],
    mode: str = "live",
    guardrail_state: Optional[GuardrailState] = None,
    feed_source: str = "KIND",
) -> None:
    """Process an event that already passed dedup/registry."""
    _tracer = get_tracer()
    _t_proc = _tracer.process_start(processed.event_id, raw.ticker, raw.title) if _tracer else None
    detected_at = raw.detected_at

    # 1.5. Skip correction/withdrawal events (only originals proceed to decision)
    if processed.event_kind in (EventKind.CORRECTION, EventKind.WITHDRAWAL):
        event_rec = EventRecord(
            mode=mode,
            schema_version=config.schema_version,
            run_id=run_id,
            event_id=processed.event_id,
            event_id_method=processed.event_id_method,
            event_kind=processed.event_kind,
            parent_id=processed.parent_id,
            event_group_id=processed.event_group_id,
            parent_match_method=processed.parent_match_method,
            parent_match_score=processed.parent_match_score,
            parent_candidate_count=processed.parent_candidate_count,
            detected_at=detected_at,
            ticker=raw.ticker,
            corp_name=raw.corp_name,
            headline=raw.title,
            bucket=Bucket.UNKNOWN,
            skip_stage=SkipStage.BUCKET,
            skip_reason="CORRECTION_EVENT",
            market_ctx=market.snapshot,
        )
        await log.write(event_rec)
        _mark_skip(counters, stage=SkipStage.BUCKET.value, reason="CORRECTION_EVENT")
        if _tracer and _t_proc is not None:
            _tracer.process_end(_t_proc, processed.event_id, "CORRECTION")
        return

    # 2. Bucket classification
    bucket_result = classify(raw.title)

    # 3. Build event record (partial — will fill quant/ctx later)
    disclosed_at: Optional[datetime] = None
    disclosed_at_missing = True
    delay_ms: Optional[int] = None
    if raw.published:
        try:
            from dateutil.parser import parse as dt_parse
            disclosed_at = dt_parse(raw.published)
            disclosed_at_missing = False
            delay_ms = int((detected_at - disclosed_at).total_seconds() * 1000)
        except Exception:
            pass

    raw_data = ContextCardData()
    skip_stage: Optional[SkipStage] = None
    skip_reason: Optional[str] = None
    analysis_tag: Optional[str] = None
    quant_passed: Optional[bool] = None
    quant_detail = None
    ctx: Optional[ContextCard] = None
    should_track_price = False

    if bucket_result.bucket == Bucket.NEG_STRONG:
        skip_stage = SkipStage.BUCKET
        skip_reason = "NEG_BUCKET"
        analysis_tag = "SHORT_WATCH"
        should_track_price = True

    elif bucket_result.bucket == Bucket.POS_STRONG:
        # Build context card
        _t_ctx = _tracer.context_card_start(raw.ticker) if _tracer else None
        ctx_card, raw_data = await build_context_card(raw.ticker, kis, config=config)
        if _tracer and _t_ctx is not None:
            _tracer.context_card_end(_t_ctx, raw.ticker)
        ctx = ctx_card

        # Quant check
        adv = raw_data.adv_value_20d or 0
        spread = raw_data.spread_bps
        ret_today = raw_data.ret_today

        qr = quant_check(adv, spread, ret_today, config)
        quant_passed = qr.passed
        quant_detail = qr.detail

        if not qr.passed:
            skip_stage = SkipStage.QUANT
            skip_reason = qr.skip_reason
            should_track_price = qr.should_track_price
            analysis_tag = qr.analysis_tag

    else:
        skip_stage = SkipStage.BUCKET
        skip_reason = f"{bucket_result.bucket.value}_BUCKET"
        # Collect UNKNOWN headlines for daily keyword review
        if bucket_result.bucket == Bucket.UNKNOWN:
            _append_unknown_headline(config.log_dir, raw.title, raw.ticker)

    # Log event record
    event_rec = EventRecord(
        mode=mode,
        schema_version=config.schema_version,
        run_id=run_id,
        event_id=processed.event_id,
        event_id_method=processed.event_id_method,
        event_kind=processed.event_kind,
        parent_id=processed.parent_id,
        event_group_id=processed.event_group_id,
        parent_match_method=processed.parent_match_method,
        parent_match_score=processed.parent_match_score,
        parent_candidate_count=processed.parent_candidate_count,
        source=feed_source,
        rss_guid=raw.rss_guid,
        rss_link=raw.link,
        kind_uid=processed.kind_uid,
        disclosed_at=disclosed_at,
        disclosed_at_missing=disclosed_at_missing,
        detected_at=detected_at,
        delay_ms=delay_ms,
        ticker=raw.ticker,
        corp_name=raw.corp_name,
        headline=raw.title,
        bucket=bucket_result.bucket,
        keyword_hits=bucket_result.keyword_hits,
        analysis_tag=analysis_tag,
        skip_stage=skip_stage,
        skip_reason=skip_reason,
        quant_check_passed=quant_passed,
        quant_check_detail=quant_detail,
        ctx=ctx,
        market_ctx=market.snapshot,
    )

    # Schedule price tracking if needed
    if should_track_price:
        scheduler.schedule_t0(
            event_id=processed.event_id,
            ticker=raw.ticker,
            t0_basis=T0Basis.DETECTED_AT,
            t0_ts=detected_at,
            run_id=run_id,
            mode=mode,
        )

    # 4. Decision (POS_STRONG + quant pass only)
    if bucket_result.bucket != Bucket.POS_STRONG or not quant_passed:
        await log.write(event_rec)
        _mark_skip(
            counters,
            stage=event_rec.skip_stage.value if event_rec.skip_stage else None,
            reason=event_rec.skip_reason,
        )
        if _tracer and _t_proc is not None:
            _tracer.process_end(_t_proc, processed.event_id, event_rec.skip_reason or "SKIP")
        return

    # Market halt check
    if market.is_halted:
        halt_reason = "MARKET_NOT_INITIALIZED" if not market.is_initialized else "MARKET_HALTED"
        logger.info("SKIP (%s): %s", halt_reason, raw.title[:60])
        await log.write(event_rec)
        _mark_skip(counters, stage="MARKET", reason=halt_reason)
        return

    market_snapshot = market.snapshot
    if (
        market_snapshot.kospi_change_pct is not None
        and market_snapshot.kosdaq_change_pct is not None
        and market_snapshot.kospi_breadth_ratio is not None
        and market_snapshot.kosdaq_breadth_ratio is not None
        and market_snapshot.kospi_change_pct < 0
        and market_snapshot.kosdaq_change_pct < 0
        and market_snapshot.kospi_breadth_ratio < config.min_market_breadth_ratio
        and market_snapshot.kosdaq_breadth_ratio < config.min_market_breadth_ratio
    ):
        logger.info("SKIP (MARKET_BREADTH_RISK_OFF): %s", raw.title[:60])
        event_rec.skip_stage = SkipStage.GUARDRAIL
        event_rec.skip_reason = "MARKET_BREADTH_RISK_OFF"
        await log.write(event_rec)
        _mark_skip(counters, stage=SkipStage.GUARDRAIL.value, reason="MARKET_BREADTH_RISK_OFF")
        return

    # Dry run: skip LLM
    if config.dry_run:
        logger.info("DRY-RUN SKIP decision: %s", raw.title[:60])
        await log.write(event_rec)
        _mark_skip(counters, stage="DRY_RUN", reason="DRY_RUN")
        return

    detected_str = detected_at.strftime("%H:%M:%S")
    _t_llm = _tracer.llm_start(raw.ticker) if _tracer else None
    try:
        decision = await decision_engine.decide(
            ticker=raw.ticker,
            corp_name=raw.corp_name,
            headline=raw.title,
            bucket=bucket_result.bucket,
            ctx=ctx if ctx else ContextCard(),
            detected_at_str=detected_str,
            run_id=run_id,
            schema_version=config.schema_version,
        )
    except LlmTimeoutError:
        if _tracer and _t_llm is not None:
            _tracer.llm_end(_t_llm, raw.ticker, error="timeout")
        event_rec.skip_stage = SkipStage.LLM_TIMEOUT
        event_rec.skip_reason = "LLM_TIMEOUT"
        await log.write(event_rec)
        _mark_skip(counters, stage=SkipStage.LLM_TIMEOUT.value, reason="LLM_TIMEOUT")
        if counters is not None:
            counters.errors["llm_timeout"] += 1
        if _tracer and _t_proc is not None:
            _tracer.process_end(_t_proc, processed.event_id, "LLM_TIMEOUT")
        return
    except LlmCallError:
        if _tracer and _t_llm is not None:
            _tracer.llm_end(_t_llm, raw.ticker, error="call_error")
        event_rec.skip_stage = SkipStage.LLM_ERROR
        event_rec.skip_reason = "LLM_ERROR"
        await log.write(event_rec)
        _mark_skip(counters, stage=SkipStage.LLM_ERROR.value, reason="LLM_ERROR")
        if counters is not None:
            counters.errors["llm_call_error"] += 1
        if _tracer and _t_proc is not None:
            _tracer.process_end(_t_proc, processed.event_id, "LLM_ERROR")
        return
    except LlmParseError:
        if _tracer and _t_llm is not None:
            _tracer.llm_end(_t_llm, raw.ticker, error="parse_error")
        event_rec.skip_stage = SkipStage.LLM_PARSE
        event_rec.skip_reason = "LLM_PARSE"
        await log.write(event_rec)
        _mark_skip(counters, stage=SkipStage.LLM_PARSE.value, reason="LLM_PARSE")
        if counters is not None:
            counters.errors["llm_parse_error"] += 1
        if _tracer and _t_proc is not None:
            _tracer.process_end(_t_proc, processed.event_id, "LLM_PARSE")
        return

    if _tracer and _t_llm is not None:
        _tracer.llm_end(_t_llm, raw.ticker)
    decision.event_id = processed.event_id
    decision.mode = mode

    # Guardrails check (final safety net before order)
    # sector data not yet available in context_card; pass explicitly when added
    gr = check_guardrails(
        ticker=raw.ticker,
        config=config,
        spread_bps=raw_data.spread_bps if ctx else None,
        adv_value_20d=raw_data.adv_value_20d if ctx else None,
        ret_today=raw_data.ret_today if ctx else None,
        state=guardrail_state,
        headline=raw.title,
        sector=raw_data.sector if ctx else "",
        quote_risk_state=raw_data.quote_risk_state if ctx else None,
        orderbook_snapshot=raw_data.orderbook_snapshot if ctx else None,
        intraday_value_vs_adv20d=raw_data.intraday_value_vs_adv20d if ctx else None,
        decision_action=decision.action,
    )
    if not gr.passed:
        event_rec.skip_stage = SkipStage.GUARDRAIL
        event_rec.skip_reason = gr.reason
        await log.write(event_rec)
        _mark_skip(
            counters,
            stage=SkipStage.GUARDRAIL.value,
            reason=gr.reason,
        )
        return

    # Success: write event + decision (exactly once each)
    await log.write(event_rec)
    await log.write(decision)
    if counters is not None:
        counters.totals["decisions_emitted"] += 1
        counters.totals[f"decision_action_{decision.action.value}"] += 1
        counters.totals[f"decision_source_{decision.decision_source}"] += 1

    # Track BUY in guardrail state for portfolio-level controls
    if decision.action == Action.BUY and guardrail_state is not None:
        guardrail_state.record_buy(raw.ticker)

    # Schedule price snapshots with DECIDED_AT basis
    scheduler.schedule_t0(
        event_id=processed.event_id,
        ticker=raw.ticker,
        t0_basis=T0Basis.DECIDED_AT,
        t0_ts=decision.decided_at,
        run_id=run_id,
        mode=mode,
        is_buy_decision=(decision.action == Action.BUY),
    )

    # Paper mode guard: no order execution
    if mode == "paper":
        logger.info("PAPER %s [%s] conf=%d hint=%s: %s",
                     decision.action.value, raw.ticker, decision.confidence,
                     decision.size_hint.value, decision.reason)
        return

    action_str = decision.action.value
    logger.info(
        "%s [%s] conf=%d hint=%s: %s",
        action_str, raw.ticker, decision.confidence, decision.size_hint.value, decision.reason,
    )


async def _pipeline_loop(
    feed,
    registry: EventRegistry,
    decision_engine: DecisionEngine,
    market: MarketMonitor,
    scheduler: SnapshotScheduler,
    log: JsonlLogger,
    config: Config,
    run_id: str,
    kis: Optional[KisClient],
    counters: Optional[RuntimeCounters] = None,
    mode: str = "live",
    stop_event: Optional[asyncio.Event] = None,
    guardrail_state: Optional[GuardrailState] = None,
    feed_source: str = "KIND",
) -> None:
    """Main pipeline: feed/registry + queue/worker event processing."""
    worker_count = max(1, config.pipeline_workers)
    queue_maxsize = max(1, config.pipeline_queue_maxsize)
    queue: asyncio.Queue = asyncio.Queue(maxsize=queue_maxsize)

    async def _worker(worker_idx: int) -> None:
        while True:
            item = await queue.get()
            try:
                if item is None:
                    return
                raw, processed = item
                await _process_registered_event(
                    raw=raw,
                    processed=processed,
                    decision_engine=decision_engine,
                    market=market,
                    scheduler=scheduler,
                    log=log,
                    config=config,
                    run_id=run_id,
                    kis=kis,
                    counters=counters,
                    mode=mode,
                    guardrail_state=guardrail_state,
                    feed_source=feed_source,
                )
            except LogWriteError:
                logger.critical("Log write failed in worker %d — initiating shutdown", worker_idx)
                if counters is not None:
                    counters.errors["log_write_error"] += 1
                if stop_event is not None:
                    stop_event.set()
                    feed.stop()
                return
            except Exception:
                logger.exception("Pipeline worker %d failed", worker_idx)
                if counters is not None:
                    counters.errors["worker_exception"] += 1
            finally:
                queue.task_done()

    workers = [
        asyncio.create_task(_worker(idx), name=f"pipeline-worker-{idx}")
        for idx in range(worker_count)
    ]

    try:
        async for batch in feed.stream():
            if stop_event is not None and stop_event.is_set():
                logger.info("Pipeline stop_event detected, exiting feed loop")
                feed.stop()
                break
            for raw in batch:
                if counters is not None:
                    counters.totals["events_seen"] += 1
                detected_at = raw.detected_at

                # 1. Registry: dedup + correction
                processed = registry.process(raw)
                if processed is None:
                    logger.debug("DUPLICATE: %s", raw.title[:60])
                    dup_id = "dup_" + hashlib.sha256(
                        f"{raw.link}|{detected_at.isoformat()}".encode()
                    ).hexdigest()[:16]
                    dup_event = EventRecord(
                        mode=mode,
                        schema_version=config.schema_version,
                        run_id=run_id,
                        event_id=dup_id,
                        event_id_method=EventIdMethod.FALLBACK,
                        event_group_id=dup_id,
                        detected_at=detected_at,
                        ticker=raw.ticker,
                        corp_name=raw.corp_name,
                        headline=raw.title,
                        bucket=Bucket.UNKNOWN,
                        skip_stage=SkipStage.DUPLICATE,
                        skip_reason="DUPLICATE",
                        market_ctx=market.snapshot,
                    )
                    await log.write(dup_event)
                    _mark_skip(counters, stage=SkipStage.DUPLICATE.value, reason="DUPLICATE")
                    continue

                if not raw.ticker:
                    logger.debug("SKIP (empty ticker): %s", raw.title[:60])
                    _mark_skip(counters, stage="FEED", reason="EMPTY_TICKER")
                    continue

                _tracer = get_tracer()
                _t_q = _tracer.queue_put(queue.qsize(), queue.maxsize) if _tracer else None
                await queue.put((raw, processed))
                if _tracer and _t_q is not None:
                    _tracer.queue_put_done(_t_q)
                if counters is not None:
                    counters.totals["events_enqueued"] += 1

        # Feed stopped naturally. Drain queue before shutdown.
        await queue.join()
        for _ in workers:
            await queue.put(None)
        await asyncio.gather(*workers, return_exceptions=True)
    except asyncio.CancelledError:
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        raise


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
        else:
            logger.warning("KIS client disabled — market monitor will block trading (fail-close), price snapshots UNAVAILABLE")

        state_dir = config.log_dir / "state" / mode
        feed_source = config.feed_source.upper()
        if feed_source == "KIS" and kis:
            feed = KisFeed(config, kis, state_dir=state_dir / "feed")
            logger.info("Feed source: KIS API")
        else:
            if feed_source == "KIS" and not kis:
                logger.warning("KIS feed requested but KIS client disabled — falling back to KIND RSS")
            feed = KindFeed(config, session)
            feed_source = "KIND"
            logger.info("Feed source: KIND RSS")
        registry = EventRegistry(state_dir=state_dir)
        decision_engine = DecisionEngine(config)
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

        def _on_close_pnl(ticker: str, pnl_won: float) -> None:
            guardrail_state.record_pnl(pnl_won)
            logger.info("P&L recorded: %s %.0f won (daily total: %.0f)", ticker, pnl_won, guardrail_state.daily_pnl)

        scheduler = SnapshotScheduler(
            config, fetcher, log,
            stop_event=stop_event,
            pnl_callback=_on_close_pnl,
        )

        # Market monitor task (update every 60s)
        async def _market_loop() -> None:
            while not stop_event.is_set():
                try:
                    guardrail_state.check_daily_reset()
                    await market.update()
                except Exception:
                    logger.exception("Market monitor error")
                await _wait_or_stop(stop_event, 60)

        notify_ready()

        tasks = [
            asyncio.create_task(_pipeline_loop(
                feed, registry, decision_engine, market, scheduler, log, config, run_id, kis, counters, mode,
                stop_event=stop_event,
                guardrail_state=guardrail_state,
                feed_source=feed_source,
            ), name="pipeline"),
            asyncio.create_task(scheduler.run(), name="snapshots"),
            asyncio.create_task(_market_loop(), name="market"),
            asyncio.create_task(_watchdog_loop(feed, counters, config, stop_event), name="watchdog"),
        ]

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            # Idempotent shutdown to cover both signaled and non-signaled exits.
            stop_event.set()
            feed.stop()
            scheduler.stop()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if kis is not None:
                logger.info("KIS client stats: %s", kis.stats_snapshot())
            logger.info("Runtime counters: %s", _counter_snapshot(counters))
            logger.info("Shutdown complete. Pending snapshots lost: %d", scheduler.pending_count)
