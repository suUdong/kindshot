"""Tests for main pipeline branching: duplicate, LLM failure, guardrail."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kindshot.config import Config
from kindshot.context_card import ContextCardData
from kindshot.decision import LlmTimeoutError, LlmParseError, LlmCallError
from kindshot.feed import RawDisclosure
from kindshot.kis_client import OrderbookSnapshot, QuoteRiskState
from kindshot.models import Bucket, ReviewStatus, SkipStage, UnknownReviewRecord


async def _run_pipeline_once(
    tmp_path,
    raw_items,
    decision_side_effect=None,
    guardrail_passed=True,
    dry_run=False,
    paper=False,
    ctx_raw=None,
    config_overrides=None,
    market_snapshot_overrides=None,
    capture_guardrail_calls=False,
    recent_pattern_profile=None,
    guardrail_state=None,
    ctx_card=None,
):
    """Helper: run one iteration of the pipeline and return logged records."""
    from kindshot.event_registry import EventRegistry
    from kindshot.logger import JsonlLogger
    from kindshot.market import MarketMonitor
    from kindshot.price import PriceFetcher, SnapshotScheduler

    cfg = Config(log_dir=tmp_path / "logs", dry_run=dry_run, paper=paper, **(config_overrides or {}))
    log = JsonlLogger(cfg.log_dir, run_id="test_run")
    registry = EventRegistry()
    market = MarketMonitor(cfg)
    # Simulate initialized market for pipeline tests
    market._initialized = True
    market._halted = False
    if market_snapshot_overrides:
        for attr, value in market_snapshot_overrides.items():
            setattr(market, f"_{attr}", value)
    fetcher = PriceFetcher(kis=None)
    scheduler = SnapshotScheduler(cfg, fetcher, log)

    mock_engine = MagicMock()
    if decision_side_effect:
        mock_engine.decide = AsyncMock(side_effect=decision_side_effect)
        # Rule-based fallback for LLM errors
        from kindshot.models import DecisionRecord, Action, SizeHint
        mock_engine.fallback_decide = MagicMock(return_value=DecisionRecord(
            schema_version="0.1.2", run_id="test_run", event_id="",
            decided_at=datetime.now(timezone.utc), llm_model="rule_fallback",
            llm_latency_ms=0, action=Action.SKIP, confidence=72,
            size_hint=SizeHint.S, reason="rule_fallback:test",
            decision_source="RULE_FALLBACK",
        ))
    else:
        mock_engine.decide = AsyncMock(return_value=None)

    # Import pipeline function
    from kindshot.pipeline import pipeline_loop
    from kindshot.feed import KindFeed

    # Create a mock feed that yields once then stops
    mock_feed = AsyncMock(spec=KindFeed)

    async def _one_batch():
        yield raw_items
    mock_feed.stream = _one_batch

    # Run pipeline with a timeout
    with patch("kindshot.pipeline.build_context_card", new_callable=AsyncMock) as mock_ctx, \
         patch("kindshot.pipeline.check_guardrails") as mock_gr:
        from kindshot.models import ContextCard
        from kindshot.guardrails import GuardrailResult
        mock_ctx.return_value = (
            ctx_card or ContextCard(adv_value_20d=10e9, spread_bps=10.0),
            ctx_raw or ContextCardData(adv_value_20d=10e9, spread_bps=10.0, ret_today=5.0),
        )
        mock_gr.return_value = GuardrailResult(passed=guardrail_passed, reason="BLOCKED" if not guardrail_passed else None)

        mode = "dry_run" if dry_run else ("paper" if paper else "live")
        try:
            await asyncio.wait_for(
                pipeline_loop(
                    mock_feed,
                    registry,
                    mock_engine,
                    market,
                    scheduler,
                    log,
                    cfg,
                    "test_run",
                    None,
                    mode=mode,
                    guardrail_state=guardrail_state,
                    recent_pattern_profile=recent_pattern_profile,
                ),
                timeout=1.0,
            )
        except (asyncio.TimeoutError, StopAsyncIteration):
            pass
        guardrail_calls = mock_gr.call_args_list

    # Read back logged records
    records = []
    for f in (tmp_path / "logs").glob("*.jsonl"):
        for line in f.read_text(encoding="utf-8").strip().split("\n"):
            if line:
                records.append(json.loads(line))
    if capture_guardrail_calls:
        return records, guardrail_calls
    return records


def _make_raw(
    title="삼성전자(005930) - 공급계약 체결",
    link="https://kind.krx.co.kr/?rcpNo=20260305000001",
    detected_at=None,
):
    from kindshot.feed import RawDisclosure
    return RawDisclosure(
        title=title,
        link=link,
        rss_guid="guid1",
        published="2026-03-05T09:12:04+09:00",
        ticker="005930",
        corp_name="삼성전자",
        detected_at=detected_at or datetime(2026, 3, 5, 1, 30, tzinfo=timezone.utc),
    )


async def test_duplicate_logged_with_skip_stage(tmp_path):
    """Duplicate events should be logged with skip_stage=DUPLICATE."""
    raw = _make_raw()
    # Send same item twice → second is duplicate
    records = await _run_pipeline_once(tmp_path, [raw, raw], dry_run=True)

    dup_records = [r for r in records if r.get("skip_stage") == "DUPLICATE"]
    assert len(dup_records) == 1
    assert dup_records[0]["skip_reason"] == "DUPLICATE"


async def test_llm_timeout_uses_fallback(tmp_path):
    """LLM timeout should trigger rule-based fallback decision."""
    raw = _make_raw()
    records = await _run_pipeline_once(
        tmp_path, [raw],
        decision_side_effect=LlmTimeoutError("timeout"),
    )

    # Fallback produces a decision record with RULE_FALLBACK source
    decision_records = [r for r in records if r.get("type") == "decision" and r.get("decision_source") == "RULE_FALLBACK"]
    assert len(decision_records) == 1


async def test_llm_parse_error_uses_fallback(tmp_path):
    """LLM parse failure should trigger rule-based fallback decision."""
    raw = _make_raw()
    records = await _run_pipeline_once(
        tmp_path, [raw],
        decision_side_effect=LlmParseError("bad json"),
    )

    decision_records = [r for r in records if r.get("type") == "decision" and r.get("decision_source") == "RULE_FALLBACK"]
    assert len(decision_records) == 1


async def test_llm_call_error_uses_fallback(tmp_path):
    """LLM call failure should trigger rule-based fallback decision."""
    raw = _make_raw()
    records = await _run_pipeline_once(
        tmp_path, [raw],
        decision_side_effect=LlmCallError("network error"),
    )

    decision_records = [r for r in records if r.get("type") == "decision" and r.get("decision_source") == "RULE_FALLBACK"]
    assert len(decision_records) == 1


async def test_guardrail_block_logged(tmp_path):
    """Guardrail failure should produce event with skip_stage=GUARDRAIL."""
    from kindshot.models import DecisionRecord, Action, SizeHint
    mock_decision = DecisionRecord(
        schema_version="0.1.2",
        run_id="test_run",
        event_id="",
        decided_at=datetime(2026, 3, 5, 0, 12, 30, tzinfo=timezone.utc),
        llm_model="test",
        llm_latency_ms=10,
        action=Action.BUY,
        confidence=80,
        size_hint=SizeHint.M,
        reason="test",
        decision_source="LLM",
    )
    raw = _make_raw()
    records = await _run_pipeline_once(
        tmp_path, [raw],
        decision_side_effect=[mock_decision],
        guardrail_passed=False,
    )

    gr_records = [r for r in records if r.get("skip_stage") == "GUARDRAIL"]
    assert len(gr_records) == 1
    assert gr_records[0]["skip_reason"] == "BLOCKED"

    # No decision record should be written when guardrail blocks
    decision_records = [r for r in records if r.get("type") == "decision"]
    assert len(decision_records) == 0


async def test_pipeline_passes_delay_ms_to_guardrails(tmp_path):
    from kindshot.models import DecisionRecord, Action, SizeHint

    mock_decision = DecisionRecord(
        schema_version="0.1.2",
        run_id="test_run",
        event_id="",
        decided_at=datetime(2026, 3, 5, 0, 12, 30, tzinfo=timezone.utc),
        llm_model="test",
        llm_latency_ms=10,
        action=Action.BUY,
        confidence=82,
        size_hint=SizeHint.M,
        reason="test",
        decision_source="LLM",
    )
    raw = _make_raw()
    raw.published = "2026-03-05T09:11:00+09:00"
    raw.detected_at = datetime.fromisoformat("2026-03-05T09:12:30+09:00")

    _records, guardrail_calls = await _run_pipeline_once(
        tmp_path,
        [raw],
        decision_side_effect=[mock_decision],
        capture_guardrail_calls=True,
    )

    assert len(guardrail_calls) == 1
    assert guardrail_calls[0].kwargs["delay_ms"] == 90_000


async def test_pipeline_records_buy_with_sector_metadata(tmp_path):
    from kindshot.models import DecisionRecord, Action, SizeHint
    from kindshot.guardrails import GuardrailState
    from kindshot.config import Config

    mock_decision = DecisionRecord(
        schema_version="0.1.2",
        run_id="test_run",
        event_id="",
        decided_at=datetime.now(timezone.utc),
        llm_model="test",
        llm_latency_ms=10,
        action=Action.BUY,
        confidence=82,
        size_hint=SizeHint.M,
        reason="test",
        decision_source="LLM",
    )
    raw = _make_raw()
    guardrail_state = GuardrailState(Config())
    guardrail_state.record_buy = MagicMock(wraps=guardrail_state.record_buy)

    await _run_pipeline_once(
        tmp_path,
        [raw],
        decision_side_effect=[mock_decision],
        paper=True,
        ctx_raw=ContextCardData(
            adv_value_20d=10e9,
            spread_bps=10.0,
            ret_today=1.0,
            sector="반도체",
        ),
        guardrail_state=guardrail_state,
    )

    guardrail_state.record_buy.assert_called_once_with("005930", sector="반도체")


async def test_pipeline_logs_news_signal_enrichment(tmp_path):
    from kindshot.models import DecisionRecord, Action, SizeHint

    mock_decision = DecisionRecord(
        schema_version="0.1.2",
        run_id="test_run",
        event_id="",
        decided_at=datetime.now(timezone.utc),
        llm_model="test",
        llm_latency_ms=10,
        action=Action.BUY,
        confidence=84,
        size_hint=SizeHint.M,
        reason="test",
        decision_source="LLM",
    )
    raw = _make_raw(title="삼성전자(005930) - 8237억 규모 공급계약 체결")
    records = await _run_pipeline_once(
        tmp_path,
        [raw],
        decision_side_effect=[mock_decision],
        paper=True,
    )

    event_records = [r for r in records if r.get("type") == "event"]
    assert len(event_records) == 1
    event = event_records[0]
    assert event["news_category"] == "contract"
    assert event["analysis_headline"].endswith("8237억 규모 공급계약 체결")
    assert event["news_signal"]["contract_amount_eok"] == 8237
    assert event["news_signal"]["impact_score"] >= 70


async def test_pipeline_loop_prioritizes_rising_sector_ticker(tmp_path):
    from kindshot.event_registry import EventRegistry
    from kindshot.feed import KindFeed
    from kindshot.logger import JsonlLogger
    from kindshot.market import MarketMonitor
    from kindshot.pipeline import pipeline_loop
    from kindshot.price import PriceFetcher, SnapshotScheduler

    cfg = Config(
        log_dir=tmp_path / "logs",
        dry_run=True,
        alpha_scanner_api_base_url="http://alpha.local",
        pipeline_workers=1,
    )
    log = JsonlLogger(cfg.log_dir, run_id="test_run")
    registry = EventRegistry()
    market = MarketMonitor(cfg)
    market._initialized = True
    market._halted = False
    scheduler = SnapshotScheduler(cfg, PriceFetcher(kis=None), log)
    mock_feed = AsyncMock(spec=KindFeed)

    raw_lagging = _make_raw(
        title="종목A(000660) - 공급계약 체결",
        link="https://kind.krx.co.kr/?rcpNo=20260305000002",
    )
    raw_lagging.ticker = "000660"
    raw_lagging.corp_name = "종목A"
    raw_rising = _make_raw(
        title="종목B(005930) - 공급계약 체결",
        link="https://kind.krx.co.kr/?rcpNo=20260305000003",
    )
    raw_rising.ticker = "005930"
    raw_rising.corp_name = "종목B"

    async def _one_batch():
        yield [raw_lagging, raw_rising]

    mock_feed.stream = _one_batch
    processed_order: list[str] = []

    async def _capture_order(*, raw, **kwargs):
        processed_order.append(raw.ticker)

    snapshot = {
        "status": "ok",
        "prioritized_stocks": [
            {
                "ticker": "005930",
                "sector_rotation_signal": "LEADING",
                "sector_momentum_score": 82.0,
                "priority_score": 81.7,
            },
            {
                "ticker": "000660",
                "sector_rotation_signal": "LAGGING",
                "sector_momentum_score": 22.0,
                "priority_score": 39.0,
            },
        ],
    }

    with patch("kindshot.pipeline.fetch_alpha_scanner_sector_snapshot", new=AsyncMock(return_value=snapshot)), \
         patch("kindshot.pipeline.process_registered_event", new=AsyncMock(side_effect=_capture_order)):
        await asyncio.wait_for(
            pipeline_loop(
                mock_feed,
                registry,
                MagicMock(),
                market,
                scheduler,
                log,
                cfg,
                "test_run",
                None,
                mode="paper",
            ),
            timeout=1.0,
        )

    assert processed_order == ["005930", "000660"]


async def test_neg_strong_requests_news_exit_for_open_ticker(tmp_path):
    from kindshot.event_registry import ProcessedEvent
    from kindshot.logger import JsonlLogger
    from kindshot.models import EventIdMethod, EventKind, MarketContext
    from kindshot.pipeline import RuntimeCounters, execute_bucket_path

    raw = _make_raw(title="삼성전자(005930) - 대규모 손상차손 및 실적 쇼크")
    processed = ProcessedEvent(
        event_id="evt_neg",
        event_id_method=EventIdMethod.UID,
        event_kind=EventKind.ORIGINAL,
        parent_id=None,
        event_group_id="evt_neg",
        parent_match_method=None,
        parent_match_score=None,
        parent_candidate_count=None,
        kind_uid=None,
        raw=raw,
    )
    cfg = Config(log_dir=tmp_path / "logs", paper=True)
    log = JsonlLogger(cfg.log_dir, run_id="test_run")
    scheduler = MagicMock()
    scheduler.has_open_position.return_value = True
    scheduler.force_exit_ticker = AsyncMock(return_value=1)
    market = MagicMock()
    market.snapshot = MarketContext()

    outcome = await execute_bucket_path(
        raw=raw,
        processed=processed,
        bucket=Bucket.NEG_STRONG,
        keyword_hits=[],
        decision_engine=MagicMock(),
        market=market,
        scheduler=scheduler,
        log=log,
        config=cfg,
        run_id="test_run",
        kis=None,
        counters=RuntimeCounters(),
        mode="paper",
        guardrail_state=None,
        feed_source="KIS",
    )

    scheduler.force_exit_ticker.assert_awaited_once_with(
        "005930",
        exit_type="news_exit",
        horizon="news",
    )
    assert outcome.skip_reason == "NEG_BUCKET"


async def test_pos_weak_disabled_skips_before_decision_path(tmp_path):
    from kindshot.event_registry import ProcessedEvent
    from kindshot.logger import JsonlLogger
    from kindshot.models import EventIdMethod, EventKind, MarketContext
    from kindshot.pipeline import RuntimeCounters, execute_bucket_path

    raw = _make_raw(title="에이비엘바이오, AACR서 이중항체 ADC 2종 공개…美 임상 1상 추진")
    processed = ProcessedEvent(
        event_id="evt_pos_weak_disabled",
        event_id_method=EventIdMethod.UID,
        event_kind=EventKind.ORIGINAL,
        parent_id=None,
        event_group_id="evt_pos_weak_disabled",
        parent_match_method=None,
        parent_match_score=None,
        parent_candidate_count=None,
        kind_uid=None,
        raw=raw,
    )
    cfg = Config(log_dir=tmp_path / "logs", paper=True)
    log = JsonlLogger(cfg.log_dir, run_id="test_run")
    scheduler = MagicMock()
    market = MagicMock()
    market.snapshot = MarketContext()
    decision_engine = MagicMock()
    decision_engine.decide = AsyncMock()

    outcome = await execute_bucket_path(
        raw=raw,
        processed=processed,
        bucket=Bucket.POS_WEAK,
        keyword_hits=["임상 1상"],
        decision_engine=decision_engine,
        market=market,
        scheduler=scheduler,
        log=log,
        config=cfg,
        run_id="test_run",
        kis=None,
        counters=RuntimeCounters(),
        mode="paper",
        guardrail_state=None,
        feed_source="KIS",
    )

    decision_engine.decide.assert_not_awaited()
    scheduler.schedule_t0.assert_not_called()
    assert outcome.skip_stage == SkipStage.BUCKET
    assert outcome.skip_reason == "NEWS_WEAK_DISABLED"

    records = []
    for file in cfg.log_dir.glob("*.jsonl"):
        for line in file.read_text(encoding="utf-8").splitlines():
            if line:
                records.append(json.loads(line))
    event_record = next(record for record in records if record.get("type") == "event")
    assert event_record["bucket"] == "POS_WEAK"
    assert event_record["skip_stage"] == "BUCKET"
    assert event_record["skip_reason"] == "NEWS_WEAK_DISABLED"


async def test_pipeline_passes_time_and_hold_profile_to_guardrails(tmp_path):
    from kindshot.models import DecisionRecord, Action, SizeHint

    detected_at = datetime(2026, 3, 24, 4, 10, 0, tzinfo=timezone.utc)
    decided_at = datetime(2026, 3, 24, 4, 10, 5, tzinfo=timezone.utc)
    raw = RawDisclosure(
        title="삼성전자(005930) - 공급계약 체결",
        link="https://kind.krx.co.kr/?rcpNo=20260305000001",
        rss_guid="guid1",
        published="2026-03-24T13:10:00+09:00",
        ticker="005930",
        corp_name="삼성전자",
        detected_at=detected_at,
    )
    mock_decision = DecisionRecord(
        schema_version="0.1.2",
        run_id="test_run",
        event_id="",
        decided_at=decided_at,
        llm_model="test",
        llm_latency_ms=10,
        action=Action.BUY,
        confidence=82,
        size_hint=SizeHint.M,
        reason="test",
        decision_source="LLM",
    )

    _records, guardrail_calls = await _run_pipeline_once(
        tmp_path,
        [raw],
        decision_side_effect=[mock_decision],
        paper=True,
        capture_guardrail_calls=True,
    )

    assert len(guardrail_calls) == 1
    kwargs = guardrail_calls[0].kwargs
    assert kwargs["decision_time_kst"] == decided_at
    assert kwargs["decision_hold_minutes"] == 30


async def test_pipeline_passes_supportive_dynamic_guardrail_profile(tmp_path):
    from kindshot.models import DecisionRecord, Action, SizeHint

    detected_at = datetime(2026, 3, 24, 4, 50, 0, tzinfo=timezone.utc)
    decided_at = datetime(2026, 3, 24, 4, 50, 5, tzinfo=timezone.utc)
    raw = RawDisclosure(
        title="삼성전자(005930) - 공급계약 체결",
        link="https://kind.krx.co.kr/?rcpNo=20260305000001",
        rss_guid="guid1",
        published="2026-03-24T13:50:00+09:00",
        ticker="005930",
        corp_name="삼성전자",
        detected_at=detected_at,
    )
    mock_decision = DecisionRecord(
        schema_version="0.1.2",
        run_id="test_run",
        event_id="",
        decided_at=decided_at,
        llm_model="test",
        llm_latency_ms=10,
        action=Action.BUY,
        confidence=77,
        size_hint=SizeHint.M,
        reason="test",
        decision_source="LLM",
    )

    _records, guardrail_calls = await _run_pipeline_once(
        tmp_path,
        [raw],
        decision_side_effect=[mock_decision],
        paper=True,
        config_overrides={"dynamic_fast_profile_extension_minutes": 60},
        market_snapshot_overrides={
            "kospi_change": 0.4,
            "kosdaq_change": 0.7,
            "kospi_breadth_ratio": 0.56,
            "kosdaq_breadth_ratio": 0.61,
        },
        capture_guardrail_calls=True,
    )

    profile = guardrail_calls[0].kwargs["dynamic_profile"]
    assert profile.supportive_market is True
    assert profile.min_buy_confidence == 76  # max(71, 78-2)
    assert profile.afternoon_min_confidence == 78  # max(75, 80-2)
    assert (profile.fast_profile_no_buy_after_kst_hour, profile.fast_profile_no_buy_after_kst_minute) == (15, 15)  # min(15:15, 15:30)


async def test_pipeline_skips_fast_profile_late_entry_before_llm(tmp_path):
    raw = RawDisclosure(
        title="삼성전자(005930) - 공급계약 체결",
        link="https://kind.krx.co.kr/?rcpNo=20260305000001",
        rss_guid="guid1",
        published="2026-03-24T14:40:00+09:00",  # v78: cutoff 14:30 이후
        ticker="005930",
        corp_name="삼성전자",
        detected_at=datetime(2026, 3, 24, 5, 40, 0, tzinfo=timezone.utc),
    )

    records, guardrail_calls = await _run_pipeline_once(
        tmp_path,
        [raw],
        paper=True,
        capture_guardrail_calls=True,
    )

    event_records = [r for r in records if r.get("type") == "event"]
    assert len(event_records) == 1
    assert event_records[0]["skip_reason"] == "FAST_PROFILE_LATE_ENTRY"
    assert guardrail_calls == []


async def test_pipeline_passes_normalized_analysis_headline_to_decision(tmp_path):
    from kindshot.event_registry import EventRegistry
    from kindshot.logger import JsonlLogger
    from kindshot.market import MarketMonitor
    from kindshot.models import Action, ContextCard, DecisionRecord, SizeHint
    from kindshot.pipeline import process_registered_event
    from kindshot.price import PriceFetcher, SnapshotScheduler
    from kindshot.guardrails import GuardrailResult

    raw = RawDisclosure(
        title='삼성전자, "250억 규모 공급계약 체결"',
        link="https://kind.krx.co.kr/?rcpNo=20260305000001",
        rss_guid="guid1",
        published="2026-03-24T14:10:00+09:00",
        ticker="005930",
        corp_name="삼성전자",
        detected_at=datetime(2026, 3, 24, 4, 10, 0, tzinfo=timezone.utc),
    )
    mock_decision = DecisionRecord(
        schema_version="0.1.2",
        run_id="test_run",
        event_id="",
        decided_at=datetime(2026, 3, 24, 4, 10, 5, tzinfo=timezone.utc),
        llm_model="test",
        llm_latency_ms=10,
        action=Action.SKIP,
        confidence=60,
        size_hint=SizeHint.S,
        reason="test",
        decision_source="LLM",
    )

    cfg = Config(log_dir=tmp_path / "logs", paper=True)
    log = JsonlLogger(cfg.log_dir, run_id="test_run")
    registry = EventRegistry()
    processed = registry.process(raw)
    assert processed is not None
    market = MarketMonitor(cfg)
    market._initialized = True
    market._halted = False
    fetcher = PriceFetcher(kis=None)
    scheduler = SnapshotScheduler(cfg, fetcher, log)

    mock_engine = MagicMock()
    mock_engine.decide = AsyncMock(return_value=mock_decision)
    mock_engine.fallback_decide = MagicMock()

    with patch("kindshot.pipeline.build_context_card", new_callable=AsyncMock) as mock_ctx, \
         patch("kindshot.pipeline.check_guardrails") as mock_gr, \
         patch("kindshot.pipeline.classify", wraps=__import__("kindshot.pipeline", fromlist=["classify"]).classify) as mock_classify, \
         patch("kindshot.pipeline.normalize_analysis_headline", return_value="삼성전자, 250억 규모 공급계약 체결"):
        mock_ctx.return_value = (
            ContextCard(adv_value_20d=10e9, spread_bps=10.0),
            ContextCardData(adv_value_20d=10e9, spread_bps=10.0, ret_today=1.0),
        )
        mock_gr.return_value = GuardrailResult(passed=True)

        await process_registered_event(
            raw,
            processed,
            mock_engine,
            market,
            scheduler,
            log,
            cfg,
            "test_run",
            None,   # kis
            None,   # counters
            mode="paper",
            feed_source="KIS",
        )

    assert mock_classify.call_args.args[0] == raw.title
    kwargs = mock_engine.decide.await_args.kwargs
    assert kwargs["headline"] == raw.title
    assert kwargs["analysis_headline"] == "삼성전자, 250억 규모 공급계약 체결"
    assert kwargs["dorg"] == ""


def test_runtime_counters_helpers():
    """Runtime counter helpers should aggregate skip stats consistently."""
    from kindshot.pipeline import RuntimeCounters, counter_snapshot, _mark_skip

    counters = RuntimeCounters()
    _mark_skip(counters, stage="QUANT", reason="RET_TODAY_DATA_MISSING")
    _mark_skip(counters, stage="LLM_ERROR", reason="LLM_ERROR")

    snap = counter_snapshot(counters)
    assert snap["totals"]["events_skipped"] == 2
    assert snap["skip_stage"]["QUANT"] == 1
    assert snap["skip_stage"]["LLM_ERROR"] == 1
    assert snap["skip_reason"]["RET_TODAY_DATA_MISSING"] == 1
    assert snap["skip_reason"]["LLM_ERROR"] == 1


def test_make_error_event_record():
    """_make_error_event_record builds a well-formed EventRecord for LLM errors."""
    from kindshot.pipeline import _make_error_event_record
    from kindshot.models import SkipStage, Bucket, EventIdMethod, EventKind

    raw = RawDisclosure(
        title="테스트 뉴스", link="http://test.com", rss_guid="guid1",
        published="2026-03-24T09:00:00", ticker="005930", corp_name="삼성전자",
        detected_at=datetime.now(timezone.utc),
    )
    processed = MagicMock()
    processed.event_id = "evt_test"
    processed.event_id_method = EventIdMethod.UID
    processed.event_kind = EventKind.ORIGINAL
    processed.parent_id = None
    processed.event_group_id = "grp_test"
    processed.parent_match_method = None
    processed.parent_match_score = None
    processed.parent_candidate_count = None
    processed.kind_uid = "uid123"

    bucket_result = MagicMock()
    bucket_result.bucket = Bucket.POS_STRONG
    bucket_result.keyword_hits = ["공급계약"]

    cfg = Config(anthropic_api_key="test")
    rec = _make_error_event_record(
        mode="paper", config=cfg, run_id="run1", processed=processed,
        raw=raw, detected_at=raw.detected_at, feed_source="KIS",
        bucket_result=bucket_result, skip_stage=SkipStage.LLM_TIMEOUT,
        skip_reason="LLM_TIMEOUT", market_snapshot=None,
    )
    assert rec.event_id == "evt_test"
    assert rec.skip_stage == SkipStage.LLM_TIMEOUT
    assert rec.skip_reason == "LLM_TIMEOUT"
    assert rec.bucket == Bucket.POS_STRONG
    assert rec.disclosed_at is None
    assert rec.disclosed_at_missing is True
    assert rec.ticker == "005930"


async def test_paper_mode_logs_decision_with_paper_mode(tmp_path):
    """Paper mode should log event+decision with mode='paper' and no order execution."""
    from kindshot.models import DecisionRecord, Action, SizeHint
    mock_decision = DecisionRecord(
        schema_version="0.1.2",
        run_id="test_run",
        event_id="",
        decided_at=datetime.now(timezone.utc),
        llm_model="test",
        llm_latency_ms=10,
        action=Action.BUY,
        confidence=80,
        size_hint=SizeHint.M,
        reason="test",
        decision_source="LLM",
    )
    raw = _make_raw()
    records = await _run_pipeline_once(
        tmp_path, [raw],
        decision_side_effect=[mock_decision],
        paper=True,
    )

    event_records = [r for r in records if r.get("type") == "event" and r.get("skip_stage") is None]
    decision_records = [r for r in records if r.get("type") == "decision"]

    assert len(event_records) == 1
    assert event_records[0]["mode"] == "paper"
    assert len(decision_records) == 1
    assert decision_records[0]["mode"] == "paper"


async def test_pipeline_logs_latency_profile_and_cache_layer(tmp_path):
    from kindshot.models import DecisionRecord, Action, SizeHint

    mock_decision = DecisionRecord(
        schema_version="0.1.2",
        run_id="test_run",
        event_id="",
        decided_at=datetime.now(timezone.utc),
        llm_model="test",
        llm_latency_ms=0,
        action=Action.BUY,
        confidence=80,
        size_hint=SizeHint.M,
        reason="cached decision",
        decision_source="CACHE",
        cache_layer="disk",
    )
    raw = _make_raw()
    records = await _run_pipeline_once(
        tmp_path,
        [raw],
        decision_side_effect=[mock_decision],
        paper=True,
    )

    event_records = [r for r in records if r.get("type") == "event" and r.get("skip_stage") is None]

    assert len(event_records) == 1
    assert event_records[0]["decision_source"] == "CACHE"
    assert event_records[0]["decision_cache_layer"] == "disk"
    assert event_records[0]["pipeline_profile"]["llm_cache_layer"] == "disk"
    assert event_records[0]["pipeline_profile"]["pipeline_total_ms"] >= 0


async def test_recent_pattern_profile_boosts_matching_buy_confidence(tmp_path):
    from kindshot.models import Action, DecisionRecord, SizeHint
    from kindshot.pattern_profile import PatternCohort, RecentPatternProfile

    raw = RawDisclosure(
        title="삼성전자 FDA 품목허가 승인",
        link="https://kind.krx.co.kr/?rcpNo=20260305000001",
        rss_guid="guid1",
        published="2026-03-24T11:10:00+09:00",
        ticker="005930",
        corp_name="삼성전자",
        detected_at=datetime(2026, 3, 24, 2, 10, 0, tzinfo=timezone.utc),
    )
    decision = DecisionRecord(
        schema_version="0.1.2",
        run_id="test_run",
        event_id="",
        decided_at=datetime(2026, 3, 24, 2, 10, 5, tzinfo=timezone.utc),
        llm_model="test",
        llm_latency_ms=10,
        action=Action.BUY,
        confidence=80,
        size_hint=SizeHint.M,
        reason="test",
        decision_source="LLM",
    )
    profile = RecentPatternProfile(
        enabled=True,
        analysis_dates=("20260320", "20260327"),
        total_trades=6,
        boost_patterns=(
            PatternCohort(
                pattern_type="news_type_hour_bucket",
                key="clinical_regulatory|midday",
                news_type="clinical_regulatory",
                ticker=None,
                hour_bucket="midday",
                count=2,
                wins=2,
                losses=0,
                win_rate=1.0,
                avg_pnl_pct=0.4,
                total_pnl_pct=0.8,
                confidence_delta=3,
            ),
        ),
        loss_guardrail_patterns=(),
    )

    records = await _run_pipeline_once(
        tmp_path,
        [raw],
        decision_side_effect=[decision],
        paper=True,
        ctx_raw=ContextCardData(adv_value_20d=10e9, spread_bps=10.0, ret_today=1.0),
        recent_pattern_profile=profile,
    )

    decision_records = [r for r in records if r.get("type") == "decision"]
    assert len(decision_records) == 1
    assert decision_records[0]["confidence"] == 91  # v71: clinical 0(was+2), 11시 +3(was+2) → net -1


async def test_recent_pattern_profile_blocks_matching_loss_cohort(tmp_path):
    from kindshot.models import Action, DecisionRecord, SizeHint
    from kindshot.pattern_profile import PatternCohort, RecentPatternProfile

    raw = RawDisclosure(
        title="테스트 공급계약 체결",
        link="https://kind.krx.co.kr/?rcpNo=20260305000001",
        rss_guid="guid1",
        published="2026-03-24T09:10:00+09:00",
        ticker="005930",
        corp_name="삼성전자",
        detected_at=datetime(2026, 3, 24, 0, 10, 0, tzinfo=timezone.utc),
    )
    decision = DecisionRecord(
        schema_version="0.1.2",
        run_id="test_run",
        event_id="",
        decided_at=datetime(2026, 3, 24, 0, 10, 5, tzinfo=timezone.utc),
        llm_model="test",
        llm_latency_ms=10,
        action=Action.BUY,
        confidence=82,
        size_hint=SizeHint.M,
        reason="test",
        decision_source="LLM",
    )
    profile = RecentPatternProfile(
        enabled=True,
        analysis_dates=("20260320", "20260327"),
        total_trades=6,
        boost_patterns=(),
        loss_guardrail_patterns=(
            PatternCohort(
                pattern_type="news_type_hour_bucket",
                key="contract|open",
                news_type="contract",
                ticker=None,
                hour_bucket="open",
                count=2,
                wins=0,
                losses=2,
                win_rate=0.0,
                avg_pnl_pct=-0.68,
                total_pnl_pct=-1.36,
                guardrail_reason="PATTERN_LOSS_GUARDRAIL",
            ),
        ),
    )

    records = await _run_pipeline_once(
        tmp_path,
        [raw],
        decision_side_effect=[decision],
        paper=True,
        recent_pattern_profile=profile,
    )

    event_records = [r for r in records if r.get("type") == "event"]
    assert len(event_records) == 1
    assert event_records[0]["skip_stage"] == "GUARDRAIL"
    assert event_records[0]["skip_reason"] == "PATTERN_LOSS_GUARDRAIL"
    assert [r for r in records if r.get("type") == "decision"] == []


async def test_unknown_shadow_review_writes_inbox_and_enqueues_request(tmp_path):
    from kindshot.event_registry import EventRegistry
    from kindshot.feed import RawDisclosure
    from kindshot.logger import JsonlLogger
    from kindshot.pipeline import process_registered_event
    from kindshot.market import MarketMonitor
    from kindshot.price import PriceFetcher, SnapshotScheduler

    cfg = Config(
        log_dir=tmp_path / "logs",
        unknown_shadow_review_enabled=True,
        unknown_inbox_dir=tmp_path / "logs" / "unknown_inbox",
        unknown_review_dir=tmp_path / "logs" / "unknown_review",
    )
    log = JsonlLogger(cfg.log_dir, run_id="test_run")
    market = MarketMonitor(cfg)
    market._initialized = True
    market._halted = False
    scheduler = SnapshotScheduler(cfg, PriceFetcher(kis=None), log)
    registry = EventRegistry()
    raw = RawDisclosure(
        title="삼성전자(005930) - 임원변경",
        link="https://example.com/unknown",
        rss_guid="guid-unknown",
        published="2026-03-05T09:12:04+09:00",
        ticker="005930",
        corp_name="삼성전자",
        detected_at=datetime.now(timezone.utc),
    )
    processed = registry.process(raw)
    queue: asyncio.Queue = asyncio.Queue(maxsize=1)

    await process_registered_event(
        raw=raw,
        processed=processed,
        decision_engine=MagicMock(),
        market=market,
        scheduler=scheduler,
        log=log,
        config=cfg,
        run_id="test_run",
        kis=None,
        counters=None,
        mode="paper",
        feed_source="KIND",
        unknown_review_queue=queue,
    )

    assert queue.qsize() == 1
    inbox_path = cfg.unknown_inbox_dir / f"{raw.detected_at.astimezone(timezone(timedelta(hours=9))).strftime('%Y-%m-%d')}.jsonl"
    assert inbox_path.exists()
    inbox_row = json.loads(inbox_path.read_text(encoding="utf-8").splitlines()[0])
    assert inbox_row["event_id"] == processed.event_id
    assert inbox_row["headline"] == raw.title


async def test_unknown_paper_promotion_logs_promoted_pos_strong_and_decision(tmp_path):
    from kindshot.guardrails import GuardrailResult
    from kindshot.logger import JsonlLogger
    from kindshot.pipeline import process_unknown_promotion
    from kindshot.market import MarketMonitor
    from kindshot.models import Action, ContextCard, DecisionRecord, SizeHint
    from kindshot.price import PriceFetcher, SnapshotScheduler
    from kindshot.unknown_review import UnknownReviewRequest

    cfg = Config(
        log_dir=tmp_path / "logs",
        paper=True,
        unknown_paper_promotion_enabled=True,
        unknown_promotion_dir=tmp_path / "logs" / "unknown_promotion",
        runtime_context_cards_dir=tmp_path / "data" / "runtime" / "context_cards",
        runtime_index_path=tmp_path / "data" / "runtime" / "index.json",
    )
    log = JsonlLogger(cfg.log_dir, run_id="test_run")
    market = MarketMonitor(cfg)
    market._initialized = True
    market._halted = False
    scheduler = SnapshotScheduler(cfg, PriceFetcher(kis=None), log)
    promotion_detected_at = datetime(2026, 3, 24, 4, 10, 0, tzinfo=timezone.utc)

    decision_engine = MagicMock()
    decision_engine.decide = AsyncMock(
        return_value=DecisionRecord(
            schema_version="0.1.2",
            run_id="test_run",
            event_id="",
            decided_at=promotion_detected_at + timedelta(seconds=5),
            llm_model="test",
            llm_latency_ms=10,
            action=Action.BUY,
            confidence=88,
            size_hint=SizeHint.M,
            reason="promoted test",
            decision_source="LLM",
        )
    )
    request = UnknownReviewRequest(
        event_id="evt_unknown",
        detected_at=promotion_detected_at,
        runtime_mode="paper",
        ticker="005930",
        corp_name="삼성전자",
        headline="삼성전자, 대형 공급 계약 확대",
        rss_link="https://example.com/promoted",
        rss_guid="guid-promoted",
        published="2026-03-05T09:12:04+09:00",
        source="KIND",
    )
    review = UnknownReviewRecord(
        event_id=request.event_id,
        reviewed_at=promotion_detected_at + timedelta(minutes=1),
        runtime_mode="paper",
        headline_only=True,
        review_status=ReviewStatus.OK,
        suggested_bucket=Bucket.POS_STRONG,
        confidence=91,
        promote_now=True,
        needs_article_body=False,
    )

    with patch("kindshot.pipeline.build_context_card", new_callable=AsyncMock) as mock_ctx, \
         patch("kindshot.pipeline.check_guardrails") as mock_gr:
        mock_ctx.return_value = (
            ContextCard(adv_value_20d=10e9, spread_bps=10.0),
            ContextCardData(adv_value_20d=10e9, spread_bps=10.0, ret_today=5.0),
        )
        mock_gr.return_value = GuardrailResult(passed=True)
        await process_unknown_promotion(
            request=request,
            review=review,
            decision_engine=decision_engine,
            market=market,
            scheduler=scheduler,
            log=log,
            config=cfg,
            run_id="test_run",
            kis=None,
            counters=None,
            guardrail_state=None,
        )

    records = []
    for f in cfg.log_dir.glob("*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line:
                records.append(json.loads(line))
    event_records = [r for r in records if r.get("type") == "event"]
    decision_records = [r for r in records if r.get("type") == "decision"]
    assert len(event_records) == 1
    assert len(decision_records) == 1
    assert event_records[0]["promotion_original_event_id"] == "evt_unknown"
    assert event_records[0]["bucket"] == "POS_STRONG"
    assert decision_records[0]["event_id"] == event_records[0]["event_id"]

    promotion_path = cfg.unknown_promotion_dir / f"{request.detected_at.astimezone(timezone(timedelta(hours=9))).strftime('%Y-%m-%d')}.jsonl"
    promotion_row = json.loads(promotion_path.read_text(encoding="utf-8").splitlines()[0])
    assert promotion_row["promotion_status"] == "PROMOTED"
    assert promotion_row["decision_action"] == "BUY"
    assert promotion_row["derived_event_id"] == event_records[0]["event_id"]

    context_path = cfg.runtime_context_cards_dir / f"{request.detected_at.astimezone(timezone(timedelta(hours=9))).strftime('%Y%m%d')}.jsonl"
    context_row = json.loads(context_path.read_text(encoding="utf-8").splitlines()[0])
    assert context_row["promotion_original_event_id"] == "evt_unknown"
    assert context_row["promotion_confidence"] == 91


async def test_unknown_paper_promotion_logs_promoted_neg_strong_and_tracks_price(tmp_path):
    from kindshot.logger import JsonlLogger
    from kindshot.pipeline import process_unknown_promotion
    from kindshot.market import MarketMonitor
    from kindshot.price import PriceFetcher, SnapshotScheduler
    from kindshot.unknown_review import UnknownReviewRequest

    cfg = Config(
        log_dir=tmp_path / "logs",
        paper=True,
        unknown_paper_promotion_enabled=True,
        unknown_promotion_dir=tmp_path / "logs" / "unknown_promotion",
    )
    log = JsonlLogger(cfg.log_dir, run_id="test_run")
    market = MarketMonitor(cfg)
    market._initialized = True
    market._halted = False
    scheduler = SnapshotScheduler(cfg, PriceFetcher(kis=None), log)

    request = UnknownReviewRequest(
        event_id="evt_unknown_neg",
        detected_at=datetime.now(timezone.utc),
        runtime_mode="paper",
        ticker="005930",
        corp_name="삼성전자",
        headline="삼성전자, 공급 계약 해지",
        rss_link="https://example.com/neg",
        rss_guid="guid-neg",
        published="2026-03-05T09:12:04+09:00",
        source="KIND",
    )
    review = UnknownReviewRecord(
        event_id=request.event_id,
        reviewed_at=datetime.now(timezone.utc),
        runtime_mode="paper",
        headline_only=True,
        review_status=ReviewStatus.OK,
        suggested_bucket=Bucket.NEG_STRONG,
        confidence=90,
        promote_now=True,
        needs_article_body=False,
    )

    await process_unknown_promotion(
        request=request,
        review=review,
        decision_engine=MagicMock(),
        market=market,
        scheduler=scheduler,
        log=log,
        config=cfg,
        run_id="test_run",
        kis=None,
        counters=None,
        guardrail_state=None,
    )

    records = []
    for f in cfg.log_dir.glob("*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line:
                records.append(json.loads(line))
    event_records = [r for r in records if r.get("type") == "event"]
    decision_records = [r for r in records if r.get("type") == "decision"]
    assert len(event_records) == 1
    assert event_records[0]["bucket"] == "NEG_STRONG"
    assert event_records[0]["skip_reason"] == "NEG_BUCKET"
    assert decision_records == []
    assert any(s.event_id == event_records[0]["event_id"] for s in scheduler._heap)

    promotion_path = cfg.unknown_promotion_dir / f"{request.detected_at.astimezone(timezone(timedelta(hours=9))).strftime('%Y-%m-%d')}.jsonl"
    promotion_row = json.loads(promotion_path.read_text(encoding="utf-8").splitlines()[0])
    assert promotion_row["promotion_status"] == "PROMOTED"
    assert promotion_row["skip_reason"] == "NEG_BUCKET"


async def test_pipeline_passes_quote_risk_state_to_guardrails(tmp_path):
    from kindshot.models import DecisionRecord, Action, SizeHint

    mock_decision = DecisionRecord(
        schema_version="0.1.2",
        run_id="test_run",
        event_id="",
        decided_at=datetime.now(timezone.utc),
        llm_model="test",
        llm_latency_ms=10,
        action=Action.BUY,
        confidence=80,
        size_hint=SizeHint.M,
        reason="test",
        decision_source="LLM",
    )
    raw = _make_raw()
    risk_state = QuoteRiskState(temp_stop_yn="Y", vi_cls_code="D")

    from kindshot.event_registry import EventRegistry
    from kindshot.logger import JsonlLogger
    from kindshot.market import MarketMonitor
    from kindshot.price import PriceFetcher, SnapshotScheduler
    from kindshot.pipeline import pipeline_loop
    from kindshot.feed import KindFeed
    from kindshot.guardrails import GuardrailResult
    from kindshot.models import ContextCard

    cfg = Config(log_dir=tmp_path / "logs", paper=True)
    log = JsonlLogger(cfg.log_dir, run_id="test_run")
    registry = EventRegistry()
    market = MarketMonitor(cfg)
    market._initialized = True
    market._halted = False
    scheduler = SnapshotScheduler(cfg, PriceFetcher(kis=None), log)

    mock_engine = MagicMock()
    mock_engine.decide = AsyncMock(return_value=mock_decision)

    mock_feed = AsyncMock(spec=KindFeed)

    async def _one_batch():
        yield [raw]
    mock_feed.stream = _one_batch

    with patch("kindshot.pipeline.build_context_card", new_callable=AsyncMock) as mock_ctx, \
         patch("kindshot.pipeline.check_guardrails") as mock_gr:
        mock_ctx.return_value = (
            ContextCard(adv_value_20d=10e9, spread_bps=10.0),
            ContextCardData(adv_value_20d=10e9, spread_bps=10.0, ret_today=5.0, quote_risk_state=risk_state),
        )
        mock_gr.return_value = GuardrailResult(passed=True)

        await asyncio.wait_for(
            pipeline_loop(mock_feed, registry, mock_engine, market, scheduler, log, cfg, "test_run", None, mode="paper"),
            timeout=2.0,
        )

    assert mock_gr.call_args is not None
    assert mock_gr.call_args.kwargs["quote_risk_state"] == risk_state


async def test_pipeline_passes_orderbook_snapshot_and_action_to_guardrails(tmp_path):
    from kindshot.models import DecisionRecord, Action, SizeHint

    mock_decision = DecisionRecord(
        schema_version="0.1.2",
        run_id="test_run",
        event_id="",
        decided_at=datetime.now(timezone.utc),
        llm_model="test",
        llm_latency_ms=10,
        action=Action.BUY,
        confidence=80,
        size_hint=SizeHint.M,
        reason="test",
        decision_source="LLM",
    )
    raw = _make_raw()
    orderbook = OrderbookSnapshot(
        ask_price1=50_100.0,
        bid_price1=49_900.0,
        ask_size1=90,
        bid_size1=120,
        total_ask_size=2000,
        total_bid_size=2400,
        spread_bps=40.0,
    )

    from kindshot.event_registry import EventRegistry
    from kindshot.logger import JsonlLogger
    from kindshot.market import MarketMonitor
    from kindshot.price import PriceFetcher, SnapshotScheduler
    from kindshot.pipeline import pipeline_loop
    from kindshot.feed import KindFeed
    from kindshot.guardrails import GuardrailResult
    from kindshot.models import ContextCard

    cfg = Config(log_dir=tmp_path / "logs", paper=True)
    log = JsonlLogger(cfg.log_dir, run_id="test_run")
    registry = EventRegistry()
    market = MarketMonitor(cfg)
    market._initialized = True
    market._halted = False
    scheduler = SnapshotScheduler(cfg, PriceFetcher(kis=None), log)

    mock_engine = MagicMock()
    mock_engine.decide = AsyncMock(return_value=mock_decision)

    mock_feed = AsyncMock(spec=KindFeed)

    async def _one_batch():
        yield [raw]
    mock_feed.stream = _one_batch

    with patch("kindshot.pipeline.build_context_card", new_callable=AsyncMock) as mock_ctx, \
         patch("kindshot.pipeline.check_guardrails") as mock_gr:
        mock_ctx.return_value = (
            ContextCard(adv_value_20d=10e9, spread_bps=10.0),
            ContextCardData(adv_value_20d=10e9, spread_bps=10.0, ret_today=5.0, orderbook_snapshot=orderbook),
        )
        mock_gr.return_value = GuardrailResult(passed=True)

        await asyncio.wait_for(
            pipeline_loop(mock_feed, registry, mock_engine, market, scheduler, log, cfg, "test_run", None, mode="paper"),
            timeout=2.0,
        )

    assert mock_gr.call_args is not None
    assert mock_gr.call_args.kwargs["orderbook_snapshot"] == orderbook
    assert mock_gr.call_args.kwargs["decision_action"] == Action.BUY


async def test_pipeline_passes_intraday_value_ratio_to_guardrails(tmp_path):
    from kindshot.models import DecisionRecord, Action, SizeHint

    mock_decision = DecisionRecord(
        schema_version="0.1.2",
        run_id="test_run",
        event_id="",
        decided_at=datetime.now(timezone.utc),
        llm_model="test",
        llm_latency_ms=10,
        action=Action.BUY,
        confidence=80,
        size_hint=SizeHint.M,
        reason="test",
        decision_source="LLM",
    )
    raw = _make_raw()

    from kindshot.event_registry import EventRegistry
    from kindshot.logger import JsonlLogger
    from kindshot.market import MarketMonitor
    from kindshot.price import PriceFetcher, SnapshotScheduler
    from kindshot.pipeline import pipeline_loop
    from kindshot.feed import KindFeed
    from kindshot.guardrails import GuardrailResult
    from kindshot.models import ContextCard

    cfg = Config(log_dir=tmp_path / "logs", paper=True)
    log = JsonlLogger(cfg.log_dir, run_id="test_run")
    registry = EventRegistry()
    market = MarketMonitor(cfg)
    market._initialized = True
    market._halted = False
    scheduler = SnapshotScheduler(cfg, PriceFetcher(kis=None), log)

    mock_engine = MagicMock()
    mock_engine.decide = AsyncMock(return_value=mock_decision)

    mock_feed = AsyncMock(spec=KindFeed)

    async def _one_batch():
        yield [raw]
    mock_feed.stream = _one_batch

    with patch("kindshot.pipeline.build_context_card", new_callable=AsyncMock) as mock_ctx, \
         patch("kindshot.pipeline.check_guardrails") as mock_gr:
        mock_ctx.return_value = (
            ContextCard(adv_value_20d=10e9, spread_bps=10.0, intraday_value_vs_adv20d=0.005),
            ContextCardData(adv_value_20d=10e9, spread_bps=10.0, ret_today=5.0, intraday_value_vs_adv20d=0.005),
        )
        mock_gr.return_value = GuardrailResult(passed=True)

        await asyncio.wait_for(
            pipeline_loop(mock_feed, registry, mock_engine, market, scheduler, log, cfg, "test_run", None, mode="paper"),
            timeout=2.0,
        )

    assert mock_gr.call_args is not None
    assert mock_gr.call_args.kwargs["intraday_value_vs_adv20d"] == 0.005


async def test_pipeline_passes_effective_delay_and_prior_volume_to_guardrails(tmp_path):
    from kindshot.models import DecisionRecord, Action, SizeHint

    mock_decision = DecisionRecord(
        schema_version="0.1.2",
        run_id="test_run",
        event_id="",
        decided_at=datetime(2026, 3, 27, 1, 6, 15, tzinfo=timezone.utc),
        llm_model="test",
        llm_latency_ms=10,
        action=Action.BUY,
        confidence=80,
        size_hint=SizeHint.M,
        reason="test",
        decision_source="LLM",
    )
    raw = _make_raw()
    raw = raw.__class__(
        title=raw.title,
        link=raw.link,
        rss_guid=raw.rss_guid,
        published="2026-03-27T10:05:10+09:00",
        ticker=raw.ticker,
        corp_name=raw.corp_name,
        detected_at=datetime(2026, 3, 27, 10, 5, 30, tzinfo=timezone(timedelta(hours=9))),
        dorg=raw.dorg,
    )

    from kindshot.event_registry import EventRegistry
    from kindshot.logger import JsonlLogger
    from kindshot.market import MarketMonitor
    from kindshot.price import PriceFetcher, SnapshotScheduler
    from kindshot.pipeline import pipeline_loop
    from kindshot.feed import KindFeed
    from kindshot.guardrails import GuardrailResult
    from kindshot.models import ContextCard

    cfg = Config(log_dir=tmp_path / "logs", paper=True)
    log = JsonlLogger(cfg.log_dir, run_id="test_run")
    registry = EventRegistry()
    market = MarketMonitor(cfg)
    market._initialized = True
    market._halted = False
    scheduler = SnapshotScheduler(cfg, PriceFetcher(kis=None), log)

    mock_engine = MagicMock()
    mock_engine.decide = AsyncMock(return_value=mock_decision)

    mock_feed = AsyncMock(spec=KindFeed)

    async def _one_batch():
        yield [raw]

    mock_feed.stream = _one_batch

    with patch("kindshot.pipeline.build_context_card", new_callable=AsyncMock) as mock_ctx, \
         patch("kindshot.pipeline.check_guardrails") as mock_gr:
        mock_ctx.return_value = (
            ContextCard(adv_value_20d=10e9, spread_bps=10.0, prior_volume_rate=55.0),
            ContextCardData(
                adv_value_20d=10e9,
                spread_bps=10.0,
                ret_today=1.0,
                prior_volume_rate=55.0,
            ),
        )
        mock_gr.return_value = GuardrailResult(passed=True)

        await asyncio.wait_for(
            pipeline_loop(mock_feed, registry, mock_engine, market, scheduler, log, cfg, "test_run", None, mode="paper"),
            timeout=2.0,
        )

    assert mock_gr.call_args is not None
    assert mock_gr.call_args.kwargs["delay_ms"] == 65_000
    assert mock_gr.call_args.kwargs["prior_volume_rate"] == 55.0
    assert mock_gr.call_args.kwargs["decision_action"] == Action.BUY


async def test_market_breadth_risk_off_blocks_before_llm(tmp_path):
    from kindshot.event_registry import EventRegistry
    from kindshot.logger import JsonlLogger
    from kindshot.market import MarketMonitor
    from kindshot.price import PriceFetcher, SnapshotScheduler
    from kindshot.pipeline import pipeline_loop
    from kindshot.feed import KindFeed

    cfg = Config(log_dir=tmp_path / "logs", paper=True, min_market_breadth_ratio=0.8)
    log = JsonlLogger(cfg.log_dir, run_id="test_run")
    registry = EventRegistry()
    market = MarketMonitor(cfg)
    market._initialized = True
    market._halted = False
    market._kospi_change = -0.2
    market._kosdaq_change = -0.3
    market._kospi_breadth_ratio = 0.5
    market._kosdaq_breadth_ratio = 0.6
    scheduler = SnapshotScheduler(cfg, PriceFetcher(kis=None), log)

    mock_engine = MagicMock()
    mock_engine.decide = AsyncMock()

    mock_feed = AsyncMock(spec=KindFeed)

    async def _one_batch():
        yield [_make_raw()]
    mock_feed.stream = _one_batch

    with patch("kindshot.pipeline.build_context_card", new_callable=AsyncMock) as mock_ctx, \
         patch("kindshot.pipeline.check_guardrails") as mock_gr:
        from kindshot.models import ContextCard
        mock_ctx.return_value = (
            ContextCard(adv_value_20d=10e9, spread_bps=10.0),
            ContextCardData(adv_value_20d=10e9, spread_bps=10.0, ret_today=5.0),
        )

        await asyncio.wait_for(
            pipeline_loop(mock_feed, registry, mock_engine, market, scheduler, log, cfg, "test_run", None, mode="paper"),
            timeout=1.0,
        )

    mock_engine.decide.assert_not_awaited()
    mock_gr.assert_not_called()

    records = []
    for f in (tmp_path / "logs").glob("*.jsonl"):
        for line in f.read_text(encoding="utf-8").strip().split("\n"):
            if line:
                records.append(json.loads(line))

    blocked = [r for r in records if r.get("skip_reason") == "MARKET_BREADTH_RISK_OFF"]
    assert len(blocked) == 1


async def test_market_halt_block_logged_before_llm(tmp_path):
    from kindshot.event_registry import EventRegistry
    from kindshot.logger import JsonlLogger
    from kindshot.market import MarketMonitor
    from kindshot.price import PriceFetcher, SnapshotScheduler
    from kindshot.pipeline import pipeline_loop
    from kindshot.feed import KindFeed

    cfg = Config(log_dir=tmp_path / "logs", paper=True)
    log = JsonlLogger(cfg.log_dir, run_id="test_run")
    registry = EventRegistry()
    market = MarketMonitor(cfg)
    market._initialized = True
    market._halted = True
    market._kospi_change = -8.5
    market._kosdaq_change = -7.2
    scheduler = SnapshotScheduler(cfg, PriceFetcher(kis=None), log)

    mock_engine = MagicMock()
    mock_engine.decide = AsyncMock()

    mock_feed = AsyncMock(spec=KindFeed)

    async def _one_batch():
        yield [_make_raw()]
    mock_feed.stream = _one_batch

    with patch("kindshot.pipeline.build_context_card", new_callable=AsyncMock) as mock_ctx:
        from kindshot.models import ContextCard
        mock_ctx.return_value = (
            ContextCard(adv_value_20d=10e9, spread_bps=10.0),
            ContextCardData(adv_value_20d=10e9, spread_bps=10.0, ret_today=5.0),
        )

        await asyncio.wait_for(
            pipeline_loop(mock_feed, registry, mock_engine, market, scheduler, log, cfg, "test_run", None, mode="paper"),
            timeout=1.0,
        )

    mock_engine.decide.assert_not_awaited()

    records = []
    for f in (tmp_path / "logs").glob("*.jsonl"):
        for line in f.read_text(encoding="utf-8").strip().split("\n"):
            if line:
                records.append(json.loads(line))

    blocked = [r for r in records if r.get("skip_reason") == "MARKET_HALTED"]
    assert len(blocked) == 1
    assert blocked[0]["skip_stage"] == SkipStage.GUARDRAIL.value


async def test_pipeline_persists_runtime_context_card(tmp_path):
    from kindshot.event_registry import EventRegistry
    from kindshot.feed import KindFeed
    from kindshot.guardrails import GuardrailResult
    from kindshot.logger import JsonlLogger
    from kindshot.pipeline import pipeline_loop
    from kindshot.market import MarketMonitor
    from kindshot.models import ContextCard, DecisionRecord, Action, SizeHint
    from kindshot.price import PriceFetcher, SnapshotScheduler

    cfg = Config(
        log_dir=tmp_path / "logs",
        paper=True,
        runtime_context_cards_dir=tmp_path / "data" / "runtime" / "context_cards",
    )
    log = JsonlLogger(cfg.log_dir, run_id="test_run")
    registry = EventRegistry()
    market = MarketMonitor(cfg)
    market._initialized = True
    market._halted = False
    market._kospi_change = -0.2
    market._kosdaq_change = 0.1
    scheduler = SnapshotScheduler(cfg, PriceFetcher(kis=None), log)

    mock_decision = DecisionRecord(
        schema_version="0.1.2",
        run_id="test_run",
        event_id="",
        decided_at=datetime.now(timezone.utc),
        llm_model="test",
        llm_latency_ms=10,
        action=Action.BUY,
        confidence=80,
        size_hint=SizeHint.M,
        reason="test",
        decision_source="LLM",
    )
    mock_engine = MagicMock()
    mock_engine.decide = AsyncMock(return_value=mock_decision)

    raw = _make_raw()
    mock_feed = AsyncMock(spec=KindFeed)

    async def _one_batch():
        yield [raw]
    mock_feed.stream = _one_batch

    with patch("kindshot.pipeline.build_context_card", new_callable=AsyncMock) as mock_ctx, \
         patch("kindshot.pipeline.check_guardrails") as mock_gr:
        mock_ctx.return_value = (
            ContextCard(adv_value_20d=10e9, spread_bps=10.0, ret_today=5.0),
            ContextCardData(
                adv_value_20d=10e9,
                spread_bps=10.0,
                ret_today=5.0,
                quote_risk_state=QuoteRiskState(temp_stop_yn="Y", vi_cls_code="D"),
                orderbook_snapshot=OrderbookSnapshot(
                    ask_price1=50_100.0,
                    bid_price1=49_900.0,
                    ask_size1=90,
                    bid_size1=120,
                    total_ask_size=2000,
                    total_bid_size=2400,
                    spread_bps=40.0,
                ),
            ),
        )
        mock_gr.return_value = GuardrailResult(passed=True)

        await asyncio.wait_for(
            pipeline_loop(mock_feed, registry, mock_engine, market, scheduler, log, cfg, "test_run", None, mode="paper"),
            timeout=1.0,
        )

    files = list((tmp_path / "data" / "runtime" / "context_cards").glob("*.jsonl"))
    assert len(files) == 1
    rows = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
    assert rows[0]["type"] == "context_card"
    assert rows[0]["ticker"] == "005930"
    assert rows[0]["bucket"] == "POS_STRONG"
    assert rows[0]["quant_check_passed"] is True
    assert rows[0]["ctx"]["ret_today"] == 5.0
    assert rows[0]["raw"]["quote_risk_state"]["vi_cls_code"] == "D"
    assert rows[0]["raw"]["orderbook_snapshot"]["total_bid_size"] == 2400
    mock_engine.decide.assert_awaited_once()


async def test_wait_or_stop_interrupts_timeout():
    """_wait_or_stop should return immediately when stop_event is set."""
    import time
    from kindshot.main import _wait_or_stop

    stop_event = asyncio.Event()
    stop_event.set()
    t0 = time.monotonic()
    await _wait_or_stop(stop_event, 60)
    assert time.monotonic() - t0 < 0.1


async def test_quant_fail_still_tracks_price(tmp_path):
    """POS_STRONG quant 실패 시에도 반사실 데이터 수집을 위해 가격 추적해야 함.

    기존: qr.should_track_price (10% 샘플링) → 대부분 추적 안 함
    변경: should_track_price = True 고정 → 항상 추적
    """
    from kindshot.event_registry import EventRegistry
    from kindshot.feed import KindFeed
    from kindshot.logger import JsonlLogger
    from kindshot.pipeline import pipeline_loop
    from kindshot.market import MarketMonitor
    from kindshot.models import ContextCard
    from kindshot.price import PriceFetcher, SnapshotScheduler

    cfg = Config(
        log_dir=tmp_path / "logs",
        paper=True,
        # adv_threshold 크게 설정해 quant 실패 유도 (컨텍스트 카드 adv=10e9 < threshold)
        adv_threshold=9_999_999_999_999,
        pos_strong_adv_threshold=9_999_999_999_999,
    )
    log = JsonlLogger(cfg.log_dir, run_id="test_run")
    registry = EventRegistry()
    market = MarketMonitor(cfg)
    market._initialized = True
    market._halted = False
    fetcher = PriceFetcher(kis=None)
    scheduler = SnapshotScheduler(cfg, fetcher, log)

    mock_engine = MagicMock()
    mock_engine.decide = AsyncMock(return_value=None)

    raw = _make_raw()
    mock_feed = AsyncMock(spec=KindFeed)

    async def _one_batch():
        yield [raw]
    mock_feed.stream = _one_batch

    # random.random()을 1.0으로 고정해 샘플링이 절대 발동 안 되도록 함
    with patch("kindshot.pipeline.build_context_card", new_callable=AsyncMock) as mock_ctx, \
         patch("kindshot.quant.random") as mock_random:
        mock_random.random.return_value = 1.0  # quant_fail_sample_rate < 1.0 이므로 샘플링 미발동
        mock_ctx.return_value = (
            ContextCard(adv_value_20d=10e9, spread_bps=10.0),
            ContextCardData(adv_value_20d=10e9, spread_bps=10.0, ret_today=2.0),
        )

        try:
            await asyncio.wait_for(
                pipeline_loop(mock_feed, registry, mock_engine, market, scheduler, log, cfg, "test_run", None, mode="paper"),
                timeout=1.0,
            )
        except (asyncio.TimeoutError, StopAsyncIteration):
            pass

    # quant 실패해도 가격 추적 스케줄러에 반드시 등록되어야 함
    assert len(scheduler._heap) >= 1, "quant 실패 종목도 가격 추적 스케줄링 되어야 함"


async def test_pos_strong_adv_override_reaches_decision_engine(tmp_path):
    from kindshot.event_registry import EventRegistry
    from kindshot.feed import KindFeed
    from kindshot.guardrails import GuardrailResult
    from kindshot.logger import JsonlLogger
    from kindshot.pipeline import pipeline_loop
    from kindshot.market import MarketMonitor
    from kindshot.models import Action, ContextCard, DecisionRecord, SizeHint
    from kindshot.price import PriceFetcher, SnapshotScheduler

    cfg = Config(
        log_dir=tmp_path / "logs",
        paper=True,
        adv_threshold=5_000_000_000,
        pos_strong_adv_threshold=2_000_000_000,
    )
    log = JsonlLogger(cfg.log_dir, run_id="test_run")
    registry = EventRegistry()
    market = MarketMonitor(cfg)
    market._initialized = True
    market._halted = False
    scheduler = SnapshotScheduler(cfg, PriceFetcher(kis=None), log)

    mock_decision = DecisionRecord(
        schema_version="0.1.3",
        run_id="test_run",
        event_id="",
        decided_at=datetime.now(timezone.utc),
        llm_model="test",
        llm_latency_ms=10,
        action=Action.BUY,
        confidence=80,
        size_hint=SizeHint.M,
        reason="strong catalyst",
        decision_source="LLM",
    )
    mock_engine = MagicMock()
    mock_engine.decide = AsyncMock(return_value=mock_decision)

    raw = _make_raw(title="힘스, HDD용 유리플래터 검사장비 대규모 독점 공급계약 체결")
    mock_feed = AsyncMock(spec=KindFeed)

    async def _one_batch():
        yield [raw]
    mock_feed.stream = _one_batch

    with patch("kindshot.pipeline.build_context_card", new_callable=AsyncMock) as mock_ctx, \
         patch("kindshot.pipeline.check_guardrails") as mock_gr:
        mock_ctx.return_value = (
            ContextCard(adv_value_20d=2.5e9, spread_bps=10.0),
            ContextCardData(adv_value_20d=2.5e9, spread_bps=10.0, ret_today=2.0),
        )
        mock_gr.return_value = GuardrailResult(passed=True)

        try:
            await asyncio.wait_for(
                pipeline_loop(mock_feed, registry, mock_engine, market, scheduler, log, cfg, "test_run", None, mode="paper"),
                timeout=1.0,
            )
        except (asyncio.TimeoutError, StopAsyncIteration):
            pass

    mock_engine.decide.assert_awaited_once()
    assert mock_gr.call_args is not None
    assert mock_gr.call_args.kwargs["adv_threshold"] == 2_000_000_000


async def test_pos_weak_still_stops_at_strict_adv_threshold(tmp_path):
    raw = _make_raw(title="에이비엘바이오, AACR서 이중항체 ADC 2종 공개…美 임상 1상 추진")
    records = await _run_pipeline_once(
        tmp_path,
        [raw],
        paper=True,
        config_overrides={
            "adv_threshold": 5_000_000_000,
            "pos_strong_adv_threshold": 2_000_000_000,
            "news_weak_enabled": True,
        },
        ctx_raw=ContextCardData(adv_value_20d=2.5e9, spread_bps=10.0, ret_today=2.0),
    )

    quant_records = [r for r in records if r.get("type") == "event"]
    assert len(quant_records) == 1
    assert quant_records[0]["bucket"] == "POS_WEAK"
    assert quant_records[0]["skip_stage"] == "QUANT"
    assert quant_records[0]["skip_reason"] == "ADV_TOO_LOW"


async def test_skip_decision_tracks_price_for_false_negative(tmp_path):
    """SKIP 결정된 POS_STRONG 종목도 가격 추적하여 false negative 식별."""
    from kindshot.models import DecisionRecord, Action, SizeHint

    mock_decision = DecisionRecord(
        schema_version="0.1.2",
        run_id="test_run",
        event_id="",
        decided_at=datetime.now(timezone.utc),
        llm_model="test",
        llm_latency_ms=10,
        action=Action.SKIP,  # SKIP 결정
        confidence=55,
        size_hint=SizeHint.S,
        reason="소규모 계약",
        decision_source="LLM",
    )

    from kindshot.event_registry import EventRegistry
    from kindshot.feed import KindFeed
    from kindshot.guardrails import GuardrailResult
    from kindshot.logger import JsonlLogger
    from kindshot.pipeline import pipeline_loop
    from kindshot.market import MarketMonitor
    from kindshot.models import ContextCard
    from kindshot.price import PriceFetcher, SnapshotScheduler

    cfg = Config(log_dir=tmp_path / "logs", paper=True)
    log = JsonlLogger(cfg.log_dir, run_id="test_run")
    registry = EventRegistry()
    market = MarketMonitor(cfg)
    market._initialized = True
    market._halted = False
    scheduler = SnapshotScheduler(cfg, PriceFetcher(kis=None), log)

    mock_engine = MagicMock()
    mock_engine.decide = AsyncMock(return_value=mock_decision)

    raw = _make_raw()
    mock_feed = AsyncMock(spec=KindFeed)

    async def _one_batch():
        yield [raw]
    mock_feed.stream = _one_batch

    with patch("kindshot.pipeline.build_context_card", new_callable=AsyncMock) as mock_ctx, \
         patch("kindshot.pipeline.check_guardrails") as mock_gr:
        mock_ctx.return_value = (
            ContextCard(adv_value_20d=10e9, spread_bps=10.0),
            ContextCardData(adv_value_20d=10e9, spread_bps=10.0, ret_today=2.0),
        )
        mock_gr.return_value = GuardrailResult(passed=True)

        try:
            await asyncio.wait_for(
                pipeline_loop(mock_feed, registry, mock_engine, market, scheduler, log, cfg, "test_run", None, mode="paper"),
                timeout=1.0,
            )
        except (asyncio.TimeoutError, StopAsyncIteration):
            pass

    # SKIP 종목도 가격 추적: "skip_" 프리픽스 이벤트 ID가 스케줄러에 있어야 함
    skip_events = [s for s in scheduler._heap if s.event_id.startswith("skip_")]
    assert len(skip_events) >= 1, "SKIP 결정된 POS_STRONG 종목도 가격 추적되어야 함"

    # 원본 이벤트도 스케줄링됨 (SKIP이어도 schedule_t0 호출)
    main_events = [s for s in scheduler._heap if not s.event_id.startswith("skip_")]
    assert len(main_events) >= 1


# ── US-002: 장전 이벤트 재평가 메커니즘 ──────────────────


@pytest.mark.asyncio
async def test_premarket_intraday_thin_defers_event(tmp_path):
    """장전(09시 이전) iv_ratio=0 → INTRADAY_VALUE_TOO_THIN 시 pending에 추가 + registry unmark."""
    from kindshot.event_registry import EventRegistry
    from kindshot.logger import JsonlLogger
    from kindshot.market import MarketMonitor
    from kindshot.price import PriceFetcher, SnapshotScheduler
    from kindshot.pipeline import process_registered_event
    from kindshot.models import (
        Action, ContextCard, DecisionRecord, SizeHint,
    )
    from kindshot.guardrails import GuardrailResult

    cfg = Config(log_dir=tmp_path / "logs", paper=True)
    log = JsonlLogger(cfg.log_dir, run_id="test_run")
    registry = EventRegistry()
    market = MarketMonitor(cfg)
    market._initialized = True
    market._halted = False
    fetcher = PriceFetcher(kis=None)
    scheduler = SnapshotScheduler(cfg, fetcher, log)

    # 장전 시각: 07:30 KST = 22:30 UTC (전일)
    premarket_dt = datetime(2026, 3, 20, 22, 30, 0, tzinfo=timezone.utc)
    raw = RawDisclosure(
        title="삼성전자(005930) - 자사주 소각 결정",
        link="https://kind.krx.co.kr/?rcpNo=20260320000001",
        rss_guid="premarket_guid",
        published="2026-03-20T07:30:00+09:00",
        ticker="005930",
        corp_name="삼성전자",
        detected_at=premarket_dt,
    )

    # Registry에 등록
    processed = registry.process(raw)
    assert processed is not None
    event_id = processed.event_id

    # 동일 이벤트 → DUPLICATE 확인
    assert registry.process(raw) is None

    premarket_pending = []

    decision = DecisionRecord(
        schema_version="0.1.3", run_id="test_run", event_id=event_id,
        decided_at=premarket_dt, llm_model="test", llm_latency_ms=0,
        action=Action.BUY, confidence=85, size_hint=SizeHint.M,
        reason="test", decision_source="test",
    )

    with patch("kindshot.pipeline.build_context_card", new_callable=AsyncMock) as mock_ctx, \
         patch("kindshot.pipeline.check_guardrails") as mock_gr, \
         patch("kindshot.pipeline.classify") as mock_bucket:
        from kindshot.bucket import BucketResult
        from kindshot.context_card import ContextCardData
        mock_bucket.return_value = BucketResult(bucket=Bucket.POS_STRONG, keyword_hits=["자사주 소각"])
        mock_ctx.return_value = (
            ContextCard(adv_value_20d=10e9, spread_bps=10.0, intraday_value_vs_adv20d=0.0),
            ContextCardData(adv_value_20d=10e9, spread_bps=10.0, ret_today=0.0, intraday_value_vs_adv20d=0.0),
        )
        # INTRADAY_VALUE_TOO_THIN 반환
        mock_gr.return_value = GuardrailResult(passed=False, reason="INTRADAY_VALUE_TOO_THIN")

        mock_engine = MagicMock()
        mock_engine.decide = AsyncMock(return_value=decision)

        await process_registered_event(
            raw=raw, processed=processed,
            decision_engine=mock_engine, market=market,
            scheduler=scheduler, log=log, config=cfg,
            run_id="test_run", kis=None, counters=None,
            mode="paper", feed_source="KIND",
            registry=registry,
            premarket_pending=premarket_pending,
        )

    # pending에 추가되었는지 확인
    assert len(premarket_pending) == 1
    assert premarket_pending[0][0].ticker == "005930"

    # registry에서 unmark되어 재처리 가능
    reprocessed = registry.process(raw)
    assert reprocessed is not None, "unmark 후 재처리 가능해야 함"


@pytest.mark.asyncio
async def test_market_hours_intraday_thin_not_deferred(tmp_path):
    """장중(09시 이후) INTRADAY_VALUE_TOO_THIN은 pending에 추가 안 함."""
    from kindshot.event_registry import EventRegistry
    from kindshot.logger import JsonlLogger
    from kindshot.market import MarketMonitor
    from kindshot.price import PriceFetcher, SnapshotScheduler
    from kindshot.pipeline import process_registered_event
    from kindshot.models import (
        Action, DecisionRecord, SizeHint, ContextCard,
    )
    from kindshot.guardrails import GuardrailResult

    cfg = Config(log_dir=tmp_path / "logs", paper=True)
    log = JsonlLogger(cfg.log_dir, run_id="test_run")
    registry = EventRegistry()
    market = MarketMonitor(cfg)
    market._initialized = True
    market._halted = False
    fetcher = PriceFetcher(kis=None)
    scheduler = SnapshotScheduler(cfg, fetcher, log)

    # 장중 시각: 10:00 KST = 01:00 UTC
    market_dt = datetime(2026, 3, 20, 1, 0, 0, tzinfo=timezone.utc)
    raw = RawDisclosure(
        title="삼성전자(005930) - 자사주 소각 결정",
        link="https://kind.krx.co.kr/?rcpNo=20260320000002",
        rss_guid="market_guid",
        published="2026-03-20T10:00:00+09:00",
        ticker="005930",
        corp_name="삼성전자",
        detected_at=market_dt,
    )

    processed = registry.process(raw)
    assert processed is not None

    premarket_pending = []

    decision = DecisionRecord(
        schema_version="0.1.3", run_id="test_run", event_id=processed.event_id,
        decided_at=market_dt, llm_model="test", llm_latency_ms=0,
        action=Action.BUY, confidence=85, size_hint=SizeHint.M,
        reason="test", decision_source="test",
    )

    with patch("kindshot.pipeline.build_context_card", new_callable=AsyncMock) as mock_ctx, \
         patch("kindshot.pipeline.check_guardrails") as mock_gr, \
         patch("kindshot.pipeline.classify") as mock_bucket:
        from kindshot.bucket import BucketResult
        from kindshot.context_card import ContextCardData
        mock_bucket.return_value = BucketResult(bucket=Bucket.POS_STRONG, keyword_hits=["자사주 소각"])
        mock_ctx.return_value = (
            ContextCard(adv_value_20d=10e9, spread_bps=10.0, intraday_value_vs_adv20d=0.005),
            ContextCardData(adv_value_20d=10e9, spread_bps=10.0, ret_today=0.5, intraday_value_vs_adv20d=0.005),
        )
        mock_gr.return_value = GuardrailResult(passed=False, reason="INTRADAY_VALUE_TOO_THIN")

        mock_engine = MagicMock()
        mock_engine.decide = AsyncMock(return_value=decision)

        await process_registered_event(
            raw=raw, processed=processed,
            decision_engine=mock_engine, market=market,
            scheduler=scheduler, log=log, config=cfg,
            run_id="test_run", kis=None, counters=None,
            mode="paper", feed_source="KIND",
            registry=registry,
            premarket_pending=premarket_pending,
        )

    # 장중이므로 pending에 추가되지 않아야 함
    assert len(premarket_pending) == 0


# ── v66: shadow snapshot tests ──────────────────────────────────


async def test_guardrail_block_schedules_shadow_snapshot_for_high_conf_buy(tmp_path):
    """차단된 BUY(conf>=75)는 shadow snapshot을 스케줄링해야 한다."""
    from kindshot.models import DecisionRecord, Action, SizeHint

    mock_decision = DecisionRecord(
        schema_version="0.1.2",
        run_id="test_run",
        event_id="",
        decided_at=datetime.now(timezone.utc),
        llm_model="test",
        llm_latency_ms=10,
        action=Action.BUY,
        confidence=80,
        size_hint=SizeHint.M,
        reason="test",
        decision_source="LLM",
    )
    raw = _make_raw()

    with patch("kindshot.price.SnapshotScheduler.schedule_t0") as mock_schedule:
        await _run_pipeline_once(
            tmp_path, [raw],
            decision_side_effect=[mock_decision],
            guardrail_passed=False,
        )

        # shadow snapshot이 스케줄링되었는지 확인
        assert mock_schedule.call_count >= 1
        # shadow_ prefix로 호출되었는지 확인
        shadow_calls = [
            c for c in mock_schedule.call_args_list
            if str(c).find("shadow_") != -1
        ]
        assert len(shadow_calls) >= 1
        # is_buy_decision=False 확인
        for call in shadow_calls:
            assert call.kwargs.get("is_buy_decision") is False


async def test_guardrail_block_no_shadow_for_low_conf_buy(tmp_path):
    """차단된 BUY(conf<75)는 shadow snapshot을 스케줄링하지 않아야 한다."""
    from kindshot.models import DecisionRecord, Action, SizeHint

    mock_decision = DecisionRecord(
        schema_version="0.1.2",
        run_id="test_run",
        event_id="",
        decided_at=datetime.now(timezone.utc),
        llm_model="test",
        llm_latency_ms=10,
        action=Action.BUY,
        confidence=70,
        size_hint=SizeHint.S,
        reason="test",
        decision_source="LLM",
    )
    raw = _make_raw()

    with patch("kindshot.price.SnapshotScheduler.schedule_t0") as mock_schedule:
        await _run_pipeline_once(
            tmp_path, [raw],
            decision_side_effect=[mock_decision],
            guardrail_passed=False,
        )

        # conf<75이므로 shadow snapshot 호출 없어야 함
        shadow_calls = [
            c for c in mock_schedule.call_args_list
            if str(c).find("shadow_") != -1
        ]
        assert len(shadow_calls) == 0


async def test_guardrail_block_no_shadow_for_skip_action(tmp_path):
    """SKIP 결정이 guardrail에서 차단되면 shadow snapshot 미생성."""
    from kindshot.models import DecisionRecord, Action, SizeHint

    mock_decision = DecisionRecord(
        schema_version="0.1.2",
        run_id="test_run",
        event_id="",
        decided_at=datetime.now(timezone.utc),
        llm_model="test",
        llm_latency_ms=10,
        action=Action.SKIP,
        confidence=85,
        size_hint=SizeHint.S,
        reason="test",
        decision_source="LLM",
    )
    raw = _make_raw()

    with patch("kindshot.price.SnapshotScheduler.schedule_t0") as mock_schedule:
        await _run_pipeline_once(
            tmp_path, [raw],
            decision_side_effect=[mock_decision],
            guardrail_passed=False,
        )

        # SKIP이므로 shadow snapshot 호출 없어야 함
        shadow_calls = [
            c for c in mock_schedule.call_args_list
            if str(c).find("shadow_") != -1
        ]
        assert len(shadow_calls) == 0


# ── v82: 해외 품목허가 confidence cap 75 ──

async def test_v82_foreign_product_approval_cap(tmp_path):
    """비FDA/EMA 해외 품목허가 → confidence cap 75 적용 확인.

    cap 후 guardrail 보정(ADV 등)이 추가되어 최종 confidence가 78 이상일 수 있으나,
    원본 85에서 cap 75로 제한된 후 보정되므로 최종값은 원본보다 낮아야 함.
    """
    from kindshot.models import Action, DecisionRecord, SizeHint

    raw = RawDisclosure(
        title="셀트리온 일본 품목허가 승인",
        link="https://kind.krx.co.kr/?rcpNo=20260329000001",
        rss_guid="guid-foreign-approval",
        published="2026-03-29T10:00:00+09:00",
        ticker="068270",
        corp_name="셀트리온",
        detected_at=datetime(2026, 3, 29, 1, 0, 0, tzinfo=timezone.utc),
    )
    decision = DecisionRecord(
        schema_version="0.1.2",
        run_id="test_run",
        event_id="",
        decided_at=datetime(2026, 3, 29, 1, 0, 5, tzinfo=timezone.utc),
        llm_model="test",
        llm_latency_ms=10,
        action=Action.BUY,
        confidence=85,
        size_hint=SizeHint.M,
        reason="일본 품목허가",
        decision_source="LLM",
    )

    records = await _run_pipeline_once(
        tmp_path,
        [raw],
        decision_side_effect=[decision],
        paper=True,
        ctx_raw=ContextCardData(adv_value_20d=10e9, spread_bps=10.0, ret_today=1.0),
    )

    # cap 적용 확인: LLM 원본 85 → cap 75 → guardrail 보정 후 최종값은 원본보다 낮아야 함
    decision_records = [r for r in records if r.get("type") == "decision"]
    assert len(decision_records) == 1
    assert decision_records[0]["confidence"] < 85  # cap이 적용되었음


async def test_v82_fda_approval_not_capped(tmp_path):
    """FDA 품목허가는 cap 적용 안 됨 — confidence 유지."""
    from kindshot.models import Action, DecisionRecord, SizeHint

    raw = RawDisclosure(
        title="셀트리온 FDA 품목허가 승인",
        link="https://kind.krx.co.kr/?rcpNo=20260329000002",
        rss_guid="guid-fda-approval",
        published="2026-03-29T10:00:00+09:00",
        ticker="068270",
        corp_name="셀트리온",
        detected_at=datetime(2026, 3, 29, 1, 0, 0, tzinfo=timezone.utc),
    )
    decision = DecisionRecord(
        schema_version="0.1.2",
        run_id="test_run",
        event_id="",
        decided_at=datetime(2026, 3, 29, 1, 0, 5, tzinfo=timezone.utc),
        llm_model="test",
        llm_latency_ms=10,
        action=Action.BUY,
        confidence=85,
        size_hint=SizeHint.M,
        reason="FDA 품목허가",
        decision_source="LLM",
    )

    records = await _run_pipeline_once(
        tmp_path,
        [raw],
        decision_side_effect=[decision],
        paper=True,
        ctx_raw=ContextCardData(adv_value_20d=10e9, spread_bps=10.0, ret_today=1.0),
    )

    # FDA 품목허가 → cap 미적용 → confidence 유지 → BUY 통과 가능
    decision_records = [r for r in records if r.get("type") == "decision"]
    assert len(decision_records) == 1
    assert decision_records[0]["confidence"] >= 78  # cap 미적용
