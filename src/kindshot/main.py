"""Main entry point: asyncio supervisor orchestrating all components."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from kindshot.bucket import classify
from kindshot.config import Config, load_config
from kindshot.context_card import build_context_card
from kindshot.decision import DecisionEngine, LlmTimeoutError, LlmParseError
from kindshot.event_registry import EventRegistry
from kindshot.feed import KindFeed, _extract_kind_uid
from kindshot.guardrails import check_guardrails
from kindshot.kis_client import KisClient
from kindshot.logger import JsonlLogger
from kindshot.market import MarketMonitor
from kindshot.models import (
    Bucket,
    ContextCard,
    EventIdMethod,
    EventRecord,
    SkipStage,
    T0Basis,
)
from kindshot.price import PriceFetcher, SnapshotScheduler
from kindshot.quant import quant_check

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-20s %(levelname)-5s %(message)s",
)
logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="kindshot MVP")
    p.add_argument("--dry-run", action="store_true", help="Skip LLM calls, log events only")
    return p.parse_args()


async def _pipeline_loop(
    feed: KindFeed,
    registry: EventRegistry,
    decision_engine: DecisionEngine,
    market: MarketMonitor,
    scheduler: SnapshotScheduler,
    log: JsonlLogger,
    config: Config,
    run_id: str,
    kis: Optional[KisClient],
) -> None:
    """Main pipeline: feed → registry → bucket → quant → decision → log."""

    async for batch in feed.stream():
        for raw in batch:
            detected_at = raw.detected_at

            # 1. Registry: dedup + correction
            processed = registry.process(raw)
            if processed is None:
                # Duplicate — log skip event
                logger.debug("DUPLICATE: %s", raw.title[:60])
                import hashlib
                dup_id = "dup_" + hashlib.sha256(
                    f"{raw.link}|{detected_at.isoformat()}".encode()
                ).hexdigest()[:16]
                dup_event = EventRecord(
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
                )
                await log.write(dup_event)
                continue

            # 2. Bucket classification
            bucket_result = classify(raw.title)

            # 3. Build event record (partial — will fill quant/ctx later)
            # Parse disclosed_at
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

            # Skip non-actionable buckets early
            raw_data: dict = {}
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
                ctx_card, raw_data = await build_context_card(raw.ticker, kis)
                ctx = ctx_card

                # Quant check
                adv = raw_data.get("adv_value_20d") or 0
                spread = raw_data.get("spread_bps")
                ret_today = raw_data.get("ret_today") or 0

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

            # Log event record
            event_rec = EventRecord(
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
                source="KIND",
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
            )

            # Schedule price tracking if needed
            if should_track_price:
                scheduler.schedule_t0(
                    event_id=processed.event_id,
                    ticker=raw.ticker,
                    t0_basis=T0Basis.DETECTED_AT,
                    t0_ts=detected_at,
                    run_id=run_id,
                )

            # 4. Decision (POS_STRONG + quant pass only)
            if bucket_result.bucket != Bucket.POS_STRONG or not quant_passed:
                await log.write(event_rec)
                continue

            # Market halt check
            if market.is_halted:
                logger.info("SKIP (market halted): %s", raw.title[:60])
                await log.write(event_rec)
                continue

            # Dry run: skip LLM
            if config.dry_run:
                logger.info("DRY-RUN SKIP decision: %s", raw.title[:60])
                await log.write(event_rec)
                continue

            detected_str = detected_at.strftime("%H:%M:%S")
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
                event_rec.skip_stage = SkipStage.LLM_TIMEOUT
                event_rec.skip_reason = "LLM_TIMEOUT"
                await log.write(event_rec)
                continue
            except LlmParseError:
                event_rec.skip_stage = SkipStage.LLM_PARSE
                event_rec.skip_reason = "LLM_PARSE"
                await log.write(event_rec)
                continue

            decision.event_id = processed.event_id

            # Guardrails check (MVP: always passes, real checks in v0.4)
            gr = check_guardrails(
                ticker=raw.ticker,
                spread_bps=raw_data.get("spread_bps") if ctx else None,
                adv_value_20d=raw_data.get("adv_value_20d") if ctx else None,
                ret_today=raw_data.get("ret_today") if ctx else None,
            )
            if not gr.passed:
                event_rec.skip_stage = SkipStage.GUARDRAIL
                event_rec.skip_reason = gr.reason
                await log.write(event_rec)
                continue

            # Success: write event + decision (exactly once each)
            await log.write(event_rec)
            await log.write(decision)

            # Schedule price snapshots with DECIDED_AT basis
            scheduler.schedule_t0(
                event_id=processed.event_id,
                ticker=raw.ticker,
                t0_basis=T0Basis.DECIDED_AT,
                t0_ts=decision.decided_at,
                run_id=run_id,
            )

            action_str = decision.action.value
            logger.info(
                "%s [%s] conf=%d hint=%s: %s",
                action_str, raw.ticker, decision.confidence, decision.size_hint.value, decision.reason,
            )


async def run() -> None:
    args = _parse_args()
    config = load_config(dry_run=args.dry_run)
    run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    logger.info("kindshot %s starting (run_id=%s, dry_run=%s)", config.schema_version, run_id, config.dry_run)

    log = JsonlLogger(config.log_dir, run_id=run_id)

    async with aiohttp.ClientSession() as session:
        # KIS client (optional)
        kis: Optional[KisClient] = None
        if config.kis_enabled:
            kis = KisClient(config, session)
            logger.info("KIS client enabled")
        else:
            logger.warning("KIS client disabled — price snapshots will be UNAVAILABLE")

        feed = KindFeed(config, session)
        registry = EventRegistry()
        decision_engine = DecisionEngine(config)
        market = MarketMonitor(config, kis)
        fetcher = PriceFetcher(kis=kis)
        scheduler = SnapshotScheduler(config, fetcher, log)

        # Graceful shutdown
        stop_event = asyncio.Event()

        def _signal_handler() -> None:
            logger.info("Shutdown signal received, pending snapshots: %d", scheduler.pending_count)
            stop_event.set()
            scheduler.stop()

        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

        # Market monitor task (update every 60s)
        async def _market_loop() -> None:
            while not stop_event.is_set():
                try:
                    await market.update()
                except Exception:
                    logger.exception("Market monitor error")
                await asyncio.sleep(60)

        tasks = [
            asyncio.create_task(_pipeline_loop(
                feed, registry, decision_engine, market, scheduler, log, config, run_id, kis,
            ), name="pipeline"),
            asyncio.create_task(scheduler.run(), name="snapshots"),
            asyncio.create_task(_market_loop(), name="market"),
        ]

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            for t in tasks:
                t.cancel()
            logger.info("Shutdown complete. Pending snapshots lost: %d", scheduler.pending_count)
