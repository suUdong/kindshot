"""Tests for LLM decision engine."""

import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kindshot.config import Config
from kindshot.decision import (
    DecisionEngine,
    _parse_llm_response,
    _build_prompt,
    LlmTimeoutError,
    LlmParseError,
    LlmCallError,
)
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


def test_parse_json_with_leading_text():
    raw = '분석 결과:\n{"action":"BUY","confidence":81,"size_hint":"M","reason":"momentum intact"}'
    result = _parse_llm_response(raw)
    assert result is not None
    assert result["action"] == "BUY"


def test_parse_json_with_trailing_text():
    raw = '{"action":"SKIP","confidence":35,"size_hint":"S","reason":"too extended"}\n이상입니다.'
    result = _parse_llm_response(raw)
    assert result is not None
    assert result["action"] == "SKIP"


def test_parse_json_fenced_with_extra_wrapper_text():
    raw = '다음 JSON만 사용하세요.\n```json\n{"action":"BUY","confidence":77,"size_hint":"S","reason":"fresh contract"}\n```\n설명 끝.'
    result = _parse_llm_response(raw)
    assert result is not None
    assert result["size_hint"] == "S"


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


def test_parse_missing_size_hint_defaults_by_confidence():
    """size_hint 누락 시 confidence 기반 기본값."""
    # High confidence → L
    raw = '{"action": "BUY", "confidence": 85, "reason": "strong signal"}'
    result = _parse_llm_response(raw)
    assert result is not None
    assert result["size_hint"] == "L"

    # Medium confidence (75+) → M
    raw = '{"action": "BUY", "confidence": 76, "reason": "moderate"}'
    result = _parse_llm_response(raw)
    assert result is not None
    assert result["size_hint"] == "M"

    # Low-medium confidence (65-74) → S
    raw = '{"action": "BUY", "confidence": 67, "reason": "weak signal"}'
    result = _parse_llm_response(raw)
    assert result is not None
    assert result["size_hint"] == "S"

    # Low confidence → S
    raw = '{"action": "SKIP", "confidence": 30, "reason": "weak"}'
    result = _parse_llm_response(raw)
    assert result is not None
    assert result["size_hint"] == "S"


def test_parse_invalid_size_hint_defaults():
    """잘못된 size_hint도 confidence 기반 기본값으로 복구."""
    raw = '{"action": "BUY", "confidence": 75, "size_hint": "XL", "reason": "test"}'
    result = _parse_llm_response(raw)
    assert result is not None
    assert result["size_hint"] == "M"


def test_build_prompt():
    ctx = ContextCard(
        ret_today=6.1,
        ret_1d=0.8,
        ret_3d=4.2,
        pos_20d=87,
        gap=0.3,
        adv_value_20d=82e9,
        spread_bps=9,
        vol_pct_20d=88,
        intraday_value_vs_adv20d=0.043,
        top_ask_notional=8_500_000,
        quote_temp_stop=False,
        quote_liquidation_trade=False,
    )
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
    assert "intraday_value_vs_adv20d=0.043" in prompt
    assert "top_ask_notional=8500000" in prompt


def test_build_prompt_truncates_long_headline():
    """Headlines longer than 500 chars are truncated to prevent prompt injection."""
    ctx = ContextCard(
        ret_today=1.0, ret_1d=0.0, ret_3d=0.0, pos_20d=50,
        gap=0.0, adv_value_20d=10e9, spread_bps=10.0,
    )
    long_headline = "A" * 1000
    prompt = _build_prompt(
        bucket=Bucket.POS_STRONG,
        headline=long_headline,
        ticker="005930",
        corp_name="테스트",
        detected_at="09:00:00",
        ctx=ctx,
    )
    # Headline in prompt should be truncated to 500 chars
    assert "A" * 500 in prompt
    assert "A" * 501 not in prompt


def test_cache_key_changes_with_microstructure_context():
    cfg = Config(anthropic_api_key="test")
    engine = DecisionEngine(cfg)

    ctx1 = ContextCard(adv_value_20d=10e9, spread_bps=10.0, ret_today=5.0, intraday_value_vs_adv20d=0.01)
    ctx2 = ContextCard(adv_value_20d=10e9, spread_bps=10.0, ret_today=5.0, intraday_value_vs_adv20d=0.02)

    key1 = engine._cache_key("005930", "공급계약 체결", Bucket.POS_STRONG, ctx1)
    key2 = engine._cache_key("005930", "공급계약 체결", Bucket.POS_STRONG, ctx2)

    assert key1 != key2


async def test_cache_hit():
    cfg = Config(anthropic_api_key="test")
    engine = DecisionEngine(cfg)

    mock_client = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text='{"action":"BUY","confidence":80,"size_hint":"M","reason":"test"}')]
    mock_msg.usage = MagicMock(input_tokens=100, output_tokens=50)
    mock_client.messages.create = AsyncMock(return_value=mock_msg)
    engine._llm._client = mock_client

    ctx = ContextCard()
    # First call
    r1 = await engine.decide("005930", "삼성전자", "공급계약 체결", Bucket.POS_STRONG, ctx, "09:00:00")
    # Second call (should be cached)
    r2 = await engine.decide("005930", "삼성전자", "공급계약 체결", Bucket.POS_STRONG, ctx, "09:00:01")

    assert r1 is not None
    assert r2 is not None
    assert r2.decision_source == "CACHE"
    assert mock_client.messages.create.call_count == 1


async def test_inflight_dedup_single_upstream_call():
    cfg = Config(anthropic_api_key="test")
    engine = DecisionEngine(cfg)

    mock_client = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text='{"action":"BUY","confidence":80,"size_hint":"M","reason":"test"}')]

    call_count = {"n": 0}

    async def _create(*args, **kwargs):
        call_count["n"] += 1
        await asyncio.sleep(0.05)
        return mock_msg

    mock_client.messages.create = AsyncMock(side_effect=_create)
    engine._llm._client = mock_client

    ctx = ContextCard()
    r1, r2 = await asyncio.gather(
        engine.decide("005930", "삼성전자", "공급계약 체결", Bucket.POS_STRONG, ctx, "09:00:00"),
        engine.decide("005930", "삼성전자", "공급계약 체결", Bucket.POS_STRONG, ctx, "09:00:00"),
    )

    assert r1 is not None
    assert r2 is not None
    assert call_count["n"] == 1
    assert {r1.decision_source, r2.decision_source} == {"LLM", "CACHE"}


async def test_inflight_dedup_error_propagates_to_all_callers():
    cfg = Config(anthropic_api_key="test")
    engine = DecisionEngine(cfg)

    mock_client = AsyncMock()
    call_count = {"n": 0}

    async def _create(*args, **kwargs):
        call_count["n"] += 1
        await asyncio.sleep(0.05)
        raise RuntimeError("upstream failure")

    mock_client.messages.create = AsyncMock(side_effect=_create)
    engine._llm._client = mock_client

    ctx = ContextCard()

    async def _call():
        with pytest.raises(LlmCallError):
            await engine.decide("005930", "삼성전자", "공급계약 체결", Bucket.POS_STRONG, ctx, "09:00:00")

    await asyncio.gather(_call(), _call())
    assert call_count["n"] == 3  # 3 attempts with exponential backoff (all fail)


async def test_llm_timeout_raises():
    cfg = Config(anthropic_api_key="test", llm_wait_for_s=0.01)
    engine = DecisionEngine(cfg)

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(side_effect=asyncio.TimeoutError())
    engine._llm._client = mock_client

    ctx = ContextCard()
    with pytest.raises(LlmTimeoutError):
        await engine.decide("005930", "삼성전자", "공급계약 체결", Bucket.POS_STRONG, ctx, "09:00:00")


async def test_llm_call_error_raises():
    cfg = Config(anthropic_api_key="test")
    engine = DecisionEngine(cfg)

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(side_effect=RuntimeError("503"))
    engine._llm._client = mock_client

    ctx = ContextCard()
    with pytest.raises(LlmCallError):
        await engine.decide("005930", "삼성전자", "공급계약 체결", Bucket.POS_STRONG, ctx, "09:00:00")


async def test_llm_bad_response_structure_raises():
    cfg = Config(anthropic_api_key="test")
    engine = DecisionEngine(cfg)

    mock_client = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.content = []  # Empty content list
    mock_client.messages.create = AsyncMock(return_value=mock_msg)
    engine._llm._client = mock_client

    ctx = ContextCard()
    with pytest.raises(LlmCallError):
        await engine.decide("005930", "삼성전자", "공급계약 체결", Bucket.POS_STRONG, ctx, "09:00:00")


async def test_llm_invalid_json_raises():
    cfg = Config(anthropic_api_key="test")
    engine = DecisionEngine(cfg)

    mock_client = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="not json")]
    mock_client.messages.create = AsyncMock(return_value=mock_msg)
    engine._llm._client = mock_client

    ctx = ContextCard()
    with pytest.raises(LlmParseError):
        await engine.decide("005930", "삼성전자", "공급계약 체결", Bucket.POS_STRONG, ctx, "09:00:00")


# ── v3 프롬프트 & 파서 테스트 ──────────────────

def test_prompt_contains_market_adjustment():
    """프롬프트에 market_adjustment 섹션 존재."""
    ctx = ContextCard()
    prompt = _build_prompt(Bucket.POS_STRONG, "테스트", "005930", "삼성전자", "09:00:00", ctx)
    assert "market_adjustment" in prompt
    assert "KOSPI<-2%" in prompt
    assert "confidence -5" in prompt


def test_prompt_contains_concrete_examples():
    """프롬프트에 실전 사례 기반 예시 존재."""
    ctx = ContextCard()
    prompt = _build_prompt(Bucket.POS_STRONG, "테스트", "005930", "삼성전자", "09:00:00", ctx)
    assert "실전_사례" in prompt
    assert "BUY(85,L)" in prompt
    assert "BUY(88,L)" in prompt
    assert "SKIP" in prompt
    assert "LOSS 사례" in prompt


def test_prompt_market_context_included():
    """시장 컨텍스트가 프롬프트에 포함."""
    from kindshot.models import MarketContext
    ctx = ContextCard()
    mctx = MarketContext(kospi_change_pct=-2.5, kosdaq_change_pct=-1.8, kospi_breadth_ratio=0.25)
    prompt = _build_prompt(Bucket.POS_STRONG, "테스트", "005930", "삼성전자", "09:00:00", ctx, mctx)
    assert "KOSPI=-2.5%" in prompt
    assert "breadth_ratio=0.25" in prompt


def test_parse_confidence_boundary_zero():
    """confidence=0 허용."""
    raw = '{"action": "SKIP", "confidence": 0, "size_hint": "S", "reason": "no catalyst"}'
    result = _parse_llm_response(raw)
    assert result is not None
    assert result["confidence"] == 0


def test_parse_confidence_boundary_hundred():
    """confidence=100 허용."""
    raw = '{"action": "BUY", "confidence": 100, "size_hint": "L", "reason": "FDA approved"}'
    result = _parse_llm_response(raw)
    assert result is not None
    assert result["confidence"] == 100


def test_parse_empty_reason_allowed():
    """빈 reason도 허용."""
    raw = '{"action": "SKIP", "confidence": 30, "size_hint": "S", "reason": ""}'
    result = _parse_llm_response(raw)
    assert result is not None
    assert result["reason"] == ""


def test_parse_missing_reason_defaults_empty():
    """reason 누락 시 빈 문자열."""
    raw = '{"action": "BUY", "confidence": 80, "size_hint": "L"}'
    result = _parse_llm_response(raw)
    assert result is not None
    assert result["reason"] == ""


def test_parse_float_confidence_accepted():
    """confidence가 float여도 허용."""
    raw = '{"action": "BUY", "confidence": 82.5, "size_hint": "M", "reason": "good"}'
    result = _parse_llm_response(raw)
    assert result is not None
    assert result["confidence"] == 82.5


def test_parse_buy_conf_72_forced_to_skip():
    """BUY with confidence 72 is auto-converted to SKIP (safety net)."""
    raw = '{"action": "BUY", "confidence": 72, "size_hint": "M", "reason": "ESS 공급계약"}'
    result = _parse_llm_response(raw)
    assert result is not None
    assert result["action"] == "SKIP"


def test_parse_buy_conf_74_forced_to_skip():
    """BUY with confidence 74 is auto-converted to SKIP."""
    raw = '{"action": "BUY", "confidence": 74, "size_hint": "M", "reason": "중형 수주"}'
    result = _parse_llm_response(raw)
    assert result is not None
    assert result["action"] == "SKIP"


def test_parse_buy_conf_75_stays_buy():
    """BUY with confidence 75 stays BUY (minimum threshold)."""
    raw = '{"action": "BUY", "confidence": 75, "size_hint": "M", "reason": "확정 수주"}'
    result = _parse_llm_response(raw)
    assert result is not None
    assert result["action"] == "BUY"


def test_parse_skip_conf_72_stays_skip():
    """SKIP with confidence 72 stays SKIP (no conversion needed)."""
    raw = '{"action": "SKIP", "confidence": 72, "size_hint": "S", "reason": "대형주 이미 반영"}'
    result = _parse_llm_response(raw)
    assert result is not None
    assert result["action"] == "SKIP"


def test_prompt_contains_decision_bias():
    """프롬프트에 decision_bias 섹션 존재."""
    ctx = ContextCard()
    prompt = _build_prompt(Bucket.POS_STRONG, "테스트", "005930", "삼성전자", "09:00:00", ctx)
    assert "decision_bias" in prompt
    assert "POS_STRONG" in prompt
    assert "SKIP" in prompt
    assert "BUY" in prompt


def test_prompt_pos_weak_bias_conservative():
    """POS_WEAK 프롬프트에 보수적 바이어스 존재."""
    ctx = ContextCard()
    prompt = _build_prompt(Bucket.POS_WEAK, "목표가 상향", "005930", "삼성전자", "09:00:00", ctx)
    assert "POS_WEAK" in prompt
    assert "SKIP" in prompt
