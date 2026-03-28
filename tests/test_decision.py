"""Tests for LLM decision engine."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from kindshot.config import Config
from kindshot.decision import (
    DecisionEngine,
    _parse_llm_response,
    _build_prompt,
    _contract_preflight_skip,
    has_article_pattern,
    LlmTimeoutError,
    LlmParseError,
    LlmCallError,
)
from kindshot.models import AlphaSignalContext, Bucket, ContextCard, Action, MarketContext
from kindshot.news_semantics import build_news_signal


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
        news_signal=build_news_signal(
            headline="반도체 사업 미국 대형 공급계약 체결",
            ticker="005930",
            corp_name="삼성전자",
            detected_at=datetime.now(timezone.utc),
            keyword_hits=["공급계약"],
        ),
    )
    assert "POS_STRONG" in prompt
    assert "005930" in prompt
    assert "BUY" in prompt
    assert "intraday_value_vs_adv20d=0.043" in prompt
    assert "top_ask_notional=8500000" in prompt
    assert "ctx_signal:" in prompt
    assert "news_category=contract" in prompt
    assert "hold_profile=20m" in prompt
    assert "direct_disclosure=true" in prompt
    assert "impact_score=" in prompt
    assert "cluster_size=1" in prompt


def test_build_prompt_includes_structured_risk_context():
    from kindshot.guardrails import DailyLossBudgetSnapshot

    ctx = ContextCard(ret_today=1.2, ret_1d=0.4, ret_3d=1.8, pos_20d=64, gap=0.1, adv_value_20d=12e9, spread_bps=8)
    prompt = _build_prompt(
        bucket=Bucket.POS_STRONG,
        headline="자사주 소각 결정",
        ticker="005930",
        corp_name="삼성전자",
        detected_at="09:05:00",
        ctx=ctx,
        raw_headline='KB증권 "삼성전자 자사주 소각 결정"',
        dorg="연합뉴스",
        keyword_hits=["자사주 소각"],
        hold_minutes=0,
        risk_budget=DailyLossBudgetSnapshot(
            effective_floor_won=-500000,
            remaining_budget_won=250000,
            effective_floor_pct=-0.5,
            remaining_budget_pct=0.25,
            streak_multiplier=0.75,
        ),
        consecutive_stop_losses=2,
    )
    assert "broker_note=true" in prompt
    assert "hold_profile=EOD" in prompt
    assert "loss_floor_won=-500000" in prompt
    assert "remaining_loss_budget_won=250000" in prompt
    assert "consecutive_stop_losses=2" in prompt


def test_build_prompt_includes_macro_market_context():
    ctx = ContextCard(
        ret_today=1.5,
        ret_1d=0.4,
        ret_3d=1.2,
        pos_20d=63,
        gap=0.1,
        adv_value_20d=25e9,
        spread_bps=11,
    )
    market_ctx = MarketContext(
        kospi_change_pct=-0.5,
        kosdaq_change_pct=0.3,
        kospi_breadth_ratio=1.2,
        macro_overall_regime="contractionary",
        macro_overall_confidence=0.74,
        macro_kr_regime="neutral",
        macro_crypto_regime="contractionary",
    )
    prompt = _build_prompt(
        bucket=Bucket.POS_STRONG,
        headline="대규모 수주 공시",
        ticker="005930",
        corp_name="삼성전자",
        detected_at="09:10:00",
        ctx=ctx,
        market_ctx=market_ctx,
    )
    assert "macro=contractionary" in prompt
    assert "kr_macro=neutral" in prompt
    assert "crypto_macro=contractionary" in prompt


def test_build_prompt_includes_alpha_scanner_signal():
    ctx = ContextCard(
        ret_today=1.5,
        ret_1d=0.4,
        ret_3d=1.2,
        pos_20d=63,
        gap=0.1,
        adv_value_20d=25e9,
        spread_bps=11,
        alpha_signal=AlphaSignalContext(
            ticker="005930",
            signal_type="STRONG_BUY",
            score_current=83.5,
            confidence=86,
            size_hint="full",
            age_hours=1.2,
        ),
    )
    prompt = _build_prompt(
        bucket=Bucket.POS_STRONG,
        headline="대규모 수주 공시",
        ticker="005930",
        corp_name="삼성전자",
        detected_at="09:10:00",
        ctx=ctx,
    )
    assert "alpha_signal=STRONG_BUY" in prompt
    assert "alpha_score=83.5" in prompt
    assert "alpha_confidence=86" in prompt
    assert "alpha_size=full" in prompt


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


def test_contract_preflight_skips_brokerage_commentary_contract_headline():
    ctx = ContextCard(ret_today=0.5, ret_3d=0.5, adv_value_20d=50e9)
    parsed = _contract_preflight_skip(
        "삼성전자 추가 상승 여력 충분 장기공급계약 요구 큰 폭 증가",
        ["공급계약"],
        ctx,
        raw_headline='KB증권 "삼성전자, 추가 상승 여력 충분…장기공급계약 요구 큰 폭 증가"',
        dorg="연합뉴스",
    )
    assert parsed is not None
    assert parsed["reason"] == "rule_preflight:contract_article"


def test_contract_preflight_keeps_direct_disclosure_contract_headline():
    """v65: 200억+ 직접 공시 계약은 LLM 판단으로 넘어감. 200억 미만은 소규모 차단."""
    ctx = ContextCard(ret_today=0.5, ret_3d=0.5, adv_value_20d=50e9)
    # 250억 → 통과 (LLM 판단)
    parsed = _contract_preflight_skip(
        "넥스틴, SK하이닉스와 250억 규모 공급계약 체결",
        ["공급계약"],
        ctx,
        raw_headline="넥스틴, SK하이닉스와 250억 규모 공급계약 체결",
        dorg="한국거래소",
    )
    assert parsed is None
    # 106억 → v65에서 소규모 차단 (200억 미만)
    parsed_small = _contract_preflight_skip(
        "넥스틴, SK하이닉스와 106억 규모 공급계약 체결",
        ["공급계약"],
        ctx,
        raw_headline="넥스틴, SK하이닉스와 106억 규모 공급계약 체결",
        dorg="한국거래소",
    )
    assert parsed_small is not None
    assert parsed_small["confidence"] == 45


def test_contract_preflight_keeps_exchange_office_disclosure_headline():
    ctx = ContextCard(ret_today=0.5, ret_3d=0.5, adv_value_20d=50e9)
    parsed = _contract_preflight_skip(
        "삼성증권, 250억 규모 공급계약 체결",
        ["공급계약"],
        ctx,
        raw_headline="삼성증권, 250억 규모 공급계약 체결",
        dorg="유가증권시장본부",
    )
    assert parsed is None


def test_has_article_pattern_detects_raw_brokerage_framing():
    assert has_article_pattern(
        "삼성전자 추가 상승 여력 충분 장기공급계약 요구 큰 폭 증가",
        raw_headline='KB증권 "삼성전자, 추가 상승 여력 충분…장기공급계약 요구 큰 폭 증가"',
    ) is True


def test_fallback_decide_skips_brokerage_contract_commentary_from_raw_headline():
    cfg = Config(anthropic_api_key="test")
    engine = DecisionEngine(cfg)
    ctx = ContextCard(ret_today=0.5, ret_3d=0.5, adv_value_20d=50e9)

    record = engine.fallback_decide(
        ticker="005930",
        headline='KB증권 "삼성전자, 추가 상승 여력 충분…장기공급계약 요구 큰 폭 증가"',
        analysis_headline="삼성전자 추가 상승 여력 충분 장기공급계약 요구 큰 폭 증가",
        bucket=Bucket.POS_STRONG,
        ctx=ctx,
        keyword_hits=["장기 공급"],
        dorg="연합뉴스",
    )

    assert record.action == Action.SKIP
    assert record.reason == "rule_fallback:article_pattern"


# ── v82: CEO/인물 발언 하드블록 (따옴표 + 직급 패턴) ──

@pytest.mark.parametrize("headline,expected", [
    # 따옴표 패턴 — 인물 발언/인터뷰
    ("LG에너지솔루션 김동명 '올해 ESS 기회'", True),
    ("삼성전자 부회장 '반도체 투자 확대'", True),
    ('현대차 사장 "EV 전환 가속"', True),
    ("SK하이닉스 전무 '올해 HBM 2배'", True),
    # 직급 키워드 (따옴표 없어도)
    ("삼성SDI 부회장 간담회에서 투자 확대 언급", True),
    ("LG전자 사장 신년사 실적 자신감", True),
    ("SK이노 부사장 컨퍼런스 발표", True),
    ("현대모비스 상무 기술 브리핑", True),
    ("인터뷰 통해 공개된 신제품 로드맵", True),
    ("대표이사 밝혔다 내년 흑자전환", True),
    # 일반 공시 — article이 아님
    ("삼성전자 공급계약 체결 500억", False),
    ("LG화학 자기주식처분 결정", False),
])
def test_v82_article_pattern_ceo_speech_hardblock(headline, expected):
    """v82: 따옴표/직급/발언 패턴이 article_pattern으로 감지되어야 함."""
    assert has_article_pattern(headline) is expected


def test_v82_fallback_skips_ceo_quote_headline():
    """v82: CEO 발언 직급 헤드라인 → fallback_decide가 SKIP."""
    cfg = Config(anthropic_api_key="test")
    engine = DecisionEngine(cfg)
    ctx = ContextCard(ret_today=0.5, ret_3d=0.5, adv_value_20d=50e9)

    record = engine.fallback_decide(
        ticker="373220",
        headline="LG에너지솔루션 부회장 '올해 ESS 기회'",
        analysis_headline="LG에너지솔루션 부회장 올해 ESS 기회",
        bucket=Bucket.POS_STRONG,
        ctx=ctx,
        keyword_hits=["ESS"],
        dorg="연합뉴스",
    )

    assert record.action == Action.SKIP
    assert "article_pattern" in record.reason


def test_cache_key_changes_with_microstructure_context(tmp_path):
    cfg = Config(anthropic_api_key="test", llm_cache_dir=tmp_path / "llm_cache")
    engine = DecisionEngine(cfg)

    ctx1 = ContextCard(adv_value_20d=10e9, spread_bps=10.0, ret_today=5.0, intraday_value_vs_adv20d=0.01)
    ctx2 = ContextCard(adv_value_20d=10e9, spread_bps=10.0, ret_today=5.0, intraday_value_vs_adv20d=0.02)

    key1 = engine._cache_key("005930", "공급계약 체결", Bucket.POS_STRONG, ctx1)
    key2 = engine._cache_key("005930", "공급계약 체결", Bucket.POS_STRONG, ctx2)

    assert key1 != key2


async def test_cache_hit(tmp_path):
    cfg = Config(anthropic_api_key="test", llm_cache_dir=tmp_path / "llm_cache")
    engine = DecisionEngine(cfg)

    mock_client = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text='{"action":"BUY","confidence":80,"size_hint":"M","reason":"test"}')]
    mock_msg.usage = MagicMock(input_tokens=100, output_tokens=50)
    mock_client.messages.create = AsyncMock(return_value=mock_msg)
    engine._llm._anthropic_client = mock_client

    ctx = ContextCard()
    # First call
    r1 = await engine.decide("005930", "삼성전자", "공급계약 체결", Bucket.POS_STRONG, ctx, "09:00:00")
    # Second call (should be cached)
    r2 = await engine.decide("005930", "삼성전자", "공급계약 체결", Bucket.POS_STRONG, ctx, "09:00:01")

    assert r1 is not None
    assert r2 is not None
    assert r2.decision_source == "CACHE"
    assert mock_client.messages.create.call_count == 1


async def test_inflight_dedup_single_upstream_call(tmp_path):
    cfg = Config(anthropic_api_key="test", llm_cache_dir=tmp_path / "llm_cache")
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
    engine._llm._anthropic_client = mock_client

    ctx = ContextCard()
    r1, r2 = await asyncio.gather(
        engine.decide("005930", "삼성전자", "공급계약 체결", Bucket.POS_STRONG, ctx, "09:00:00"),
        engine.decide("005930", "삼성전자", "공급계약 체결", Bucket.POS_STRONG, ctx, "09:00:00"),
    )

    assert r1 is not None
    assert r2 is not None
    assert call_count["n"] == 1
    assert {r1.decision_source, r2.decision_source} == {"LLM", "CACHE"}


async def test_inflight_dedup_error_propagates_to_all_callers(tmp_path):
    cfg = Config(anthropic_api_key="test", llm_cache_dir=tmp_path / "llm_cache")
    engine = DecisionEngine(cfg)

    mock_client = AsyncMock()
    call_count = {"n": 0}

    async def _create(*args, **kwargs):
        call_count["n"] += 1
        await asyncio.sleep(0.05)
        raise RuntimeError("upstream failure")

    mock_client.messages.create = AsyncMock(side_effect=_create)
    engine._llm._anthropic_client = mock_client

    ctx = ContextCard()

    async def _call():
        with pytest.raises(LlmCallError):
            await engine.decide("005930", "삼성전자", "공급계약 체결", Bucket.POS_STRONG, ctx, "09:00:00")

    await asyncio.gather(_call(), _call())
    assert call_count["n"] == 3  # 3 attempts with exponential backoff (all fail)


async def test_llm_timeout_raises(tmp_path):
    cfg = Config(anthropic_api_key="test", llm_wait_for_s=0.01, llm_cache_dir=tmp_path / "llm_cache")
    engine = DecisionEngine(cfg)

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(side_effect=asyncio.TimeoutError())
    engine._llm._anthropic_client = mock_client

    ctx = ContextCard()
    with pytest.raises(LlmTimeoutError):
        await engine.decide("005930", "삼성전자", "공급계약 체결", Bucket.POS_STRONG, ctx, "09:00:00")


async def test_llm_call_error_raises(tmp_path):
    cfg = Config(anthropic_api_key="test", llm_cache_dir=tmp_path / "llm_cache")
    engine = DecisionEngine(cfg)

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(side_effect=RuntimeError("503"))
    engine._llm._anthropic_client = mock_client

    ctx = ContextCard()
    with pytest.raises(LlmCallError):
        await engine.decide("005930", "삼성전자", "공급계약 체결", Bucket.POS_STRONG, ctx, "09:00:00")


async def test_llm_bad_response_structure_raises(tmp_path):
    cfg = Config(anthropic_api_key="test", llm_cache_dir=tmp_path / "llm_cache")
    engine = DecisionEngine(cfg)

    mock_client = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.content = []  # Empty content list
    mock_client.messages.create = AsyncMock(return_value=mock_msg)
    engine._llm._anthropic_client = mock_client

    ctx = ContextCard()
    with pytest.raises(LlmCallError):
        await engine.decide("005930", "삼성전자", "공급계약 체결", Bucket.POS_STRONG, ctx, "09:00:00")


async def test_llm_invalid_json_raises(tmp_path):
    cfg = Config(anthropic_api_key="test", llm_cache_dir=tmp_path / "llm_cache")
    engine = DecisionEngine(cfg)

    mock_client = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="not json")]
    mock_client.messages.create = AsyncMock(return_value=mock_msg)
    engine._llm._anthropic_client = mock_client

    ctx = ContextCard()
    with pytest.raises(LlmParseError):
        await engine.decide("005930", "삼성전자", "공급계약 체결", Bucket.POS_STRONG, ctx, "09:00:00")


async def test_contract_article_preflight_skips_without_llm_call():
    cfg = Config(anthropic_api_key="test")
    engine = DecisionEngine(cfg)

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock()
    engine._llm._anthropic_client = mock_client

    ctx = ContextCard(ret_today=1.2, ret_3d=7.3, adv_value_20d=132_310_000_000)
    result = await engine.decide(
        "298040",
        "효성중공업",
        "‘파죽지세’ K전력기기…효성중공업, 美·유럽 이어 호주서 ESS 수주",
        Bucket.POS_STRONG,
        ctx,
        "10:18:27",
        keyword_hits=["수주"],
    )

    assert result.action == Action.SKIP
    assert result.decision_source == "RULE_PREFLIGHT"
    assert "contract_article" in result.reason
    assert mock_client.messages.create.call_count == 0


async def test_incremental_order_preflight_skips_without_llm_call():
    cfg = Config(anthropic_api_key="test")
    engine = DecisionEngine(cfg)

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock()
    engine._llm._anthropic_client = mock_client

    ctx = ContextCard(ret_today=0.6, ret_3d=-2.8, adv_value_20d=50_370_000_000)
    result = await engine.decide(
        "439260",
        "대한조선",
        "대한조선, 수에즈막스 원유운반선 1척 추가 수주",
        Bucket.POS_STRONG,
        ctx,
        "09:17:00",
        keyword_hits=["수주"],
    )

    assert result.action == Action.SKIP
    assert result.decision_source == "RULE_PREFLIGHT"
    assert "contract_incremental" in result.reason
    assert mock_client.messages.create.call_count == 0


async def test_contract_downtrend_preflight_skips_without_llm_call():
    """소규모 수주 + 하락장 → preflight SKIP."""
    cfg = Config(anthropic_api_key="test")
    engine = DecisionEngine(cfg)

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock()
    engine._llm._anthropic_client = mock_client

    ctx = ContextCard(ret_today=0.3, ret_3d=-9.0, adv_value_20d=103_090_000_000)
    result = await engine.decide(
        "003670",
        "포스코퓨처엠",
        "포스코퓨처엠 수주공시 - 이차전지용 인조흑연 음극재 공급",
        Bucket.POS_STRONG,
        ctx,
        "09:06:00",
        keyword_hits=["수주"],
    )

    assert result.action == Action.SKIP
    assert result.decision_source == "RULE_PREFLIGHT"
    assert "contract_downtrend" in result.reason
    assert mock_client.messages.create.call_count == 0


async def test_contract_downtrend_large_contract_bypasses_preflight():
    """대형 계약(1조+) + 하락장 → preflight 바이패스, LLM 판단 허용."""
    from kindshot.decision import _contract_preflight_skip

    ctx = ContextCard(ret_today=0.3, ret_3d=-9.0, adv_value_20d=103_090_000_000)
    result = _contract_preflight_skip(
        "포스코퓨처엠 수주공시 - 이차전지용 인조흑연 음극재 공급 1.01조",
        ["수주"],
        ctx,
    )
    # 대형 계약이므로 preflight가 None 반환 (LLM으로 넘김)
    assert result is None


async def test_contract_large_cap_large_amount_bypasses_preflight():
    """대형주(ADV 3811억) + 대형계약(1.5조) → preflight 바이패스, LLM 판단."""
    from kindshot.decision import _contract_preflight_skip

    ctx = ContextCard(ret_today=0.3, ret_3d=-2.5, adv_value_20d=381_140_000_000)
    result = _contract_preflight_skip(
        "삼성SDI, 美 에너지 기업과 1.5조 규모 ESS 공급 계약 체결",
        ["공급 계약"],
        ctx,
    )
    # 대형 계약(1.5조)이므로 preflight가 None 반환 (LLM으로 넘김)
    assert result is None


async def test_contract_large_cap_small_amount_preflight_skips():
    """대형주(ADV 3811억) + 소형계약(200억) → preflight SKIP."""
    from kindshot.decision import _contract_preflight_skip

    ctx = ContextCard(ret_today=0.3, ret_3d=-2.5, adv_value_20d=381_140_000_000)
    result = _contract_preflight_skip(
        "삼성SDI, 200억 규모 공급 계약 체결",
        ["공급 계약"],
        ctx,
    )
    assert result is not None
    assert result["confidence"] == 45
    assert "contract_large_cap" in result["reason"]


async def test_normal_contract_still_calls_llm_when_preflight_clean(tmp_path):
    cfg = Config(anthropic_api_key="test", llm_cache_dir=tmp_path / "llm_cache")
    engine = DecisionEngine(cfg)

    mock_client = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text='{"action":"BUY","confidence":80,"size_hint":"M","reason":"large confirmed contract"}')]
    mock_msg.usage = MagicMock(input_tokens=100, output_tokens=50)
    mock_client.messages.create = AsyncMock(return_value=mock_msg)
    engine._llm._anthropic_client = mock_client

    ctx = ContextCard(ret_today=-2.5, ret_3d=-2.0, adv_value_20d=156_420_000_000)
    result = await engine.decide(
        "329180",
        "HD현대중공업",
        "HD현대중공업, 8,237억원 규모 공급계약(컨테이너선 10척) 체결",
        Bucket.POS_STRONG,
        ctx,
        "09:11:00",
        keyword_hits=["공급계약"],
    )

    assert result.action == Action.BUY
    assert result.decision_source == "LLM"
    assert mock_client.messages.create.call_count == 1


async def test_decision_engine_memory_cache_reuses_equivalent_prompt(tmp_path):
    cfg = Config(
        anthropic_api_key="test",
        llm_provider="anthropic",
        llm_cache_dir=tmp_path / "llm_cache",
    )
    engine = DecisionEngine(cfg)

    mock_client = MagicMock()
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text='{"action":"BUY","confidence":80,"size_hint":"M","reason":"cached"}')]
    mock_client.messages.create = AsyncMock(return_value=mock_msg)
    engine._llm._anthropic_client = mock_client

    ctx = ContextCard(ret_today=0.2, adv_value_20d=120_000_000_000)
    first = await engine.decide("005930", "삼성전자", "공급계약 체결", Bucket.POS_STRONG, ctx, "09:00:00")
    second = await engine.decide("005930", "삼성전자", "공급계약 체결", Bucket.POS_STRONG, ctx, "09:00:00")

    assert first.decision_source == "LLM"
    assert second.decision_source == "CACHE"
    assert second.cache_layer == "memory"
    assert mock_client.messages.create.await_count == 1
    assert engine.cache_stats()["memory_hits"] == 1


async def test_decision_engine_disk_cache_survives_new_engine(tmp_path):
    cfg = Config(
        anthropic_api_key="test",
        llm_provider="anthropic",
        llm_cache_dir=tmp_path / "llm_cache",
    )
    first_engine = DecisionEngine(cfg)

    first_client = MagicMock()
    first_msg = MagicMock()
    first_msg.content = [MagicMock(text='{"action":"BUY","confidence":81,"size_hint":"M","reason":"disk cache"}')]
    first_client.messages.create = AsyncMock(return_value=first_msg)
    first_engine._llm._anthropic_client = first_client

    ctx = ContextCard(ret_today=0.2, adv_value_20d=120_000_000_000)
    initial = await first_engine.decide("005930", "삼성전자", "공급계약 체결", Bucket.POS_STRONG, ctx, "09:00:00")

    second_engine = DecisionEngine(cfg)
    second_client = MagicMock()
    second_client.messages.create = AsyncMock(side_effect=AssertionError("disk cache should satisfy the request"))
    second_engine._llm._anthropic_client = second_client

    cached = await second_engine.decide("005930", "삼성전자", "공급계약 체결", Bucket.POS_STRONG, ctx, "09:00:00")

    assert initial.decision_source == "LLM"
    assert cached.decision_source == "CACHE"
    assert cached.cache_layer == "disk"
    assert second_engine.cache_stats()["disk_hits"] == 1
    assert second_client.messages.create.await_count == 0


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


@pytest.mark.asyncio
async def test_small_contract_preflight_skip():
    """소규모 계약(<100억) → preflight SKIP."""
    cfg = Config(anthropic_api_key="test")
    engine = DecisionEngine(cfg)

    mock_client = AsyncMock()
    engine._llm._anthropic_client = mock_client

    ctx = ContextCard(ret_today=0.5, ret_3d=1.0, adv_value_20d=80_000_000_000)
    result = await engine.decide(
        "123456",
        "테스트주",
        "테스트주, 50억원 규모 공급계약 체결",
        Bucket.POS_STRONG,
        ctx,
        "10:00:00",
        keyword_hits=["공급계약"],
    )

    assert result.action == Action.SKIP
    assert result.decision_source == "RULE_PREFLIGHT"
    assert "small_contract" in result.reason
    assert mock_client.messages.create.call_count == 0


@pytest.mark.asyncio
async def test_large_contract_passes_preflight(tmp_path):
    """대형 계약(500억+) → preflight 통과 → LLM 호출."""
    cfg = Config(anthropic_api_key="test", llm_cache_dir=tmp_path / "llm_cache")
    engine = DecisionEngine(cfg)

    mock_client = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text='{"action":"BUY","confidence":80,"size_hint":"M","reason":"large contract"}')]
    mock_msg.usage = MagicMock(input_tokens=100, output_tokens=50)
    mock_client.messages.create = AsyncMock(return_value=mock_msg)
    engine._llm._anthropic_client = mock_client

    ctx = ContextCard(ret_today=0.5, ret_3d=1.0, adv_value_20d=80_000_000_000)
    result = await engine.decide(
        "123456",
        "테스트주",
        "테스트주, 500억원 규모 공급계약 체결",
        Bucket.POS_STRONG,
        ctx,
        "10:00:00",
        keyword_hits=["공급계약"],
    )

    assert result.decision_source == "LLM"
    assert mock_client.messages.create.call_count == 1
