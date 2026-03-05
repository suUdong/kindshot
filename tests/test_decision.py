"""Tests for LLM decision engine."""

import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kindshot.config import Config
from kindshot.decision import DecisionEngine, _parse_llm_response, _build_prompt, LlmTimeoutError, LlmParseError
from kindshot.models import Bucket, ContextCard, Action, SizeHint


def test_parse_valid_json():
    raw = '{"action": "BUY", "confidence": 82, "size_hint": "M", "reason": "good signal"}'
    result = _parse_llm_response(raw)
    assert result is not None
    assert result["action"] == "BUY"
    assert result["confidence"] == 82


def test_parse_json_with_backticks():
    raw = '```json\n{"action": "SKIP", "confidence": 30, "size_hint": "S", "reason": "already priced in"}\n```'
    result = _parse_llm_response(raw)
    assert result is not None
    assert result["action"] == "SKIP"


def test_parse_invalid_json():
    result = _parse_llm_response("not json at all")
    assert result is None


def test_parse_invalid_action():
    raw = '{"action": "SELL", "confidence": 50, "size_hint": "M", "reason": "test"}'
    result = _parse_llm_response(raw)
    assert result is None


def test_parse_reason_truncated():
    long_reason = "x" * 200
    raw = f'{{"action": "BUY", "confidence": 50, "size_hint": "M", "reason": "{long_reason}"}}'
    result = _parse_llm_response(raw)
    assert result is not None
    assert len(result["reason"]) == 100


def test_parse_reason_non_string():
    raw = '{"action": "BUY", "confidence": 50, "size_hint": "M", "reason": 42}'
    result = _parse_llm_response(raw)
    assert result is not None
    assert result["reason"] == "42"


def test_build_prompt():
    ctx = ContextCard(ret_today=6.1, ret_1d=0.8, ret_3d=4.2, pos_20d=87, gap=0.3, adv_value_20d=82e9, spread_bps=9, vol_pct_20d=88)
    prompt = _build_prompt(
        bucket=Bucket.POS_STRONG,
        headline="반도체 사업 미국 대형 공급계약 체결",
        ticker="005930",
        corp_name="삼성전자",
        detected_at="09:12:04",
        ctx=ctx,
    )
    assert "POS_STRONG" in prompt
    assert "005930" in prompt
    assert "BUY" in prompt


async def test_cache_hit():
    cfg = Config(anthropic_api_key="test")
    engine = DecisionEngine(cfg)

    mock_client = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text='{"action":"BUY","confidence":80,"size_hint":"M","reason":"test"}')]
    mock_msg.usage = MagicMock(input_tokens=100, output_tokens=50)
    mock_client.messages.create = AsyncMock(return_value=mock_msg)
    engine._client = mock_client

    ctx = ContextCard()
    # First call
    r1 = await engine.decide("005930", "삼성전자", "공급계약 체결", Bucket.POS_STRONG, ctx, "09:00:00")
    # Second call (should be cached)
    r2 = await engine.decide("005930", "삼성전자", "공급계약 체결", Bucket.POS_STRONG, ctx, "09:00:01")

    assert r1 is not None
    assert r2 is not None
    assert r2.decision_source == "CACHE"
    assert mock_client.messages.create.call_count == 1


async def test_llm_timeout_raises():
    cfg = Config(anthropic_api_key="test", llm_wait_for_s=0.01)
    engine = DecisionEngine(cfg)

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(side_effect=asyncio.TimeoutError())
    engine._client = mock_client

    ctx = ContextCard()
    with pytest.raises(LlmTimeoutError):
        await engine.decide("005930", "삼성전자", "공급계약 체결", Bucket.POS_STRONG, ctx, "09:00:00")


async def test_llm_bad_response_structure_raises():
    cfg = Config(anthropic_api_key="test")
    engine = DecisionEngine(cfg)

    mock_client = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.content = []  # Empty content list
    mock_client.messages.create = AsyncMock(return_value=mock_msg)
    engine._client = mock_client

    ctx = ContextCard()
    with pytest.raises(LlmParseError):
        await engine.decide("005930", "삼성전자", "공급계약 체결", Bucket.POS_STRONG, ctx, "09:00:00")


async def test_llm_invalid_json_raises():
    cfg = Config(anthropic_api_key="test")
    engine = DecisionEngine(cfg)

    mock_client = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="not json")]
    mock_client.messages.create = AsyncMock(return_value=mock_msg)
    engine._client = mock_client

    ctx = ContextCard()
    with pytest.raises(LlmParseError):
        await engine.decide("005930", "삼성전자", "공급계약 체결", Bucket.POS_STRONG, ctx, "09:00:00")
