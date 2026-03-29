"""Runtime consumer for framework-emitted strategy signals."""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime
from typing import Awaitable, Callable, Optional

from kindshot.config import Config
from kindshot.context_card import build_context_card
from kindshot.guardrails import (
    calculate_position_size,
    check_guardrails,
    detect_volatility_regime,
    get_dynamic_stop_loss_pct,
    get_dynamic_tp_pct,
    resolve_dynamic_guardrail_profile,
)
from kindshot.logger import JsonlLogger
from kindshot.models import (
    Action,
    Bucket,
    DecisionRecord,
    EventIdMethod,
    EventKind,
    EventRecord,
    PipelineLatencyProfile,
    SkipStage,
    StrategySignalRecord,
    T0Basis,
)
from kindshot.runtime_artifacts import update_runtime_artifact_index
from kindshot.strategy import StrategyRegistry, TradeSignal
from kindshot.telegram_ops import try_send_buy_signal, try_send_high_conf_skip
from kindshot.tz import KST as _KST

logger = logging.getLogger(__name__)


def _resolve_detected_at(detected_at: datetime | None) -> datetime:
    if detected_at is None:
        return datetime.now(_KST)
    if detected_at.tzinfo is None:
        return detected_at.replace(tzinfo=_KST)
    return detected_at.astimezone(_KST)


def _resolve_event_id(signal: TradeSignal, detected_at: datetime) -> str:
    if signal.event_id:
        return signal.event_id
    digest = hashlib.sha256(
        "|".join(
            [
                signal.strategy_name,
                signal.source.value,
                signal.ticker,
                signal.action.value,
                detected_at.isoformat(),
                signal.reason,
            ]
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"st_{digest}"


def _strategy_headline(signal: TradeSignal) -> str:
    headline = signal.headline.strip()
    if headline:
        return headline
    return f"[{signal.source.value}] {signal.strategy_name} {signal.action.value}"


def _mark_skip(counters: Optional[object], reason: str) -> None:
    if counters is None or not hasattr(counters, "totals"):
        return
    counters.totals["events_skipped"] += 1
    counters.skip_stage[SkipStage.GUARDRAIL.value] += 1
    counters.skip_reason[reason] += 1


def _record_execution_health(
    *,
    health_state: Optional[object],
    profile: PipelineLatencyProfile,
    decision: DecisionRecord,
    action: Action | None,
    guardrail_reason: str | None = None,
) -> None:
    if health_state is None:
        return
    if guardrail_reason and hasattr(health_state, "record_guardrail_block"):
        health_state.record_guardrail_block(guardrail_reason)
    if hasattr(health_state, "record_pipeline_profile"):
        health_state.record_pipeline_profile(profile, decision_source=decision.decision_source)
    if action is not None and hasattr(health_state, "record_decision"):
        health_state.record_decision(
            action.value,
            latency_ms=decision.llm_latency_ms,
            decision_source=decision.decision_source,
        )


async def _execute_strategy_signal(
    signal: TradeSignal,
    *,
    log: JsonlLogger,
    config: Config,
    run_id: str,
    mode: str,
    market: Optional[object],
    scheduler: Optional[object],
    kis: Optional[object],
    guardrail_state: Optional[object],
    order_executor: Optional[object],
    health_state: Optional[object],
    counters: Optional[object],
    context_builder: Callable[[str, Optional[object], Optional[Config]], Awaitable[tuple[object, object]]],
) -> None:
    if scheduler is None:
        logger.warning("Strategy signal execution skipped: scheduler unavailable")
        return

    execution_t0 = time.monotonic()
    detected_at = _resolve_detected_at(signal.detected_at)
    event_id = _resolve_event_id(signal, detected_at)
    headline = _strategy_headline(signal)
    market_ctx = market.snapshot if market is not None and hasattr(market, "snapshot") else None
    hold_minutes = config.max_hold_minutes if signal.action == Action.BUY else 0
    ctx, raw = await context_builder(signal.ticker, kis, config)

    decision = DecisionRecord(
        mode=mode,
        schema_version=config.schema_version,
        run_id=run_id,
        event_id=event_id,
        decided_at=detected_at,
        llm_model=f"strategy:{signal.strategy_name}",
        llm_latency_ms=0,
        action=signal.action,
        confidence=signal.confidence,
        size_hint=signal.size_hint,
        reason=signal.reason[:100],
        decision_source="STRATEGY_SIGNAL",
    )
    event_rec = EventRecord(
        mode=mode,
        schema_version=config.schema_version,
        run_id=run_id,
        event_id=event_id,
        event_id_method=EventIdMethod.FALLBACK,
        event_kind=EventKind.ORIGINAL,
        event_group_id=event_id,
        source=signal.source.value,
        detected_at=detected_at,
        ticker=signal.ticker,
        corp_name=signal.corp_name,
        headline=headline,
        analysis_headline=headline,
        bucket=Bucket.POS_STRONG,
        analysis_tag=f"STRATEGY_{signal.strategy_name.upper()}",
        ctx=ctx,
        market_ctx=market_ctx,
    )
    event_rec.decision_action = decision.action.value
    event_rec.decision_confidence = decision.confidence
    event_rec.decision_size_hint = decision.size_hint.value
    event_rec.decision_reason = decision.reason
    event_rec.decision_source = decision.decision_source
    event_rec.decision_llm_latency_ms = decision.llm_latency_ms

    guardrail_ms: int | None = None
    order_attempt_ms: int | None = None

    if signal.action == Action.BUY:
        if market is not None and getattr(market, "is_halted", False):
            reason = "MARKET_NOT_INITIALIZED" if not getattr(market, "is_initialized", True) else "MARKET_HALTED"
            event_rec.skip_stage = SkipStage.GUARDRAIL
            event_rec.skip_reason = reason
            event_rec.guardrail_result = reason
            event_rec.pipeline_profile = PipelineLatencyProfile(
                guardrail_ms=0,
                pipeline_total_ms=int((time.monotonic() - execution_t0) * 1000),
                llm_latency_ms=0,
            )
            await log.write(event_rec)
            _mark_skip(counters, reason)
            _record_execution_health(
                health_state=health_state,
                profile=event_rec.pipeline_profile,
                decision=decision,
                action=None,
                guardrail_reason=reason,
            )
            return

        dynamic_profile = resolve_dynamic_guardrail_profile(
            config,
            kospi_change_pct=market_ctx.kospi_change_pct if market_ctx is not None else None,
            kosdaq_change_pct=market_ctx.kosdaq_change_pct if market_ctx is not None else None,
            kospi_breadth_ratio=market_ctx.kospi_breadth_ratio if market_ctx is not None else None,
            kosdaq_breadth_ratio=market_ctx.kosdaq_breadth_ratio if market_ctx is not None else None,
        )
        guardrail_t0 = time.monotonic()
        gr = check_guardrails(
            ticker=signal.ticker,
            config=config,
            spread_bps=getattr(raw, "spread_bps", None),
            adv_value_20d=getattr(raw, "adv_value_20d", None),
            ret_today=getattr(raw, "ret_today", None),
            delay_ms=0,
            state=guardrail_state,
            headline=headline,
            sector=getattr(raw, "sector", ""),
            quote_risk_state=getattr(raw, "quote_risk_state", None),
            orderbook_snapshot=getattr(raw, "orderbook_snapshot", None),
            intraday_value_vs_adv20d=getattr(raw, "intraday_value_vs_adv20d", None),
            prior_volume_rate=getattr(raw, "prior_volume_rate", None),
            volume_ratio_vs_avg20d=getattr(raw, "volume_ratio_vs_avg20d", None),
            decision_action=decision.action,
            decision_confidence=decision.confidence,
            decision_time_kst=decision.decided_at,
            decision_hold_minutes=hold_minutes,
            adv_threshold=config.adv_threshold_for_bucket(Bucket.POS_STRONG.value),
            decision_size_hint=decision.size_hint.value,
            dynamic_profile=dynamic_profile,
        )
        guardrail_ms = int((time.monotonic() - guardrail_t0) * 1000)
        if not gr.passed:
            event_rec.skip_stage = SkipStage.GUARDRAIL
            event_rec.skip_reason = gr.reason
            event_rec.guardrail_result = gr.reason
            event_rec.pipeline_profile = PipelineLatencyProfile(
                guardrail_ms=guardrail_ms,
                pipeline_total_ms=int((time.monotonic() - execution_t0) * 1000),
                llm_latency_ms=0,
            )
            await log.write(event_rec)
            if gr.reason is not None:
                _mark_skip(counters, gr.reason)
                try_send_high_conf_skip(
                    ticker=signal.ticker,
                    corp_name=signal.corp_name,
                    headline=headline,
                    confidence=decision.confidence,
                    skip_reason=gr.reason,
                    shadow_scheduled=False,
                    decision_source=decision.decision_source,
                    mode=mode,
                )
            _record_execution_health(
                health_state=health_state,
                profile=event_rec.pipeline_profile,
                decision=decision,
                action=None,
                guardrail_reason=gr.reason,
            )
            return

        if guardrail_state is not None and hasattr(guardrail_state, "record_buy"):
            guardrail_state.record_buy(signal.ticker, sector=getattr(raw, "sector", ""))

        if mode == "live" and order_executor is not None:
            order_t0 = time.monotonic()
            macro_mult = market_ctx.macro_position_multiplier if market_ctx is not None and market_ctx.macro_position_multiplier is not None else 1.0
            account_balance = guardrail_state.account_balance if guardrail_state is not None and hasattr(guardrail_state, "account_balance") else 0.0
            orderbook_snapshot = getattr(raw, "orderbook_snapshot", None)
            ask_depth = (
                orderbook_snapshot.ask_price1 * orderbook_snapshot.ask_size1
                if orderbook_snapshot is not None
                else 0.0
            )
            adv_value_20d = getattr(raw, "adv_value_20d", None) or 0.0
            minute_volume = adv_value_20d / 390.0 if adv_value_20d > 0 else 0.0
            atr_pct = getattr(ctx, "atr_14", None)
            target_won = calculate_position_size(
                config,
                decision.size_hint.value,
                account_balance=account_balance,
                minute_volume=minute_volume,
                ask_depth_notional=ask_depth,
                macro_position_multiplier=macro_mult,
                atr_pct=atr_pct,
            )
            await order_executor.buy_market_with_retry(
                event_id=event_id,
                ticker=signal.ticker,
                target_won=target_won,
                current_price=getattr(raw, "px", None) or 0,
            )
            order_attempt_ms = int((time.monotonic() - order_t0) * 1000)

    scheduler.schedule_t0(
        event_id=event_id,
        ticker=signal.ticker,
        t0_basis=T0Basis.DECIDED_AT,
        t0_ts=decision.decided_at,
        run_id=run_id,
        mode=mode,
        is_buy_decision=signal.action == Action.BUY,
        max_hold_minutes=hold_minutes,
        confidence=decision.confidence if signal.action == Action.BUY else 0,
        size_hint=decision.size_hint.value if signal.action == Action.BUY else "M",
        support_reference_px=getattr(raw, "support_reference_px", None) if signal.action == Action.BUY else None,
    )

    if signal.action == Action.BUY:
        adv_value_20d = getattr(raw, "adv_value_20d", None)
        adv_display = f"{adv_value_20d/1e8:.0f}억" if adv_value_20d else ""
        volatility_regime = detect_volatility_regime(
            kospi_change_pct=market_ctx.kospi_change_pct if market_ctx is not None else None,
            kosdaq_change_pct=market_ctx.kosdaq_change_pct if market_ctx is not None else None,
        )
        try_send_buy_signal(
            ticker=signal.ticker,
            corp_name=signal.corp_name,
            headline=headline,
            bucket=signal.source.value,
            confidence=decision.confidence,
            size_hint=decision.size_hint.value,
            reason=decision.reason,
            keyword_hits=[f"strategy:{signal.strategy_name}"],
            hold_minutes=hold_minutes,
            ret_today=getattr(raw, "ret_today", None),
            spread_bps=getattr(raw, "spread_bps", None),
            adv_display=adv_display,
            mode=mode,
            decision_source=decision.decision_source,
            tp_pct=get_dynamic_tp_pct(config, decision.confidence, hold_minutes, volatility_regime=volatility_regime),
            sl_pct=get_dynamic_stop_loss_pct(config, decision.confidence, hold_minutes, volatility_regime=volatility_regime),
        )

    event_rec.pipeline_profile = PipelineLatencyProfile(
        guardrail_ms=guardrail_ms,
        order_attempt_ms=order_attempt_ms,
        pipeline_total_ms=int((time.monotonic() - execution_t0) * 1000),
        llm_latency_ms=0,
    )
    await log.write(event_rec)
    await log.write(decision)
    if counters is not None and hasattr(counters, "totals"):
        counters.totals["decisions_emitted"] += 1
        counters.totals[f"decision_action_{decision.action.value}"] += 1
        counters.totals[f"decision_source_{decision.decision_source}"] += 1
    _record_execution_health(
        health_state=health_state,
        profile=event_rec.pipeline_profile,
        decision=decision,
        action=decision.action,
    )


async def consume_strategy_signals(
    registry: StrategyRegistry,
    *,
    log: JsonlLogger,
    config: Config,
    run_id: str,
    mode: str,
    execute_signals: bool = False,
    market: Optional[object] = None,
    scheduler: Optional[object] = None,
    kis: Optional[object] = None,
    guardrail_state: Optional[object] = None,
    order_executor: Optional[object] = None,
    health_state: Optional[object] = None,
    counters: Optional[object] = None,
    context_builder: Callable[[str, Optional[object], Optional[Config]], Awaitable[tuple[object, object]]] = build_context_card,
) -> None:
    """Persist framework-emitted strategy signals into the shared runtime log."""
    async for signal in registry.stream_all():
        emitted_at = _resolve_detected_at(signal.detected_at)
        event_id = _resolve_event_id(signal, emitted_at)
        record = StrategySignalRecord(
            mode=mode,
            schema_version=config.schema_version,
            run_id=run_id,
            emitted_at=emitted_at,
            strategy_name=signal.strategy_name,
            source=signal.source.value,
            ticker=signal.ticker,
            corp_name=signal.corp_name,
            action=signal.action,
            confidence=signal.confidence,
            size_hint=signal.size_hint,
            reason=signal.reason,
            headline=signal.headline,
            event_id=event_id,
            metadata=signal.metadata,
        )
        await log.write(record)
        await update_runtime_artifact_index(
            config,
            date=emitted_at.strftime("%Y%m%d"),
            artifact="strategy_signals",
            path=log.current_path(),
            recorded_at=emitted_at,
        )
        if execute_signals:
            try:
                await _execute_strategy_signal(
                    signal,
                    log=log,
                    config=config,
                    run_id=run_id,
                    mode=mode,
                    market=market,
                    scheduler=scheduler,
                    kis=kis,
                    guardrail_state=guardrail_state,
                    order_executor=order_executor,
                    health_state=health_state,
                    counters=counters,
                    context_builder=context_builder,
                )
            except Exception:
                logger.exception(
                    "Failed to execute strategy signal [%s] %s",
                    signal.strategy_name,
                    signal.ticker,
                )
