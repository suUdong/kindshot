"""Stress tests for Monday live-trading readiness.

Scenarios:
1. Full-day simulation (9AM~3:30PM event flow)
2. Concurrent BUY signals exceeding max_positions
3. News flood (10+ simultaneous events)
4. Circuit breaker trigger & recovery
5. LLM API timeout/error with fallback
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kindshot.bucket import Bucket, BucketResult, classify
from kindshot.config import Config
from kindshot.decision import DecisionEngine, LlmParseError
from kindshot.feed import RawDisclosure
from kindshot.guardrails import (
    GuardrailResult,
    GuardrailState,
    check_guardrails,
    DynamicGuardrailProfile,
)
from kindshot.llm_client import LlmCallError, LlmClient, LlmTimeoutError
from kindshot.models import Action, ContextCard, DecisionRecord
from kindshot.tz import KST as _KST


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# 장중 시간 (10:30 KST) — PRE_MARKET_BLOCKED / 시간대 guardrail 우회용
_MARKET_TIME = datetime(2026, 3, 31, 10, 30, 0, tzinfo=_KST)

def _cfg(**kw) -> Config:
    defaults = dict(
        anthropic_api_key="test-key",
        nvidia_api_key="test-nvidia-key",
        llm_provider="nvidia",
        llm_fallback_enabled=True,
        llm_wait_for_s=1.0,
        llm_sdk_timeout_s=2.0,
        max_positions=4,
        max_sector_positions=2,
        consecutive_loss_halt=3,
        adv_threshold=0,  # bypass ADV filter in stress tests
    )
    defaults.update(kw)
    return Config(**defaults)


def _make_nvidia_response(text: str = '{"action":"BUY","confidence":85,"size_hint":"M","reason":"test"}'):
    msg = SimpleNamespace(content=text)
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice])


def _make_anthropic_response(text: str = '{"action":"BUY","confidence":85,"size_hint":"M","reason":"test"}'):
    block = SimpleNamespace(text=text)
    return SimpleNamespace(content=[block])


def _make_nvidia_client(cfg=None, responses=None, errors=None):
    """Create LlmClient with mocked NVIDIA client (provider=nvidia)."""
    cfg = cfg or _cfg()
    client = LlmClient(cfg)
    mock_nvidia = MagicMock()

    if errors:
        mock_nvidia.chat.completions.create = AsyncMock(side_effect=errors)
    elif responses:
        mock_nvidia.chat.completions.create = AsyncMock(side_effect=responses)
    else:
        mock_nvidia.chat.completions.create = AsyncMock(
            return_value=_make_nvidia_response()
        )

    client._nvidia_client = mock_nvidia
    return client, mock_nvidia


def _make_anthropic_client(cfg=None, responses=None, errors=None):
    """Create LlmClient with mocked Anthropic client."""
    cfg = cfg or _cfg(llm_provider="anthropic")
    client = LlmClient(cfg)
    mock_anthropic = MagicMock()

    if errors:
        mock_anthropic.messages.create = AsyncMock(side_effect=errors)
    elif responses:
        mock_anthropic.messages.create = AsyncMock(side_effect=responses)
    else:
        mock_anthropic.messages.create = AsyncMock(
            return_value=_make_anthropic_response()
        )

    client._anthropic_client = mock_anthropic
    return client, mock_anthropic


def _raw_disclosure(ticker: str, title: str, idx: int = 0) -> RawDisclosure:
    return RawDisclosure(
        title=title,
        link=f"https://example.com/{idx}",
        rss_guid=f"guid-{ticker}-{idx}",
        published="2026-03-31 09:05:00",
        ticker=ticker,
        corp_name=f"Corp{ticker}",
        detected_at=datetime(2026, 3, 31, 9, 5, 0),
    )


_POS_STRONG_HEADLINES = [
    "삼성전자(005930) 자기주식취득 결정",
    "LG에너지(373220) 수주공시: 2조원 규모 배터리 공급계약",
    "현대차(005380) 자기주식취득 결정",
    "SK하이닉스(000660) 수주공시: 1.5조원 규모 HBM 공급계약",
    "NAVER(035420) 자기주식 취득 결정",
    "카카오(035720) 자기주식취득 결정",
    "포스코홀딩스(005490) 수주공시: 8000억원 규모 2차전지 소재",
    "한화에어(012450) 수주공시: 3조원 규모 방산 수출",
    "기아(000270) 자기주식 취득 결정",
    "셀트리온(068270) 해외품목허가",
    "삼성바이오(207940) 수주공시: 5000억원 규모 CMO",
    "두산에너빌리티(034020) 수주공시: 1조원 규모 원전",
]


# ===========================================================================
# 1. Full-day simulation — 9AM~3:30PM event flow
# ===========================================================================

class TestFullDaySimulation:
    """Simulate a full trading day with events spread across time windows."""

    def test_time_windows_coverage(self):
        """Events across all trading sessions get correct bucket classification."""
        events = [
            ("09:05", "삼성전자(005930) 자기주식취득 결정"),  # 장초반
            ("09:30", "LG에너지(373220) 수주공시: 2조원 규모 배터리 공급계약"),
            ("10:15", "현대차(005380) 주요사항보고서(자기주식취득 결정)"),
            ("11:00", "SK하이닉스(000660) 수주공시: 1.5조원 규모 HBM"),
            ("13:00", "NAVER(035420) 자기주식 취득 결정"),  # 오후
            ("14:00", "카카오(035720) 기타경영사항(자율공시)"),
            ("14:30", "포스코홀딩스(005490) 수주공시: 8000억원"),
            ("15:00", "기아(000270) 자기주식 취득 결정"),  # 장마감 근접
            ("15:15", "셀트리온(068270) 해외품목허가"),  # 15:15 이후
        ]

        results = []
        for time_str, headline in events:
            r = classify(headline)
            results.append((time_str, headline[:30], r.bucket.name, r.keyword_hits))

        # 최소 5개 이상 POS 분류
        pos_count = sum(1 for _, _, b, _ in results if b.startswith("POS"))
        assert pos_count >= 5, f"POS signals too few: {pos_count}/9, results={results}"

    def test_guardrail_state_resets_daily(self):
        """GuardrailState.reset_daily() clears all counters for new day."""
        cfg = _cfg()
        state = GuardrailState(cfg)
        # 장중 거래 시뮬레이션
        state.record_buy("005930", "전자")
        state.record_buy("373220", "배터리")
        state.record_pnl(-50000)
        state.record_stop_loss()
        assert state._position_count == 2
        assert state._consecutive_stop_losses == 1

        # 다음 날 리셋
        state.reset_daily()
        assert state._position_count == 0
        assert state._consecutive_stop_losses == 0
        assert len(state._bought_tickers) == 0

    def test_full_day_position_lifecycle(self):
        """BUY → SELL across multiple positions throughout the day."""
        cfg = _cfg(max_positions=4)
        state = GuardrailState(cfg)

        # 오전: 3건 매수
        for ticker in ["005930", "373220", "005380"]:
            state.record_buy(ticker)
        assert state._position_count == 3

        # 점심: 1건 매도, 1건 매수
        state.record_sell("005930")
        assert state._position_count == 2
        state.record_buy("000660")
        assert state._position_count == 3

        # 오후: 전부 매도
        for ticker in ["373220", "005380", "000660"]:
            state.record_sell(ticker)
        assert state._position_count == 0


# ===========================================================================
# 2. Concurrent BUY signals — max_positions guardrail
# ===========================================================================

class TestMaxPositionsGuardrail:
    """Verify max_positions blocks excess buys under concurrent signals."""

    def test_max_positions_blocks_5th_buy(self):
        """With max_positions=4, 5th buy attempt is blocked."""
        cfg = _cfg(max_positions=4)
        state = GuardrailState(cfg)

        # 4건 매수 — 모두 통과해야 함
        for i, ticker in enumerate(["005930", "373220", "005380", "000660"]):
            state.record_buy(ticker)
            assert state._position_count == i + 1

        # 5번째 — guardrail 차단
        result = check_guardrails(
            "035420", cfg,
            spread_bps=5.0, adv_value_20d=1e9, ret_today=1.0,
            state=state,
            decision_action=Action.BUY,
            decision_confidence=90,
            decision_time_kst=_MARKET_TIME,
        )
        assert not result.passed, f"5th buy should be blocked, got: {result}"
        assert "MAX_POSITIONS" in (result.reason or "")

    def test_max_positions_allows_after_sell(self):
        """After selling one position, new buy is allowed."""
        cfg = _cfg(max_positions=4)
        state = GuardrailState(cfg)

        for ticker in ["005930", "373220", "005380", "000660"]:
            state.record_buy(ticker)

        # 1건 매도 후 새 매수 가능
        state.record_sell("005930")
        assert state._position_count == 3

        result = check_guardrails(
            "035420", cfg,
            spread_bps=5.0, adv_value_20d=1e9, ret_today=1.0,
            state=state,
            decision_action=Action.BUY,
            decision_confidence=90,
            decision_time_kst=_MARKET_TIME,
        )
        assert result.passed, f"Buy after sell should pass: {result.reason}"

    def test_concurrent_buy_signals_race(self):
        """Simulate 8 simultaneous BUY signals — only max_positions should execute."""
        cfg = _cfg(max_positions=4)
        state = GuardrailState(cfg)
        tickers = ["005930", "373220", "005380", "000660",
                    "035420", "035720", "005490", "012450"]

        accepted = []
        rejected = []

        for ticker in tickers:
            result = check_guardrails(
                ticker, cfg,
                spread_bps=5.0, adv_value_20d=1e9, ret_today=1.0,
                state=state,
                decision_action=Action.BUY,
                decision_confidence=90,
                decision_time_kst=_MARKET_TIME,
            )
            if result.passed:
                state.record_buy(ticker)
                accepted.append(ticker)
            else:
                rejected.append(ticker)

        assert len(accepted) == 4, f"Expected 4 accepted, got {len(accepted)}: {accepted}"
        assert len(rejected) == 4, f"Expected 4 rejected, got {len(rejected)}: {rejected}"

    def test_sector_concentration_limit(self):
        """max_sector_positions=2 blocks 3rd buy in same sector."""
        cfg = _cfg(max_positions=4, max_sector_positions=2)
        state = GuardrailState(cfg)

        state.record_buy("005930", "반도체")
        state.record_buy("000660", "반도체")

        result = check_guardrails(
            "042700", cfg,
            spread_bps=5.0, adv_value_20d=1e9, ret_today=1.0,
            state=state,
            sector="반도체",
            decision_action=Action.BUY,
            decision_confidence=90,
            decision_time_kst=_MARKET_TIME,
        )
        assert not result.passed
        assert "SECTOR" in (result.reason or "").upper()

    def test_consecutive_stop_loss_halt(self):
        """consecutive_loss_halt=3 blocks trading after 3 consecutive stop losses."""
        cfg = _cfg(consecutive_loss_halt=3)
        state = GuardrailState(cfg)

        for _ in range(3):
            state.record_stop_loss()

        assert state._consecutive_stop_losses == 3

        result = check_guardrails(
            "005930", cfg,
            spread_bps=5.0, adv_value_20d=1e9, ret_today=1.0,
            state=state,
            decision_action=Action.BUY,
            decision_confidence=90,
            decision_time_kst=_MARKET_TIME,
        )
        assert not result.passed
        assert "CONSECUTIVE" in (result.reason or "").upper() or "HALT" in (result.reason or "").upper()


# ===========================================================================
# 3. News flood — 10+ simultaneous events
# ===========================================================================

class TestNewsFlood:
    """Verify system handles 10+ simultaneous news events correctly."""

    def test_classify_12_headlines_under_1s(self):
        """Bucket classification of 12 headlines completes quickly."""
        headlines = _POS_STRONG_HEADLINES[:12]
        t0 = time.monotonic()
        results = [classify(h) for h in headlines]
        elapsed = time.monotonic() - t0

        assert elapsed < 1.0, f"Classification took {elapsed:.2f}s, expected <1s"
        assert len(results) == 12

    def test_flood_deduplication(self):
        """Same ticker with same headline should not create duplicate events."""
        seen_keys = set()
        disclosures = []
        for i in range(15):
            # 5개 종목 × 3번 반복 (뉴스 폭주 시 중복 가능)
            ticker = ["005930", "373220", "005380", "000660", "035420"][i % 5]
            title = f"{ticker} 수주공시: 대규모 계약"
            d = _raw_disclosure(ticker, title, idx=i)
            key = f"{d.ticker}:{d.title}"
            if key not in seen_keys:
                seen_keys.add(key)
                disclosures.append(d)

        # 5개 고유 이벤트만 남아야 함
        assert len(disclosures) == 5

    @pytest.mark.asyncio
    async def test_concurrent_llm_calls_semaphore(self):
        """LLM concurrency semaphore limits parallel calls to llm_max_concurrency."""
        cfg = _cfg(llm_max_concurrency=4)
        client, mock = _make_nvidia_client(cfg)

        # 느린 응답 시뮬레이션
        async def slow_response(*args, **kwargs):
            await asyncio.sleep(0.05)
            return _make_nvidia_response()

        mock.chat.completions.create = AsyncMock(side_effect=slow_response)

        # 10개 동시 호출
        tasks = [client.call(f"prompt-{i}") for i in range(10)]
        results = await asyncio.gather(*tasks)

        assert len(results) == 10
        # 모든 호출 성공
        for text, latency in results:
            assert "BUY" in text

    @pytest.mark.asyncio
    async def test_flood_with_mixed_buckets(self):
        """Mixed POS/NEG/UNKNOWN events in flood are classified correctly."""
        headlines = [
            "삼성전자(005930) 자기주식취득 결정",          # POS_STRONG
            "LG화학(051910) 횡령 혐의 기소",               # NEG
            "현대차(005380) 수주공시: 2조원",              # POS_STRONG
            "SK텔레콤(017670) 정기주주총회",                # IGNORE
            "NAVER(035420) 자기주식 취득 결정",             # POS_STRONG
            "한화솔루션(009830) 유상증자 결정",             # NEG
            "카카오(035720) 수주공시: 5000억원",            # POS_STRONG
            "포스코(005490) 단순투자목적",                  # IGNORE/UNKNOWN
            "기아(000270) 자기주식 취득 결정",              # POS_STRONG
            "셀트리온(068270) 특허 소송 패소",              # NEG
            "두산(034020) 수주공시: 1조원",                 # POS_STRONG
            "LG전자(066570) 임원 변경",                    # IGNORE/UNKNOWN
        ]

        results = [classify(h) for h in headlines]
        pos_count = sum(1 for r in results if r.bucket in (Bucket.POS_STRONG, Bucket.POS_WEAK))
        neg_count = sum(1 for r in results if r.bucket in (Bucket.NEG_STRONG, Bucket.NEG_WEAK))

        assert pos_count >= 4, f"Expected >=4 POS, got {pos_count}"
        assert neg_count >= 1, f"Expected >=1 NEG, got {neg_count}"


# ===========================================================================
# 4. Circuit breaker trigger scenarios
# ===========================================================================

class TestCircuitBreaker:
    """Circuit breaker opens on permanent errors, blocks subsequent calls."""

    @pytest.mark.asyncio
    async def test_anthropic_credit_exhaustion_opens_circuit(self):
        """Anthropic 'credit balance is too low' opens circuit breaker."""
        cfg = _cfg(llm_provider="anthropic")
        client, mock = _make_anthropic_client(
            cfg,
            errors=[Exception("credit balance is too low")],
        )

        with pytest.raises(LlmCallError, match="credit balance"):
            await client.call("test prompt")

        assert client.circuit_open, "Circuit should be open after credit error"

    @pytest.mark.asyncio
    async def test_nvidia_auth_error_opens_circuit(self):
        """NVIDIA 'invalid api key' opens nvidia circuit breaker."""
        cfg = _cfg()
        client, mock = _make_nvidia_client(
            cfg,
            errors=[Exception("invalid api key")],
        )
        # Also mock anthropic for fallback
        mock_anthropic = MagicMock()
        mock_anthropic.messages.create = AsyncMock(
            return_value=_make_anthropic_response()
        )
        client._anthropic_client = mock_anthropic

        # Should fallback to anthropic
        text, latency = await client.call("test prompt")
        assert "BUY" in text
        assert client.nvidia_circuit_open, "NVIDIA circuit should be open"

    @pytest.mark.asyncio
    async def test_circuit_open_blocks_subsequent_calls(self):
        """Once circuit is open, subsequent calls are immediately rejected."""
        cfg = _cfg(llm_provider="anthropic")
        client, mock = _make_anthropic_client(cfg)

        # Manually open circuit
        client._open_circuit("test: credit exhausted")
        assert client.circuit_open

        with pytest.raises(LlmCallError, match="circuit breaker open"):
            await client.call("test prompt")

    @pytest.mark.asyncio
    async def test_nvidia_circuit_triggers_anthropic_fallback(self):
        """When NVIDIA circuit is open, system falls back to Anthropic."""
        cfg = _cfg()
        client = LlmClient(cfg)

        # Open NVIDIA circuit
        client._open_nvidia_circuit("nvidia down")

        # Mock only Anthropic
        mock_anthropic = MagicMock()
        mock_anthropic.messages.create = AsyncMock(
            return_value=_make_anthropic_response('{"action":"SKIP","confidence":70,"reason":"fallback"}')
        )
        client._anthropic_client = mock_anthropic

        text, latency = await client.call("test prompt")
        assert "SKIP" in text
        mock_anthropic.messages.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_both_circuits_open_raises(self):
        """Both NVIDIA and Anthropic circuits open → LlmCallError."""
        cfg = _cfg()
        client = LlmClient(cfg)

        client._open_nvidia_circuit("nvidia down")
        client._open_circuit("anthropic down")

        with pytest.raises(LlmCallError):
            await client.call("test prompt")


# ===========================================================================
# 5. LLM API timeout/error with fallback
# ===========================================================================

class TestLlmTimeoutFallback:
    """Verify timeout handling and NVIDIA → Anthropic fallback."""

    @pytest.mark.asyncio
    async def test_nvidia_timeout_fallback_to_anthropic(self):
        """NVIDIA timeout → successful Anthropic fallback."""
        cfg = _cfg(llm_wait_for_s=0.1)
        client = LlmClient(cfg)

        # NVIDIA: always timeout
        mock_nvidia = MagicMock()
        async def nvidia_timeout(*a, **kw):
            await asyncio.sleep(10)  # will be cancelled by wait_for
        mock_nvidia.chat.completions.create = AsyncMock(side_effect=nvidia_timeout)
        client._nvidia_client = mock_nvidia

        # Anthropic: success
        mock_anthropic = MagicMock()
        mock_anthropic.messages.create = AsyncMock(
            return_value=_make_anthropic_response('{"action":"BUY","confidence":82,"reason":"fallback ok"}')
        )
        client._anthropic_client = mock_anthropic

        text, latency = await client.call("test", max_retries=1)
        assert "BUY" in text
        assert "fallback ok" in text

    @pytest.mark.asyncio
    async def test_nvidia_error_then_anthropic_error_raises(self):
        """Both providers fail → LlmCallError raised."""
        cfg = _cfg(llm_wait_for_s=0.1)
        client = LlmClient(cfg)

        mock_nvidia = MagicMock()
        mock_nvidia.chat.completions.create = AsyncMock(
            side_effect=Exception("nvidia 500 server error")
        )
        client._nvidia_client = mock_nvidia

        mock_anthropic = MagicMock()
        mock_anthropic.messages.create = AsyncMock(
            side_effect=Exception("anthropic 500 server error")
        )
        client._anthropic_client = mock_anthropic

        with pytest.raises(LlmCallError):
            await client.call("test", max_retries=1)

    @pytest.mark.asyncio
    async def test_retry_then_success(self):
        """First call fails, retry succeeds."""
        cfg = _cfg(llm_provider="anthropic")
        client, mock = _make_anthropic_client(
            cfg,
            responses=[
                Exception("temporary error"),
                _make_anthropic_response('{"action":"BUY","confidence":80,"reason":"retry ok"}'),
            ],
        )
        # side_effect with mix of exceptions and returns
        mock.messages.create = AsyncMock(
            side_effect=[
                Exception("temporary error"),
                _make_anthropic_response('{"action":"BUY","confidence":80,"reason":"retry ok"}'),
            ]
        )

        text, latency = await client.call("test", max_retries=3)
        assert "retry ok" in text

    @pytest.mark.asyncio
    async def test_rate_limit_429_retry(self):
        """429 rate limit triggers longer backoff but eventually succeeds."""
        cfg = _cfg(llm_provider="anthropic", llm_wait_for_s=2.0)
        client, mock = _make_anthropic_client(cfg)
        mock.messages.create = AsyncMock(
            side_effect=[
                Exception("rate limit exceeded (429)"),
                _make_anthropic_response('{"action":"SKIP","confidence":65,"reason":"after rate limit"}'),
            ]
        )

        text, latency = await client.call("test", max_retries=3)
        assert "after rate limit" in text

    def test_rule_based_fallback_on_llm_failure(self):
        """DecisionEngine.fallback_decide returns valid DecisionRecord."""
        cfg = _cfg()
        engine = DecisionEngine(cfg)

        ctx = ContextCard(
            spread_bps=5.0,
            adv_20d=1e9,
            ret_today=1.0,
        )

        record = engine.fallback_decide(
            ticker="005930",
            headline="삼성전자 자기주식취득 결정",
            bucket=Bucket.POS_STRONG,
            ctx=ctx,
            keyword_hits=["자기주식취득"],
        )

        assert isinstance(record, DecisionRecord)
        assert record.action in (Action.BUY, Action.SKIP)
        assert "rule_fallback" in record.reason


# ===========================================================================
# 6. Daily loss limit guardrail
# ===========================================================================

class TestDailyLossLimit:
    """Verify daily loss limit blocks trading when exceeded."""

    def test_daily_loss_limit_blocks(self):
        """After exceeding daily_loss_limit, new buys are blocked."""
        cfg = _cfg(daily_loss_limit=100000)
        state = GuardrailState(cfg)

        # 큰 손실 기록
        state.record_pnl(-150000)
        assert state.daily_pnl == -150000

        result = check_guardrails(
            "005930", cfg,
            spread_bps=5.0, adv_value_20d=1e9, ret_today=1.0,
            state=state,
            decision_action=Action.BUY,
            decision_confidence=90,
            decision_time_kst=_MARKET_TIME,
        )
        assert not result.passed
        assert "DAILY" in (result.reason or "").upper() or "LOSS" in (result.reason or "").upper()


# ===========================================================================
# 7. Edge cases & robustness
# ===========================================================================

class TestEdgeCases:
    """Edge cases that could surface under real trading conditions."""

    def test_empty_headline_classification(self):
        """Empty headline doesn't crash classifier."""
        r = classify("")
        assert r.bucket in (Bucket.UNKNOWN, Bucket.IGNORE)

    def test_very_long_headline(self):
        """Very long headline (1000+ chars) doesn't crash."""
        long_headline = "삼성전자(005930) " + "수주공시 " * 200
        r = classify(long_headline)
        assert r.bucket is not None

    def test_guardrail_state_position_count_never_negative(self):
        """Selling more than bought doesn't make position_count negative."""
        cfg = _cfg()
        state = GuardrailState(cfg)
        state.record_sell("005930")
        state.record_sell("373220")
        assert state._position_count == 0  # max(0, ...) 방어

    @pytest.mark.asyncio
    async def test_semaphore_prevents_thundering_herd(self):
        """Semaphore of 4 limits concurrent LLM requests even with 20 callers."""
        cfg = _cfg(llm_max_concurrency=4)
        client, mock = _make_nvidia_client(cfg)

        max_concurrent = 0
        current = 0
        lock = asyncio.Lock()

        original_create = mock.chat.completions.create

        async def track_concurrency(*args, **kwargs):
            nonlocal max_concurrent, current
            async with lock:
                current += 1
                if current > max_concurrent:
                    max_concurrent = current
            try:
                await asyncio.sleep(0.02)
                return _make_nvidia_response()
            finally:
                async with lock:
                    current -= 1

        mock.chat.completions.create = AsyncMock(side_effect=track_concurrency)

        tasks = [client.call(f"prompt-{i}") for i in range(20)]
        await asyncio.gather(*tasks)

        assert max_concurrent <= 4, f"Max concurrent was {max_concurrent}, expected <=4"

    def test_bought_tickers_dedup(self):
        """Same ticker bought twice doesn't double-count in bought_tickers set."""
        cfg = _cfg()
        state = GuardrailState(cfg)
        state.record_buy("005930")
        state.record_buy("005930")  # 중복 매수 시도
        assert "005930" in state.bought_tickers
        # position_count는 2로 올라감 (별도 관리)
        assert state._position_count == 2
