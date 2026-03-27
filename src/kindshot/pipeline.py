"""Event processing pipeline: bucket → quant → LLM → guardrails → price tracking."""

from __future__ import annotations

import asyncio
from collections import Counter
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from kindshot.alpha_scanner import (
    classify_sector_priority,
    fetch_alpha_scanner_sector_snapshot,
    lookup_sector_snapshot_ticker,
)
from kindshot.bucket import classify
from kindshot.config import Config
from kindshot.context_card import (
    ContextCardData,
    append_runtime_context_card,
    build_context_card,
)
from kindshot.decision import DecisionEngine, LlmCallError, LlmTimeoutError, LlmParseError, has_high_conviction_keyword, has_article_pattern
from kindshot.entry_filter_analysis import compute_effective_entry_delay_ms
from kindshot.event_registry import EventRegistry, ProcessedEvent
from kindshot.feed import RawDisclosure
from kindshot.headline_parser import normalize_analysis_headline
from kindshot.guardrails import GuardrailState, check_guardrails, get_kill_switch_size_hint, apply_adv_confidence_adjustment, apply_market_confidence_adjustment, apply_delay_confidence_adjustment, apply_price_reaction_adjustment, apply_volume_confidence_adjustment, apply_volume_ratio_confidence_adjustment, apply_sector_momentum_confidence_adjustment, apply_dorg_confidence_adjustment, apply_time_session_confidence_adjustment, apply_trend_confidence_adjustment, apply_technical_confidence_adjustment, apply_headline_quality_adjustment, resolve_dynamic_guardrail_profile, detect_volatility_regime, apply_volatility_confidence_adjustment, apply_news_category_confidence_adjustment, apply_mtf_confidence_adjustment, resolve_daily_loss_budget
from kindshot.news_category import classify_news_type
from kindshot.pattern_profile import match_loss_guardrail, match_profit_boost
from kindshot.ticker_learning import TickerLearner
from kindshot.hold_profile import get_max_hold_minutes
from kindshot.kis_client import KisClient
from kindshot.logger import JsonlLogger, LogWriteError
from kindshot.market import MarketMonitor
from kindshot.models import (
    Action,
    Bucket,
    ContextCard,
    DecisionRecord,
    EventIdMethod,
    EventKind,
    EventRecord,
    MarketContext,
    NewsSignalContext,
    PipelineLatencyProfile,
    PromotionStatus,
    SkipStage,
    T0Basis,
)
from kindshot.news_semantics import (
    TickerNewsClusterTracker,
    apply_impact_score_confidence_adjustment,
    build_news_signal,
)
from kindshot.price import PriceFetcher, SnapshotScheduler
from kindshot.quant import quant_check
from kindshot.poll_trace import get_tracer
from kindshot.runtime_latency import identify_bottleneck_stage
from kindshot.tz import KST as _KST
from kindshot.unknown_review import (
    UnknownReviewEngine,
    UnknownReviewRequest,
    append_unknown_inbox,
    append_unknown_promotion,
    append_unknown_review,
    evaluate_unknown_promotion,
)

logger = logging.getLogger(__name__)

_ticker_learner: Optional[TickerLearner] = None


def _get_ticker_learner(config: Config) -> Optional[TickerLearner]:
    global _ticker_learner
    if not config.ticker_learning_enabled:
        return None
    if _ticker_learner is None:
        _ticker_learner = TickerLearner(min_trades=config.ticker_learning_min_trades)
        _ticker_learner.load_history(config.data_dir)
    return _ticker_learner


def _queue_priority_key(sector_row: dict | None, sequence: int) -> tuple[tuple[int, float, float], int]:
    return classify_sector_priority(sector_row), sequence



# ── Data classes ──────────────────────────────────────

@dataclass
class RuntimeCounters:
    totals: Counter[str] = field(default_factory=Counter)
    skip_stage: Counter[str] = field(default_factory=Counter)
    skip_reason: Counter[str] = field(default_factory=Counter)
    errors: Counter[str] = field(default_factory=Counter)


@dataclass(frozen=True)
class ProcessOutcome:
    event_id: str
    action: Optional[Action] = None
    skip_stage: Optional[SkipStage] = None
    skip_reason: Optional[str] = None
    decision: Optional[DecisionRecord] = None
    pipeline_profile: Optional[PipelineLatencyProfile] = None


# ── Helpers ───────────────────────────────────────────

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


def counter_snapshot(counters: RuntimeCounters) -> dict[str, dict[str, int]]:
    return {
        "totals": dict(counters.totals),
        "skip_stage": dict(counters.skip_stage),
        "skip_reason": dict(counters.skip_reason),
        "errors": dict(counters.errors),
    }


def _elapsed_since_detected_at_ms(detected_at: datetime) -> int:
    now = datetime.now(detected_at.tzinfo or _KST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_KST)
    return max(0, int((now - detected_at).total_seconds() * 1000))


def _build_pipeline_profile(
    *,
    detected_at: datetime,
    context_card_ms: int | None = None,
    decision_total_ms: int | None = None,
    guardrail_ms: int | None = None,
    order_attempt_ms: int | None = None,
    pipeline_total_ms: int | None = None,
    llm_latency_ms: int | None = None,
    llm_cache_layer: str | None = None,
) -> PipelineLatencyProfile:
    profile = PipelineLatencyProfile(
        news_to_pipeline_ms=_elapsed_since_detected_at_ms(detected_at),
        context_card_ms=context_card_ms,
        decision_total_ms=decision_total_ms,
        guardrail_ms=guardrail_ms,
        order_attempt_ms=order_attempt_ms,
        pipeline_total_ms=pipeline_total_ms,
        llm_latency_ms=llm_latency_ms,
        llm_cache_layer=llm_cache_layer,
    )
    profile.bottleneck_stage = identify_bottleneck_stage(profile)
    return profile


def _attach_decision_summary(event_rec: EventRecord, decision: DecisionRecord) -> None:
    event_rec.decision_action = decision.action.value
    event_rec.decision_confidence = decision.confidence
    event_rec.decision_size_hint = decision.size_hint.value
    event_rec.decision_reason = decision.reason
    event_rec.decision_source = decision.decision_source
    event_rec.decision_llm_latency_ms = decision.llm_latency_ms
    event_rec.decision_cache_layer = decision.cache_layer


def _hour_bucket_from_datetime(dt: datetime) -> str:
    dt_kst = dt.astimezone(_KST) if dt.tzinfo else dt.replace(tzinfo=_KST)
    hour = dt_kst.hour
    if hour < 9:
        return "pre_open"
    if hour == 9:
        return "open"
    if hour == 10:
        return "mid_morning"
    if 11 <= hour <= 13:
        return "midday"
    if hour == 14:
        return "afternoon"
    return "late"


def _is_fast_profile_late_entry(
    *,
    config: Config,
    hold_minutes: int,
    detected_at: datetime,
    guardrail_profile,
) -> bool:
    if hold_minutes != config.fast_profile_hold_minutes:
        return False
    if guardrail_profile.fast_profile_no_buy_after_kst_hour >= 24:
        return False
    detected_at_kst = detected_at.astimezone(_KST) if detected_at.tzinfo else detected_at.replace(tzinfo=_KST)
    cutoff = detected_at_kst.replace(
        hour=guardrail_profile.fast_profile_no_buy_after_kst_hour,
        minute=guardrail_profile.fast_profile_no_buy_after_kst_minute,
        second=0,
        microsecond=0,
    )
    return detected_at_kst >= cutoff


def _parse_disclosure_meta(raw: RawDisclosure, detected_at: datetime) -> tuple[Optional[datetime], bool, Optional[int]]:
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
            logger.debug("Failed to parse disclosed_at from published=%s", raw.published)
    return disclosed_at, disclosed_at_missing, delay_ms


def _promoted_event_id(original_event_id: str, bucket: Bucket) -> str:
    digest = hashlib.sha256(f"{original_event_id}|{bucket.value}|UNKNOWN_PROMOTION".encode()).hexdigest()[:16]
    return f"up_{digest}"


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


# ── Core pipeline functions ───────────────────────────

async def execute_bucket_path(
    *,
    raw: RawDisclosure,
    processed: ProcessedEvent,
    bucket: Bucket,
    keyword_hits: list[str],
    decision_engine: DecisionEngine,
    market: MarketMonitor,
    scheduler: SnapshotScheduler,
    log: JsonlLogger,
    config: Config,
    run_id: str,
    kis: Optional[KisClient],
    counters: Optional[RuntimeCounters],
    mode: str,
    guardrail_state: Optional[GuardrailState],
    feed_source: str,
    health_state: Optional[object] = None,
    order_executor: Optional[object] = None,
    recent_pattern_profile: Optional[object] = None,
    analysis_tag_override: Optional[str] = None,
    promotion_original_event_id: Optional[str] = None,
    promotion_original_bucket: Optional[Bucket] = None,
    promotion_confidence: Optional[int] = None,
    promotion_policy: Optional[str] = None,
    news_signal: Optional[NewsSignalContext] = None,
) -> ProcessOutcome:
    detected_at = raw.detected_at
    analysis_headline = news_signal.analysis_headline if news_signal is not None else normalize_analysis_headline(raw.title)
    news_cat = news_signal.news_category if news_signal is not None else classify_news_type(analysis_headline, keyword_hits)
    disclosed_at, disclosed_at_missing, delay_ms = _parse_disclosure_meta(raw, detected_at)
    _pipeline_t0 = time.monotonic()

    raw_data = ContextCardData()
    skip_stage: Optional[SkipStage] = None
    skip_reason: Optional[str] = None
    analysis_tag: Optional[str] = analysis_tag_override
    quant_passed: Optional[bool] = None
    quant_detail = None
    ctx: Optional[ContextCard] = None
    should_track_price = False
    context_card_ms: int | None = None
    decision_total_ms: int | None = None
    guardrail_ms: int | None = None
    order_attempt_ms: int | None = None

    if bucket == Bucket.NEG_STRONG:
        if config.news_exit_enabled and scheduler.has_open_position(raw.ticker):
            closed = await scheduler.force_exit_ticker(
                raw.ticker,
                exit_type="news_exit",
                horizon="news",
            )
            if closed:
                logger.info(
                    "NEG_STRONG news liquidation [%s]: closed=%d headline=%s",
                    raw.ticker,
                    closed,
                    analysis_headline[:80],
                )
        skip_stage = SkipStage.BUCKET
        skip_reason = "NEG_BUCKET"
        analysis_tag = analysis_tag or "SHORT_WATCH"
        should_track_price = True
    elif bucket in (Bucket.POS_STRONG, Bucket.POS_WEAK):
        _ctx_t0 = time.monotonic()
        ctx_card, raw_data = await build_context_card(raw.ticker, kis, config=config)
        ctx = ctx_card
        context_card_ms = int((time.monotonic() - _ctx_t0) * 1000)
        if context_card_ms > 500:
            logger.warning("Slow context_card build [%s]: %dms", raw.ticker, context_card_ms)
        else:
            logger.debug("context_card build [%s]: %dms", raw.ticker, context_card_ms)
        effective_adv_threshold = config.adv_threshold_for_bucket(bucket.value)

        qr = quant_check(
            raw_data.adv_value_20d or 0,
            raw_data.spread_bps,
            raw_data.ret_today,
            config,
            adv_threshold=effective_adv_threshold,
            observed_at=detected_at,
        )
        quant_passed = qr.passed
        quant_detail = qr.detail

        if not qr.passed:
            skip_stage = SkipStage.QUANT
            skip_reason = qr.skip_reason
            should_track_price = True  # 반사실 데이터: quant 실패해도 가격 추적
            analysis_tag = analysis_tag or qr.analysis_tag
    else:
        skip_stage = SkipStage.BUCKET
        skip_reason = f"{bucket.value}_BUCKET"

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
        dorg=raw.dorg,
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
        analysis_headline=analysis_headline,
        bucket=bucket,
        keyword_hits=keyword_hits,
        news_category=news_cat,
        news_signal=news_signal,
        analysis_tag=analysis_tag,
        skip_stage=skip_stage,
        skip_reason=skip_reason,
        quant_check_passed=quant_passed,
        quant_check_detail=quant_detail,
        ctx=ctx,
        market_ctx=market.snapshot,
        promotion_original_event_id=promotion_original_event_id,
        promotion_original_bucket=promotion_original_bucket,
        promotion_confidence=promotion_confidence,
        promotion_policy=promotion_policy,
    )

    def _finalize_profile(decision: DecisionRecord | None = None) -> PipelineLatencyProfile:
        pipeline_total_ms = int((time.monotonic() - _pipeline_t0) * 1000)
        profile = _build_pipeline_profile(
            detected_at=detected_at,
            context_card_ms=context_card_ms,
            decision_total_ms=decision_total_ms,
            guardrail_ms=guardrail_ms,
            order_attempt_ms=order_attempt_ms,
            pipeline_total_ms=pipeline_total_ms,
            llm_latency_ms=decision.llm_latency_ms if decision is not None else None,
            llm_cache_layer=decision.cache_layer if decision is not None else None,
        )
        event_rec.pipeline_profile = profile
        if decision is not None:
            _attach_decision_summary(event_rec, decision)
        return profile

    if ctx is not None:
        await append_runtime_context_card(
            config,
            run_id=run_id,
            mode=mode,
            event_id=processed.event_id,
            event_kind=processed.event_kind.value,
            ticker=raw.ticker,
            corp_name=raw.corp_name,
            headline=raw.title,
            bucket=bucket.value,
            detected_at=detected_at,
            disclosed_at=disclosed_at,
            delay_ms=delay_ms,
            quant_check_passed=quant_passed,
            skip_stage=event_rec.skip_stage.value if event_rec.skip_stage else None,
            skip_reason=event_rec.skip_reason,
            promotion_original_event_id=promotion_original_event_id,
            promotion_original_bucket=promotion_original_bucket.value if promotion_original_bucket else None,
            promotion_confidence=promotion_confidence,
            promotion_policy=promotion_policy,
            news_signal=news_signal,
            ctx=ctx,
            raw=raw_data,
            market_ctx=event_rec.market_ctx,
            keyword_hits=keyword_hits,
        )

    if should_track_price:
        scheduler.schedule_t0(
            event_id=processed.event_id,
            ticker=raw.ticker,
            t0_basis=T0Basis.DETECTED_AT,
            t0_ts=detected_at,
            run_id=run_id,
            mode=mode,
        )

    if bucket not in (Bucket.POS_STRONG, Bucket.POS_WEAK) or not quant_passed:
        profile = _finalize_profile()
        await log.write(event_rec)
        _mark_skip(
            counters,
            stage=event_rec.skip_stage.value if event_rec.skip_stage else None,
            reason=event_rec.skip_reason,
        )
        return ProcessOutcome(
            event_id=processed.event_id,
            skip_stage=event_rec.skip_stage,
            skip_reason=event_rec.skip_reason,
            pipeline_profile=profile,
        )

    if market.is_halted:
        halt_reason = "MARKET_NOT_INITIALIZED" if not market.is_initialized else "MARKET_HALTED"
        event_rec.skip_stage = SkipStage.GUARDRAIL
        event_rec.skip_reason = halt_reason
        profile = _finalize_profile()
        await log.write(event_rec)
        _mark_skip(counters, stage=SkipStage.GUARDRAIL.value, reason=halt_reason)
        return ProcessOutcome(
            event_id=processed.event_id,
            skip_stage=SkipStage.GUARDRAIL,
            skip_reason=halt_reason,
            pipeline_profile=profile,
        )

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
        # 고확신 촉매(conf>=82 키워드)는 하락장에서도 LLM 판단 허용
        if has_high_conviction_keyword(analysis_headline, keyword_hits, min_conf=86):
            logger.info(
                "MARKET_BREADTH_RISK_OFF bypassed for high-conviction catalyst [%s]: %s",
                raw.ticker, analysis_headline[:80],
            )
        else:
            event_rec.skip_stage = SkipStage.GUARDRAIL
            event_rec.skip_reason = "MARKET_BREADTH_RISK_OFF"
            profile = _finalize_profile()
            await log.write(event_rec)
            _mark_skip(counters, stage=SkipStage.GUARDRAIL.value, reason="MARKET_BREADTH_RISK_OFF")
            return ProcessOutcome(
                event_id=processed.event_id,
                skip_stage=SkipStage.GUARDRAIL,
                skip_reason="MARKET_BREADTH_RISK_OFF",
                pipeline_profile=profile,
            )

    if config.dry_run:
        event_rec.skip_stage = SkipStage.GUARDRAIL
        event_rec.skip_reason = "DRY_RUN"
        profile = _finalize_profile()
        await log.write(event_rec)
        _mark_skip(counters, stage="DRY_RUN", reason="DRY_RUN")
        return ProcessOutcome(
            event_id=processed.event_id,
            skip_stage=SkipStage.GUARDRAIL,
            skip_reason="DRY_RUN",
            pipeline_profile=profile,
        )

    decision_hold_minutes = get_max_hold_minutes(analysis_headline, keyword_hits, config)
    guardrail_profile = resolve_dynamic_guardrail_profile(
        config,
        kospi_change_pct=market.snapshot.kospi_change_pct,
        kosdaq_change_pct=market.snapshot.kosdaq_change_pct,
        kospi_breadth_ratio=market.snapshot.kospi_breadth_ratio,
        kosdaq_breadth_ratio=market.snapshot.kosdaq_breadth_ratio,
    )

    # Pre-LLM guardrail checks (LLM 비용 절감)
    if guardrail_state is not None:
        if raw.ticker in guardrail_state.bought_tickers:
            event_rec.skip_stage = SkipStage.GUARDRAIL
            event_rec.skip_reason = "SAME_STOCK_REBUY"
            profile = _finalize_profile()
            await log.write(event_rec)
            _mark_skip(counters, stage=SkipStage.GUARDRAIL.value, reason="SAME_STOCK_REBUY")
            return ProcessOutcome(
                event_id=processed.event_id,
                skip_stage=SkipStage.GUARDRAIL,
                skip_reason="SAME_STOCK_REBUY",
                pipeline_profile=profile,
            )
        if guardrail_state.position_count >= config.max_positions:
            event_rec.skip_stage = SkipStage.GUARDRAIL
            event_rec.skip_reason = "MAX_POSITIONS"
            profile = _finalize_profile()
            await log.write(event_rec)
            _mark_skip(counters, stage=SkipStage.GUARDRAIL.value, reason="MAX_POSITIONS")
            return ProcessOutcome(
                event_id=processed.event_id,
                skip_stage=SkipStage.GUARDRAIL,
                skip_reason="MAX_POSITIONS",
                pipeline_profile=profile,
            )
    if _is_fast_profile_late_entry(
        config=config,
        hold_minutes=decision_hold_minutes,
        detected_at=detected_at,
        guardrail_profile=guardrail_profile,
    ):
        event_rec.skip_stage = SkipStage.GUARDRAIL
        event_rec.skip_reason = "FAST_PROFILE_LATE_ENTRY"
        profile = _finalize_profile()
        await log.write(event_rec)
        _mark_skip(counters, stage=SkipStage.GUARDRAIL.value, reason="FAST_PROFILE_LATE_ENTRY")
        return ProcessOutcome(
            event_id=processed.event_id,
            skip_stage=SkipStage.GUARDRAIL,
            skip_reason="FAST_PROFILE_LATE_ENTRY",
            pipeline_profile=profile,
        )

    _decision_t0 = time.monotonic()
    try:
        risk_budget = resolve_daily_loss_budget(config, guardrail_state) if guardrail_state is not None else None
        decision = await decision_engine.decide(
            ticker=raw.ticker,
            corp_name=raw.corp_name,
            headline=raw.title,
            bucket=bucket,
            ctx=ctx if ctx else ContextCard(),
            detected_at_str=detected_at.strftime("%H:%M:%S"),
            keyword_hits=keyword_hits,
            analysis_headline=analysis_headline,
            dorg=raw.dorg,
            run_id=run_id,
            schema_version=config.schema_version,
            market_ctx=market.snapshot,
            risk_budget=risk_budget,
            consecutive_stop_losses=guardrail_state.consecutive_stop_losses if guardrail_state is not None else 0,
            news_signal=news_signal,
        )
    except (LlmCallError, LlmTimeoutError, LlmParseError) as exc:
        logger.warning("LLM failed for [%s], using rule fallback: %s", raw.ticker, exc)
        if health_state is not None and hasattr(health_state, "record_llm_fallback"):
            health_state.record_llm_fallback()
        decision = decision_engine.fallback_decide(
            ticker=raw.ticker,
            headline=raw.title,
            bucket=bucket,
            ctx=ctx if ctx else ContextCard(),
            keyword_hits=keyword_hits,
            analysis_headline=analysis_headline,
            dorg=raw.dorg,
            run_id=run_id,
            schema_version=config.schema_version,
        )
    decision_total_ms = int((time.monotonic() - _decision_t0) * 1000)
    decision.event_id = processed.event_id
    decision.mode = mode

    # LLM-rule_fallback 하이브리드: LLM이 BUY를 SKIP하거나 낮은 conf를 줬지만
    # HIGH_CONVICTION 키워드 매칭 시, rule_fallback이 더 높은 conf를 주면 오버라이드
    if (
        decision.decision_source == "LLM"
        and decision.confidence < config.min_buy_confidence
        and bucket in (Bucket.POS_STRONG, Bucket.POS_WEAK)
    ):
        fallback = decision_engine.fallback_decide(
            ticker=raw.ticker,
            headline=raw.title,
            bucket=bucket,
            ctx=ctx if ctx else ContextCard(),
            keyword_hits=keyword_hits,
            analysis_headline=analysis_headline,
            dorg=raw.dorg,
            run_id=run_id,
            schema_version=config.schema_version,
        )
        if fallback.action == Action.BUY and fallback.confidence >= config.min_buy_confidence:
            logger.info(
                "LLM-fallback hybrid [%s]: LLM conf=%d → fallback conf=%d (reason=%s)",
                raw.ticker, decision.confidence, fallback.confidence, fallback.reason,
            )
            decision = fallback
            decision.event_id = processed.event_id
            decision.mode = mode
            decision.decision_source = "LLM_FALLBACK_HYBRID"

    # ── Confidence 조정 파이프라인 ──
    # 총 감점 상한: LLM 원본에서 -10 이상 감점 방지 (과다 감점 = 진짜 촉매 놓침)
    # llm_original_conf를 모든 감점 전에 캡처해야 article(-10) + pipeline(-10) = -20 방지
    effective_entry_delay_ms = None

    if decision.action == Action.BUY:
        llm_original_conf = decision.confidence
        # v66: graduated cap 보호 강화 — 유효 촉매 과다 차단 방지
        # 88+: cap 6 → 최악 82 (BUY 유지 확실)
        # 83-87: cap 8 → 최악 75-79 (BUY 유지 가능)
        # <83: cap 12 → 최악 ~71 (약한 시그널은 감점 허용)
        if llm_original_conf >= 88:
            _MAX_TOTAL_PENALTY = 6
        elif llm_original_conf >= 83:
            _MAX_TOTAL_PENALTY = 8
        else:
            _MAX_TOTAL_PENALTY = 12

        # 0. Post-LLM 기사/미확정 패턴 감점: LLM이 기사 헤드라인에 BUY를 줄 때 conf -10
        if decision.decision_source == "LLM" and has_article_pattern(
            analysis_headline,
            raw_headline=raw.title,
            dorg=raw.dorg,
        ):
            before = decision.confidence
            decision.confidence = max(0, decision.confidence - 10)
            logger.warning(
                "Post-LLM article filter [%s]: %d → %d (headline has article pattern)",
                raw.ticker, before, decision.confidence,
            )

        # 0a-2. 헤드라인 품질 감점: 짧은 제목, 추측성, 금액 미기재
        before = decision.confidence
        decision.confidence = apply_headline_quality_adjustment(decision.confidence, analysis_headline)
        if decision.confidence != before:
            logger.info("Headline quality adj [%s]: %d → %d (title=%s)",
                        raw.ticker, before, decision.confidence, analysis_headline[:50])

        # 0b. 시간대별 조정: 장전 공시 +5, 비유동 시간대 -3
        before = decision.confidence
        decision.confidence = apply_time_session_confidence_adjustment(decision.confidence, detected_at)
        if decision.confidence != before:
            logger.info("Time session adj [%s]: %d → %d (detected=%s)",
                        raw.ticker, before, decision.confidence,
                        detected_at.strftime("%H:%M"))

        # 0c. dorg 기반 감점: 뉴스 출처(거래소/금감원 아닌)면 -5
        if raw.dorg:
            before = decision.confidence
            decision.confidence = apply_dorg_confidence_adjustment(decision.confidence, raw.dorg)
            if decision.confidence != before:
                logger.info("Dorg confidence adj [%s]: %d → %d (dorg=%s)",
                            raw.ticker, before, decision.confidence, raw.dorg)

        # 1. ADV 기반 (소형주 집중 전략)
        if raw_data.adv_value_20d is not None:
            before = decision.confidence
            decision.confidence = apply_adv_confidence_adjustment(decision.confidence, raw_data.adv_value_20d)
            if decision.confidence != before:
                logger.info("ADV confidence adj [%s]: %d → %d (adv=%.0f억)",
                            raw.ticker, before, decision.confidence, raw_data.adv_value_20d / 1e8)

        # 1b. 추세 감점 (ret_3d 과열 + pos_20d 극저점)
        if ctx and (ctx.ret_3d is not None or ctx.pos_20d is not None):
            before = decision.confidence
            decision.confidence = apply_trend_confidence_adjustment(
                decision.confidence, ctx.ret_3d, ctx.pos_20d,
            )
            if decision.confidence != before:
                logger.info("Trend confidence adj [%s]: %d → %d (ret_3d=%s, pos_20d=%s)",
                            raw.ticker, before, decision.confidence,
                            f"{ctx.ret_3d:+.1f}%" if ctx.ret_3d is not None else "N/A",
                            f"{ctx.pos_20d:.0f}" if ctx.pos_20d is not None else "N/A")

        # 1c. 기술지표 감점 (RSI/MACD/BB/ATR — 프롬프트 technical_indicators 구현)
        if ctx and (ctx.rsi_14 is not None or ctx.macd_hist is not None
                    or ctx.bb_position is not None or ctx.atr_14 is not None):
            before = decision.confidence
            has_catalyst = has_high_conviction_keyword(analysis_headline, keyword_hits, min_conf=83)
            decision.confidence = apply_technical_confidence_adjustment(
                decision.confidence, ctx.rsi_14, ctx.macd_hist,
                has_catalyst=has_catalyst,
                bb_position=ctx.bb_position,
                atr_14=ctx.atr_14,
            )
            if decision.confidence != before:
                logger.info("Technical indicator adj [%s]: %d → %d (rsi=%s, macd=%s, bb=%s, atr=%s)",
                            raw.ticker, before, decision.confidence,
                            f"{ctx.rsi_14:.1f}" if ctx.rsi_14 is not None else "N/A",
                            f"{ctx.macd_hist:.2f}" if ctx.macd_hist is not None else "N/A",
                            f"{ctx.bb_position:.1f}" if ctx.bb_position is not None else "N/A",
                            f"{ctx.atr_14:.2f}%" if ctx.atr_14 is not None else "N/A")

        # 2. 시장 반응 확인 (ret_today)
        if raw_data.ret_today is not None:
            before = decision.confidence
            decision.confidence = apply_price_reaction_adjustment(decision.confidence, raw_data.ret_today)
            if decision.confidence != before:
                logger.info("Price reaction adj [%s]: %d → %d (ret_today=%+.1f%%)",
                            raw.ticker, before, decision.confidence, raw_data.ret_today)

        # 3. 거래량 확인 (전일대비)
        if raw_data.prior_volume_rate is not None:
            before = decision.confidence
            decision.confidence = apply_volume_confidence_adjustment(decision.confidence, raw_data.prior_volume_rate)
            if decision.confidence != before:
                logger.info("Volume confidence adj [%s]: %d → %d (vol_rate=%.0f%%)",
                            raw.ticker, before, decision.confidence, raw_data.prior_volume_rate)

        # 3b. 거래량 비율 확인 (20일 평균 대비 당일 누적)
        if raw_data.volume_ratio_vs_avg20d is not None:
            before = decision.confidence
            decision.confidence = apply_volume_ratio_confidence_adjustment(
                decision.confidence, raw_data.volume_ratio_vs_avg20d,
            )
            if decision.confidence != before:
                logger.info("Volume ratio adj [%s]: %d → %d (ratio=%.2fx avg20d)",
                            raw.ticker, before, decision.confidence, raw_data.volume_ratio_vs_avg20d)

        # 3c. 섹터 모멘텀 보정 (alpha-scanner sector rotation)
        if ctx and ctx.sector_momentum is not None:
            before = decision.confidence
            decision.confidence = apply_sector_momentum_confidence_adjustment(
                decision.confidence,
                ctx.sector_momentum.sector_rotation_signal,
                ctx.sector_momentum.sector_momentum_score,
            )
            if decision.confidence != before:
                logger.info(
                    "Sector momentum adj [%s]: %d → %d (sector=%s signal=%s score=%s)",
                    raw.ticker,
                    before,
                    decision.confidence,
                    ctx.sector_momentum.sector or "N/A",
                    ctx.sector_momentum.sector_rotation_signal or "N/A",
                    f"{ctx.sector_momentum.sector_momentum_score:.1f}" if ctx.sector_momentum.sector_momentum_score is not None else "N/A",
                )

        # 4. Detection delay
        effective_entry_delay_ms = compute_effective_entry_delay_ms(disclosed_at, decision.decided_at)
        if effective_entry_delay_ms is not None:
            before = decision.confidence
            decision.confidence = apply_delay_confidence_adjustment(decision.confidence, effective_entry_delay_ms)
            if decision.confidence != before:
                logger.info("Delay confidence adj [%s]: %d → %d (delay=%.1fs)",
                            raw.ticker, before, decision.confidence, effective_entry_delay_ms / 1000)

        # 5. 하락장 감점
        market_snapshot = market.snapshot
        before = decision.confidence
        # breadth_ratio: 두 시장 중 더 좋은 쪽 사용 (상승장 부스트 판단용)
        _br = None
        if market_snapshot.kospi_breadth_ratio is not None or market_snapshot.kosdaq_breadth_ratio is not None:
            _br = max(
                market_snapshot.kospi_breadth_ratio or 0.0,
                market_snapshot.kosdaq_breadth_ratio or 0.0,
            )
        decision.confidence = apply_market_confidence_adjustment(
            decision.confidence, market_snapshot.kospi_change_pct, market_snapshot.kosdaq_change_pct,
            breadth_ratio=_br,
        )
        if decision.confidence != before:
            worst = min(market_snapshot.kospi_change_pct or 0.0, market_snapshot.kosdaq_change_pct or 0.0)
            best = max(market_snapshot.kospi_change_pct or 0.0, market_snapshot.kosdaq_change_pct or 0.0)
            logger.info("Market confidence adj [%s]: %d → %d (best_idx=%+.1f%% worst_idx=%+.1f%% br=%s)",
                        raw.ticker, before, decision.confidence, best, worst,
                        f"{_br:.2f}" if _br is not None else "N/A")

        # 6. v67: 변동성 레짐 기반 confidence 보정
        vol_regime = detect_volatility_regime(
            kospi_change_pct=market_snapshot.kospi_change_pct,
            kosdaq_change_pct=market_snapshot.kosdaq_change_pct,
            vol_pct_20d=ctx.vol_pct_20d if ctx else None,
            atr_14=ctx.atr_14 if ctx else None,
        )
        before = decision.confidence
        decision.confidence = apply_volatility_confidence_adjustment(decision.confidence, vol_regime)
        if decision.confidence != before:
            logger.info("Volatility regime adj [%s]: %d → %d (regime=%s)",
                        raw.ticker, before, decision.confidence, vol_regime)

        # 7. v67: 뉴스 카테고리별 confidence 보정
        before = decision.confidence
        decision.confidence = apply_news_category_confidence_adjustment(decision.confidence, news_cat)
        if decision.confidence != before:
            logger.info("News category adj [%s]: %d → %d (category=%s)",
                        raw.ticker, before, decision.confidence, news_cat)

        # v72: POS_WEAK bucket 페널티 — 실전 0%승률, -2.28% (전량 손실)
        if bucket == Bucket.POS_WEAK:
            before = decision.confidence
            decision.confidence = max(0, decision.confidence - 8)
            logger.info("POS_WEAK penalty [%s]: %d → %d", raw.ticker, before, decision.confidence)

        if news_signal is not None and news_signal.impact_score is not None:
            before = decision.confidence
            decision.confidence = apply_impact_score_confidence_adjustment(
                decision.confidence,
                news_signal.impact_score,
            )
            if decision.confidence != before:
                logger.info(
                    "Impact score adj [%s]: %d → %d (impact=%d)",
                    raw.ticker,
                    before,
                    decision.confidence,
                    news_signal.impact_score,
                )

        # 8. v68: 종목별 학습 기반 confidence 조정
        learner = _get_ticker_learner(config)
        if learner is not None and learner.total_trades == 0:
            logger.debug("TickerLearner active but no historical trades loaded")
        if learner is not None:
            before = decision.confidence
            adj = learner.get_adjustment(raw.ticker)
            if adj != 0:
                decision.confidence = max(0, min(100, decision.confidence + adj))
                stats = learner.get_stats(raw.ticker)
                logger.info("Ticker learning adj [%s]: %d → %d (adj=%+d, win_rate=%.0f%%, trades=%d)",
                            raw.ticker, before, decision.confidence, adj,
                            stats.win_rate * 100 if stats else 0,
                            stats.total_trades if stats else 0)

        # 9. v68: 멀티 타임프레임 추세 확인
        if config.mtf_enabled and kis is not None:
            from kindshot.mtf_analysis import analyze_mtf
            mtf_result = await analyze_mtf(raw.ticker, kis, config)
            before = decision.confidence
            decision.confidence = apply_mtf_confidence_adjustment(decision.confidence, mtf_result.alignment_score)
            if decision.confidence != before:
                logger.info("MTF alignment adj [%s]: %d → %d (alignment=%d, %s)",
                            raw.ticker, before, decision.confidence,
                            mtf_result.alignment_score, mtf_result.detail)
            else:
                logger.debug("MTF neutral [%s]: alignment=%d (%s)",
                             raw.ticker, mtf_result.alignment_score, mtf_result.detail)

        # graduated penalty cap: 강한 시그널은 보호, 약한 시그널은 감점 허용
        # LLM 88+ (대형촉매): cap 8 → 최악 80 (BUY 유지)
        # LLM 83-87 (강한촉매): cap 10 → 최악 73-77 (경계선)
        # LLM <83 (보통촉매): cap 15 → 최악 65-67 (SKIP 가능)
        total_delta = decision.confidence - llm_original_conf
        if total_delta < -_MAX_TOTAL_PENALTY:
            floored = llm_original_conf - _MAX_TOTAL_PENALTY
            logger.warning(
                "Confidence graduated cap [%s]: %d → %d (total_delta=%d exceeded -%d cap, llm=%d)",
                raw.ticker, decision.confidence, floored, total_delta, _MAX_TOTAL_PENALTY, llm_original_conf,
            )
            decision.confidence = floored

        pattern_boost = match_profit_boost(
            recent_pattern_profile,
            news_type=news_cat,
            ticker=raw.ticker,
            hour_bucket=_hour_bucket_from_datetime(detected_at),
        )
        if pattern_boost is not None and pattern_boost.confidence_delta:
            before = decision.confidence
            decision.confidence = min(100, decision.confidence + pattern_boost.confidence_delta)
            logger.info(
                "Recent pattern boost [%s]: %d → %d (%s %s count=%d total=%+.3f)",
                raw.ticker,
                before,
                decision.confidence,
                pattern_boost.pattern_type,
                pattern_boost.key,
                pattern_boost.count,
                pattern_boost.total_pnl_pct,
            )

    # 킬 스위치: 연패 시 size_hint 다운그레이드
    if decision.action == Action.BUY and guardrail_state is not None:
        adjusted = get_kill_switch_size_hint(config, guardrail_state, decision.size_hint.value)
        if adjusted != decision.size_hint.value:
            from kindshot.models import SizeHint
            logger.info(
                "KILL SWITCH size down [%s]: %s → %s (consecutive_losses=%d)",
                raw.ticker, decision.size_hint.value, adjusted,
                guardrail_state.consecutive_stop_losses,
            )
            decision.size_hint = SizeHint(adjusted)

    # spread > 30bps → size_hint 한 단계 다운 (유동성 리스크)
    if decision.action == Action.BUY and raw_data.spread_bps is not None and raw_data.spread_bps > 30:
        from kindshot.guardrails import downgrade_size_hint
        from kindshot.models import SizeHint
        old_hint = decision.size_hint.value
        new_hint = downgrade_size_hint(old_hint)
        if new_hint != old_hint:
            logger.info(
                "Spread size down [%s]: %s → %s (spread=%.1f bps)",
                raw.ticker, old_hint, new_hint, raw_data.spread_bps,
            )
            decision.size_hint = SizeHint(new_hint)

    # 09:00~09:30 장 초반 변동성 구간: size_hint 최대 M (L → M 다운그레이드)
    if decision.action == Action.BUY:
        now_kst = detected_at.astimezone(_KST) if detected_at.tzinfo else detected_at.replace(tzinfo=_KST)
        if now_kst.hour == 9 and now_kst.minute < 30 and decision.size_hint.value == "L":
            from kindshot.models import SizeHint
            logger.info(
                "Opening volatility size cap [%s]: L → M (09:00~09:30)",
                raw.ticker,
            )
            decision.size_hint = SizeHint.M

    _attach_decision_summary(event_rec, decision)

    if decision.action == Action.BUY and guardrail_profile.supportive_market:
        logger.info(
            "Dynamic guardrail profile [%s]: min=%d opening=%d afternoon=%d fast_cutoff=%02d:%02d",
            raw.ticker,
            guardrail_profile.min_buy_confidence,
            guardrail_profile.opening_min_confidence,
            guardrail_profile.afternoon_min_confidence,
            guardrail_profile.fast_profile_no_buy_after_kst_hour,
            guardrail_profile.fast_profile_no_buy_after_kst_minute,
        )

    _guardrail_t0 = time.monotonic()
    pattern_loss = match_loss_guardrail(
        recent_pattern_profile,
        news_type=news_cat,
        ticker=raw.ticker,
        hour_bucket=_hour_bucket_from_datetime(detected_at),
    )
    if decision.action == Action.BUY and pattern_loss is not None:
        event_rec.skip_stage = SkipStage.GUARDRAIL
        event_rec.skip_reason = pattern_loss.guardrail_reason or "PATTERN_LOSS_GUARDRAIL"
        event_rec.guardrail_result = event_rec.skip_reason
        guardrail_ms = int((time.monotonic() - _guardrail_t0) * 1000)
        profile = _finalize_profile(decision)
        await log.write(event_rec)
        _mark_skip(counters, stage=SkipStage.GUARDRAIL.value, reason=event_rec.skip_reason)
        logger.info(
            "Recent pattern guardrail [%s]: %s (%s %s count=%d total=%+.3f)",
            raw.ticker,
            event_rec.skip_reason,
            pattern_loss.pattern_type,
            pattern_loss.key,
            pattern_loss.count,
            pattern_loss.total_pnl_pct,
        )
        return ProcessOutcome(
            event_id=processed.event_id,
            skip_stage=SkipStage.GUARDRAIL,
            skip_reason=event_rec.skip_reason,
            decision=decision,
            pipeline_profile=profile,
        )

    gr = check_guardrails(
        ticker=raw.ticker,
        config=config,
        spread_bps=raw_data.spread_bps if ctx else None,
        adv_value_20d=raw_data.adv_value_20d if ctx else None,
        ret_today=raw_data.ret_today if ctx else None,
        delay_ms=effective_entry_delay_ms,
        state=guardrail_state,
        headline=raw.title,
        sector=raw_data.sector if ctx else "",
        quote_risk_state=raw_data.quote_risk_state if ctx else None,
        orderbook_snapshot=raw_data.orderbook_snapshot if ctx else None,
        intraday_value_vs_adv20d=raw_data.intraday_value_vs_adv20d if ctx else None,
        prior_volume_rate=raw_data.prior_volume_rate if ctx else None,
        volume_ratio_vs_avg20d=raw_data.volume_ratio_vs_avg20d if ctx else None,
        decision_action=decision.action,
        decision_confidence=decision.confidence,
        decision_time_kst=decision.decided_at,
        decision_hold_minutes=decision_hold_minutes if decision.action == Action.BUY else 0,
        adv_threshold=config.adv_threshold_for_bucket(bucket.value),
        decision_size_hint=decision.size_hint.value,
        dynamic_profile=guardrail_profile,
    )
    guardrail_ms = int((time.monotonic() - _guardrail_t0) * 1000)
    if not gr.passed:
        event_rec.skip_stage = SkipStage.GUARDRAIL
        event_rec.skip_reason = gr.reason
        event_rec.guardrail_result = gr.reason
        profile = _finalize_profile(decision)
        await log.write(event_rec)
        _mark_skip(counters, stage=SkipStage.GUARDRAIL.value, reason=gr.reason)
        # BUY guardrail 차단은 텔레그램으로 운영자에게 즉시 알림
        shadow_scheduled = decision.action == Action.BUY and decision.confidence >= 75
        if decision.action == Action.BUY:
            from kindshot.telegram_ops import try_send_high_conf_skip
            try_send_high_conf_skip(
                ticker=raw.ticker,
                corp_name=raw.corp_name,
                headline=raw.title,
                confidence=decision.confidence,
                skip_reason=gr.reason or "UNKNOWN",
                shadow_scheduled=shadow_scheduled,
                decision_source=decision.decision_source,
                mode=mode,
            )
        # v66: 차단된 BUY의 shadow snapshot — 기회비용 추적
        if shadow_scheduled:
            scheduler.schedule_t0(
                event_id=f"shadow_{processed.event_id}",
                ticker=raw.ticker,
                t0_basis=T0Basis.DECIDED_AT,
                t0_ts=decision.decided_at,
                run_id=run_id,
                mode=mode,
                is_buy_decision=False,  # snapshot만, 가상 매매 아님
            )
        return ProcessOutcome(
            event_id=processed.event_id,
            skip_stage=SkipStage.GUARDRAIL,
            skip_reason=gr.reason,
            decision=decision,
            pipeline_profile=profile,
        )

    if decision.action == Action.BUY and guardrail_state is not None:
        guardrail_state.record_buy(raw.ticker, sector=raw_data.sector if ctx else "")

    # Live mode: 실주문 매수
    if mode == "live" and order_executor is not None and decision.action == Action.BUY:
        try:
            _order_t0 = time.monotonic()
            _macro_mult = market.snapshot.macro_position_multiplier or 1.0
            _base_order_won = config.order_size_for_hint(decision.size_hint.value)
            _buy_result = await order_executor.buy_market(
                event_id=processed.event_id,
                ticker=raw.ticker,
                target_won=_base_order_won * _macro_mult,
                current_price=raw_data.px if raw_data.px else 0,
            )
            order_attempt_ms = int((time.monotonic() - _order_t0) * 1000)
        except Exception:
            logger.exception("LIVE BUY order error [%s]", raw.ticker)
            order_attempt_ms = int((time.monotonic() - _order_t0) * 1000)

    # 보유시간 차등화: 키워드 기반 hold profile
    is_buy = decision.action == Action.BUY
    hold_minutes = decision_hold_minutes if is_buy else 0

    scheduler.schedule_t0(
        event_id=processed.event_id,
        ticker=raw.ticker,
        t0_basis=T0Basis.DECIDED_AT,
        t0_ts=decision.decided_at,
        run_id=run_id,
        mode=mode,
        is_buy_decision=is_buy,
        max_hold_minutes=hold_minutes,
        confidence=decision.confidence if is_buy else 0,
        size_hint=decision.size_hint.value if is_buy else "M",
        support_reference_px=raw_data.support_reference_px if is_buy else None,
    )

    # SKIP 종목 후속 추적: false negative 식별을 위해 가격 스냅샷 스케줄
    if decision.action == Action.SKIP and bucket in (Bucket.POS_STRONG, Bucket.POS_WEAK):
        scheduler.schedule_t0(
            event_id=f"skip_{processed.event_id}",
            ticker=raw.ticker,
            t0_basis=T0Basis.DECIDED_AT,
            t0_ts=decision.decided_at,
            run_id=run_id,
            mode=mode,
            is_buy_decision=False,
        )
    if mode == "paper":
        logger.info(
            "PAPER %s [%s] conf=%d hint=%s: %s",
            decision.action.value,
            raw.ticker,
            decision.confidence,
            decision.size_hint.value,
            decision.reason,
        )
    else:
        logger.info(
            "%s [%s] conf=%d hint=%s: %s",
            decision.action.value,
            raw.ticker,
            decision.confidence,
            decision.size_hint.value,
            decision.reason,
        )

    # 실시간 텔레그램 BUY 알림 (best-effort, 실패해도 파이프라인 중단 안 함)
    if decision.action == Action.BUY:
        from kindshot.telegram_ops import try_send_buy_signal
        from kindshot.guardrails import get_dynamic_tp_pct, get_dynamic_stop_loss_pct
        adv_display = f"{raw_data.adv_value_20d/1e8:.0f}억" if raw_data.adv_value_20d else ""
        buy_tp = get_dynamic_tp_pct(config, decision.confidence, hold_minutes)
        buy_sl = get_dynamic_stop_loss_pct(config, decision.confidence, hold_minutes)
        try_send_buy_signal(
            ticker=raw.ticker,
            corp_name=raw.corp_name,
            headline=raw.title,
            bucket=bucket.value,
            confidence=decision.confidence,
            size_hint=decision.size_hint.value,
            reason=decision.reason,
            keyword_hits=keyword_hits,
            hold_minutes=hold_minutes,
            ret_today=raw_data.ret_today,
            spread_bps=raw_data.spread_bps,
            adv_display=adv_display,
            mode=mode,
            decision_source=decision.decision_source,
            tp_pct=buy_tp,
            sl_pct=buy_sl,
        )

    profile = _finalize_profile(decision)
    await log.write(event_rec)
    await log.write(decision)
    if counters is not None:
        counters.totals["decisions_emitted"] += 1
        counters.totals[f"decision_action_{decision.action.value}"] += 1
        counters.totals[f"decision_source_{decision.decision_source}"] += 1

    _pipeline_ms = profile.pipeline_total_ms or int((time.monotonic() - _pipeline_t0) * 1000)
    logger.info(
        "Pipeline total [%s] %s: %dms (llm=%dms)",
        raw.ticker, decision.action.value, _pipeline_ms, decision.llm_latency_ms,
    )
    return ProcessOutcome(
        event_id=processed.event_id,
        action=decision.action,
        decision=decision,
        pipeline_profile=profile,
    )


def _make_error_event_record(
    *,
    mode: str,
    config: Config,
    run_id: str,
    processed: ProcessedEvent,
    raw: RawDisclosure,
    detected_at: datetime,
    feed_source: str,
    bucket_result,
    skip_stage: SkipStage,
    skip_reason: str,
    market_snapshot,
) -> EventRecord:
    """Build an EventRecord for LLM error paths (DRY helper)."""
    return EventRecord(
        mode=mode, schema_version=config.schema_version, run_id=run_id,
        event_id=processed.event_id, event_id_method=processed.event_id_method,
        event_kind=processed.event_kind, parent_id=processed.parent_id,
        event_group_id=processed.event_group_id,
        parent_match_method=processed.parent_match_method,
        parent_match_score=processed.parent_match_score,
        parent_candidate_count=processed.parent_candidate_count,
        source=feed_source, dorg=raw.dorg, rss_guid=raw.rss_guid, rss_link=raw.link,
        kind_uid=processed.kind_uid,
        disclosed_at=None, disclosed_at_missing=True,
        detected_at=detected_at, delay_ms=None,
        ticker=raw.ticker, corp_name=raw.corp_name, headline=raw.title,
        bucket=bucket_result.bucket, keyword_hits=bucket_result.keyword_hits,
        skip_stage=skip_stage, skip_reason=skip_reason,
        market_ctx=market_snapshot,
    )


async def process_registered_event(
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
    unknown_review_queue: Optional[asyncio.Queue] = None,
    health_state: Optional[object] = None,
    order_executor: Optional[object] = None,
    recent_pattern_profile: Optional[object] = None,
    registry: Optional[EventRegistry] = None,
    premarket_pending: Optional[list] = None,
    news_cluster_tracker: Optional[TickerNewsClusterTracker] = None,
) -> None:
    """Process an event that already passed dedup/registry."""
    _tracer = get_tracer()
    _t_proc = _tracer.process_start(processed.event_id, raw.ticker, raw.title) if _tracer else None
    detected_at = raw.detected_at
    analysis_headline = normalize_analysis_headline(raw.title)
    if analysis_headline and analysis_headline != raw.title:
        logger.info("Normalized analysis headline [%s]: %s -> %s", raw.ticker, raw.title[:80], analysis_headline[:80])

    # 1.5. Skip correction/withdrawal events (only originals proceed to decision)
    if processed.event_kind in (EventKind.CORRECTION, EventKind.WITHDRAWAL):
        if config.news_exit_enabled and scheduler.has_open_position(raw.ticker):
            closed = await scheduler.force_exit_ticker(
                raw.ticker,
                exit_type="correction_exit",
                horizon="correction",
            )
            if closed:
                logger.info(
                    "Correction/withdrawal liquidation [%s]: closed=%d headline=%s",
                    raw.ticker,
                    closed,
                    analysis_headline[:80],
                )
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
    news_signal = build_news_signal(
        headline=raw.title,
        ticker=raw.ticker,
        corp_name=raw.corp_name,
        detected_at=detected_at,
        dorg=raw.dorg,
        keyword_hits=bucket_result.keyword_hits,
        cluster_tracker=news_cluster_tracker,
    )
    if bucket_result.bucket == Bucket.UNKNOWN:
        _append_unknown_headline(config.log_dir, raw.title, raw.ticker)
        review_request = UnknownReviewRequest(
            event_id=processed.event_id,
            detected_at=detected_at,
            runtime_mode=mode,
            ticker=raw.ticker,
            corp_name=raw.corp_name,
            headline=raw.title,
            rss_link=raw.link,
            rss_guid=raw.rss_guid,
            published=raw.published,
            source=feed_source,
        )
        try:
            append_unknown_inbox(config, review_request)
        except Exception:
            logger.warning("UNKNOWN inbox write failed for %s", processed.event_id, exc_info=True)
        if unknown_review_queue is not None:
            try:
                unknown_review_queue.put_nowait(review_request)
            except asyncio.QueueFull:
                logger.warning("UNKNOWN review queue full; dropping %s", processed.event_id)

    outcome: ProcessOutcome
    _t_llm = _tracer.llm_start(raw.ticker) if _tracer and bucket_result.bucket == Bucket.POS_STRONG else None
    try:
        outcome = await execute_bucket_path(
            raw=raw,
            processed=processed,
            bucket=bucket_result.bucket,
            keyword_hits=bucket_result.keyword_hits,
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
            health_state=health_state,
            order_executor=order_executor,
            recent_pattern_profile=recent_pattern_profile,
            news_signal=news_signal,
        )
    except (LlmTimeoutError, LlmCallError, LlmParseError) as llm_exc:
        err_type = type(llm_exc).__name__
        if _tracer and _t_llm is not None:
            _tracer.llm_end(_t_llm, raw.ticker, error=err_type)
        if counters is not None:
            counters.errors[f"llm_{err_type}"] += 1
        if health_state is not None and hasattr(health_state, "record_llm_fallback"):
            health_state.record_llm_fallback()

        # 안전망: POS 버킷이면 rule_fallback으로 재시도 (LLM 없이)
        if bucket_result.bucket in (Bucket.POS_STRONG, Bucket.POS_WEAK):
            logger.warning(
                "Outer LLM error [%s] %s, retrying via rule_fallback: %s",
                raw.ticker, err_type, llm_exc,
            )
            try:
                outcome = await execute_bucket_path(
                    raw=raw,
                    processed=processed,
                    bucket=bucket_result.bucket,
                    keyword_hits=bucket_result.keyword_hits,
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
                    health_state=health_state,
                    order_executor=order_executor,
                    recent_pattern_profile=recent_pattern_profile,
                    news_signal=news_signal,
                )
            except Exception:
                logger.warning("Rule fallback retry also failed for [%s]", raw.ticker, exc_info=True)
                skip_stage = SkipStage.LLM_ERROR
                skip_reason = f"LLM_{err_type}_FALLBACK_FAIL"
                outcome = ProcessOutcome(processed.event_id, skip_stage=skip_stage, skip_reason=skip_reason)
                err_rec = _make_error_event_record(
                    mode=mode, config=config, run_id=run_id, processed=processed,
                    raw=raw, detected_at=detected_at, feed_source=feed_source,
                    bucket_result=bucket_result, skip_stage=skip_stage,
                    skip_reason=skip_reason, market_snapshot=market.snapshot,
                )
                await log.write(err_rec)
                _mark_skip(counters, stage=skip_stage.value, reason=skip_reason)
                scheduler.schedule_t0(event_id=processed.event_id, ticker=raw.ticker, t0_basis=T0Basis.DETECTED_AT, t0_ts=detected_at, run_id=run_id, mode=mode)
        else:
            skip_stage = SkipStage.LLM_ERROR
            skip_reason = f"LLM_{err_type}"
            outcome = ProcessOutcome(processed.event_id, skip_stage=skip_stage, skip_reason=skip_reason)
            err_rec = _make_error_event_record(
                mode=mode, config=config, run_id=run_id, processed=processed,
                raw=raw, detected_at=detected_at, feed_source=feed_source,
                bucket_result=bucket_result, skip_stage=skip_stage,
                skip_reason=skip_reason, market_snapshot=market.snapshot,
            )
            await log.write(err_rec)
            _mark_skip(counters, stage=skip_stage.value, reason=skip_reason)
    except Exception:
        logger.exception("Unexpected error processing %s [%s]", processed.event_id, raw.ticker)
        if _tracer and _t_llm is not None:
            _tracer.llm_end(_t_llm, raw.ticker, error="unexpected")
        if counters is not None:
            counters.errors["unexpected_error"] = counters.errors.get("unexpected_error", 0) + 1
        outcome = ProcessOutcome(processed.event_id, skip_stage=SkipStage.LLM_ERROR, skip_reason="UNEXPECTED_ERROR")
        try:
            err_rec = _make_error_event_record(
                mode=mode, config=config, run_id=run_id, processed=processed,
                raw=raw, detected_at=detected_at, feed_source=feed_source,
                bucket_result=bucket_result, skip_stage=SkipStage.LLM_ERROR,
                skip_reason="UNEXPECTED_ERROR", market_snapshot=market.snapshot,
            )
            await log.write(err_rec)
        except Exception:
            logger.exception("Failed to log UNEXPECTED_ERROR event for %s", processed.event_id)
    else:
        if _tracer and _t_llm is not None:
            _tracer.llm_end(_t_llm, raw.ticker)

    # Health state tracking: guardrail blocks + decisions
    if health_state is not None:
        if outcome.skip_stage == SkipStage.GUARDRAIL and outcome.skip_reason:
            if hasattr(health_state, "record_guardrail_block"):
                health_state.record_guardrail_block(outcome.skip_reason)
        if outcome.pipeline_profile is not None and hasattr(health_state, "record_pipeline_profile"):
            health_state.record_pipeline_profile(
                outcome.pipeline_profile,
                decision_source=outcome.decision.decision_source if outcome.decision is not None else "",
            )
        if outcome.action is not None and outcome.decision is not None and hasattr(health_state, "record_decision"):
            health_state.record_decision(
                outcome.action.value,
                latency_ms=outcome.decision.llm_latency_ms,
                decision_source=outcome.decision.decision_source,
                cache_layer=outcome.decision.cache_layer or "",
            )

    # 장전 재평가: iv_ratio=0으로 INTRADAY_VALUE_TOO_THIN된 POS 이벤트를
    # registry에서 해제해 장 시작 후 재처리 가능하게 함
    if (
        premarket_pending is not None
        and registry is not None
        and outcome.skip_reason == "INTRADAY_VALUE_TOO_THIN"
        and detected_at.astimezone(_KST).hour < 9
    ):
        registry.unmark(processed.event_id)
        premarket_pending.append((raw, processed))
        logger.info(
            "PREMARKET_DEFERRED [%s] %s — iv_ratio=0, 장 시작 후 재평가 예정",
            raw.ticker, processed.event_id[:8],
        )
        if counters is not None:
            counters.totals["premarket_deferred"] = counters.totals.get("premarket_deferred", 0) + 1

    if _tracer and _t_proc is not None:
        _tracer.process_end(_t_proc, processed.event_id, outcome.action.value if outcome.action else (outcome.skip_reason or "SKIP"))


async def process_unknown_promotion(
    *,
    request: UnknownReviewRequest,
    review,
    decision_engine: DecisionEngine,
    market: MarketMonitor,
    scheduler: SnapshotScheduler,
    log: JsonlLogger,
    config: Config,
    run_id: str,
    kis: Optional[KisClient],
    counters: Optional[RuntimeCounters],
    guardrail_state: Optional[GuardrailState],
    news_cluster_tracker: Optional[TickerNewsClusterTracker] = None,
) -> None:
    promotion = evaluate_unknown_promotion(config, request, review)
    if promotion.promotion_status != PromotionStatus.PROMOTED:
        append_unknown_promotion(config, request.detected_at, promotion)
        return

    derived_event_id = _promoted_event_id(request.event_id, review.suggested_bucket)
    synthetic_raw = RawDisclosure(
        title=request.headline,
        link=request.rss_link,
        rss_guid=request.rss_guid,
        published=request.published,
        ticker=request.ticker,
        corp_name=request.corp_name,
        detected_at=request.detected_at,
    )
    synthetic_processed = ProcessedEvent(
        event_id=derived_event_id,
        event_id_method=EventIdMethod.FALLBACK,
        event_kind=EventKind.ORIGINAL,
        parent_id=None,
        event_group_id=derived_event_id,
        parent_match_method=None,
        parent_match_score=None,
        parent_candidate_count=None,
        kind_uid=None,
        raw=synthetic_raw,
    )

    try:
        outcome = await execute_bucket_path(
            raw=synthetic_raw,
            processed=synthetic_processed,
            bucket=review.suggested_bucket,
            keyword_hits=[],
            decision_engine=decision_engine,
            market=market,
            scheduler=scheduler,
            log=log,
            config=config,
            run_id=run_id,
            kis=kis,
            counters=counters,
            mode=request.runtime_mode,
            guardrail_state=guardrail_state,
            feed_source=request.source,
            analysis_tag_override="UNKNOWN_PROMOTED",
            promotion_original_event_id=request.event_id,
            promotion_original_bucket=Bucket.UNKNOWN,
            promotion_confidence=review.confidence,
            promotion_policy=promotion.promotion_policy,
            news_signal=build_news_signal(
                headline=request.headline,
                ticker=request.ticker,
                corp_name=request.corp_name,
                detected_at=request.detected_at,
                keyword_hits=[],
                cluster_tracker=news_cluster_tracker,
            ),
        )
    except LlmTimeoutError:
        if counters is not None:
            counters.errors["llm_timeout"] += 1
        promotion.promotion_status = PromotionStatus.ERROR
        promotion.derived_event_id = derived_event_id
        promotion.gate_reasons = ["PROMOTION_EXECUTION_ERROR"]
        promotion.skip_stage = SkipStage.LLM_TIMEOUT
        promotion.skip_reason = "LLM_TIMEOUT"
        append_unknown_promotion(config, request.detected_at, promotion)
        return
    except LlmCallError:
        if counters is not None:
            counters.errors["llm_call_error"] += 1
        promotion.promotion_status = PromotionStatus.ERROR
        promotion.derived_event_id = derived_event_id
        promotion.gate_reasons = ["PROMOTION_EXECUTION_ERROR"]
        promotion.skip_stage = SkipStage.LLM_ERROR
        promotion.skip_reason = "LLM_ERROR"
        append_unknown_promotion(config, request.detected_at, promotion)
        return
    except LlmParseError:
        if counters is not None:
            counters.errors["llm_parse_error"] += 1
        promotion.promotion_status = PromotionStatus.ERROR
        promotion.derived_event_id = derived_event_id
        promotion.gate_reasons = ["PROMOTION_EXECUTION_ERROR"]
        promotion.skip_stage = SkipStage.LLM_PARSE
        promotion.skip_reason = "LLM_PARSE"
        append_unknown_promotion(config, request.detected_at, promotion)
        return
    except Exception as exc:
        promotion.promotion_status = PromotionStatus.ERROR
        promotion.derived_event_id = derived_event_id
        promotion.gate_reasons = ["PROMOTION_EXECUTION_ERROR"]
        promotion.error = f"{type(exc).__name__}: {exc}"
        append_unknown_promotion(config, request.detected_at, promotion)
        return

    promotion.derived_event_id = derived_event_id
    promotion.decision_action = outcome.action
    promotion.skip_stage = outcome.skip_stage
    promotion.skip_reason = outcome.skip_reason or ""
    append_unknown_promotion(config, request.detected_at, promotion)


async def pipeline_loop(
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
    unknown_review_queue: Optional[asyncio.Queue] = None,
    health_state: Optional[object] = None,
    order_executor: Optional[object] = None,
    recent_pattern_profile: Optional[object] = None,
) -> None:
    """Main pipeline: feed/registry + queue/worker event processing."""
    worker_count = max(1, config.pipeline_workers)
    queue_maxsize = max(1, config.pipeline_queue_maxsize)
    queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=queue_maxsize)
    enqueue_sequence = 0
    news_cluster_tracker = TickerNewsClusterTracker()

    # 장전 재평가: iv_ratio=0으로 SKIP된 POS 이벤트를 09:01 이후 재처리
    premarket_pending: list[tuple] = []
    premarket_reeval_done = False

    async def _worker(worker_idx: int) -> None:
        while True:
            item = await queue.get()
            try:
                if item is None:
                    return
                _priority, _sequence, payload = item
                if payload is None:
                    return
                raw, processed = payload
                await process_registered_event(
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
                    unknown_review_queue=unknown_review_queue,
                    health_state=health_state,
                    order_executor=order_executor,
                    recent_pattern_profile=recent_pattern_profile,
                    registry=registry,
                    premarket_pending=premarket_pending,
                    news_cluster_tracker=news_cluster_tracker,
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
            if health_state is not None and hasattr(health_state, "record_poll"):
                health_state.record_poll()
            if stop_event is not None and stop_event.is_set():
                logger.info("Pipeline stop_event detected, exiting feed loop")
                feed.stop()
                break
            for raw in batch:
                if counters is not None:
                    counters.totals["events_seen"] += 1
                if health_state is not None and hasattr(health_state, "record_event"):
                    health_state.record_event()
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
                sector_snapshot = None
                sector_row = None
                if config.alpha_scanner_api_base_url:
                    sector_snapshot = await fetch_alpha_scanner_sector_snapshot(
                        config.alpha_scanner_api_base_url,
                        config.alpha_scanner_api_timeout_s,
                    )
                    sector_row = lookup_sector_snapshot_ticker(sector_snapshot, raw.ticker)
                priority_item = (*_queue_priority_key(sector_row, enqueue_sequence), (raw, processed))
                enqueue_sequence += 1
                await queue.put(priority_item)
                if _tracer and _t_q is not None:
                    _tracer.queue_put_done(_t_q)
                if counters is not None:
                    counters.totals["events_enqueued"] += 1

            # 장전 재평가: 09:01 이후 pending 이벤트 재주입
            if not premarket_reeval_done and premarket_pending:
                now_kst = datetime.now(_KST)
                if now_kst.hour >= 9 and now_kst.minute >= 1:
                    premarket_reeval_done = True
                    n = len(premarket_pending)
                    logger.info("PREMARKET_REEVAL: %d건 장전 이벤트 재처리 시작", n)
                    for p_raw, p_processed in premarket_pending:
                        sector_snapshot = None
                        sector_row = None
                        if config.alpha_scanner_api_base_url:
                            sector_snapshot = await fetch_alpha_scanner_sector_snapshot(
                                config.alpha_scanner_api_base_url,
                                config.alpha_scanner_api_timeout_s,
                            )
                            sector_row = lookup_sector_snapshot_ticker(sector_snapshot, p_raw.ticker)
                        priority_item = (*_queue_priority_key(sector_row, enqueue_sequence), (p_raw, p_processed))
                        enqueue_sequence += 1
                        await queue.put(priority_item)
                    premarket_pending.clear()
                    if counters is not None:
                        counters.totals["premarket_reeval_injected"] = n

        # Feed stopped naturally. Drain queue before shutdown.
        await queue.join()
        for _ in workers:
            await queue.put(((99, 0.0, 0.0), enqueue_sequence, None))
            enqueue_sequence += 1
        await asyncio.gather(*workers, return_exceptions=True)
    except asyncio.CancelledError:
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        raise
