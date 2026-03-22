"""Tests for models.py — Pydantic models serialization and enums."""

import json
from datetime import datetime, timezone

from kindshot.models import (
    Action,
    Bucket,
    ContextCard,
    DecisionRecord,
    EventRecord,
    EventIdMethod,
    MarketContext,
    SizeHint,
    SkipStage,
)


def test_bucket_enum_values():
    assert Bucket.POS_STRONG.value == "POS_STRONG"
    assert Bucket.UNKNOWN.value == "UNKNOWN"
    assert Bucket.IGNORE.value == "IGNORE"


def test_context_card_defaults():
    card = ContextCard()
    assert card.ret_today is None
    assert card.rsi_14 is None
    assert card.macd_hist is None
    assert card.quote_temp_stop is None


def test_context_card_with_values():
    card = ContextCard(ret_today=1.5, rsi_14=65.3, macd_hist=-50.2)
    assert card.ret_today == 1.5
    assert card.rsi_14 == 65.3
    assert card.macd_hist == -50.2


def test_context_card_serialization():
    card = ContextCard(ret_today=2.0, spread_bps=15.0)
    data = card.model_dump()
    assert data["ret_today"] == 2.0
    assert data["spread_bps"] == 15.0
    assert data["rsi_14"] is None


def test_context_card_json_roundtrip():
    card = ContextCard(ret_today=3.5, adv_value_20d=1e10)
    json_str = card.model_dump_json()
    restored = ContextCard.model_validate_json(json_str)
    assert restored.ret_today == 3.5
    assert restored.adv_value_20d == 1e10


def test_event_record_defaults():
    rec = EventRecord(
        schema_version="0.1.3",
        run_id="test",
        event_id="evt_001",
        event_id_method=EventIdMethod.UID,
        event_group_id="evt_001",
        detected_at="2026-03-16T09:00:00+09:00",
        ticker="005930",
        corp_name="삼성전자",
        headline="테스트",
        bucket="POS_STRONG",
    )
    assert rec.type == "event"
    assert rec.mode == "live"
    assert rec.ticker == "005930"


def test_decision_record_serialization():
    rec = DecisionRecord(
        schema_version="0.1.3",
        run_id="test",
        event_id="evt_001",
        decided_at=datetime.now(timezone.utc),
        llm_model="claude-haiku-4-5-20251001",
        llm_latency_ms=150,
        action=Action.BUY,
        confidence=85,
        size_hint=SizeHint.L,
        reason="대형 수주",
    )
    data = rec.model_dump(mode="json")
    assert data["action"] == "BUY"
    assert data["confidence"] == 85
    assert data["size_hint"] == "L"


def test_market_context_defaults():
    mc = MarketContext()
    assert mc.kospi_change_pct is None
    assert mc.vkospi is None


def test_skip_stage_enum():
    assert SkipStage.BUCKET.value == "BUCKET"
    assert SkipStage.QUANT.value == "QUANT"
