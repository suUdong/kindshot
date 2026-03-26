"""Tests for llm_client.py — NVIDIA NIM, Anthropic fallback, retry, circuit breaker."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kindshot.config import Config
from kindshot.llm_client import LlmClient, LlmCallError, LlmTimeoutError


# ── Helpers ──


def _cfg(**kw) -> Config:
    defaults = dict(anthropic_api_key="test-key", llm_provider="anthropic",
                    llm_wait_for_s=1.0, llm_sdk_timeout_s=2.0)
    defaults.update(kw)
    return Config(**defaults)


def _nvidia_cfg(**kw) -> Config:
    defaults = dict(nvidia_api_key="nvda-key", llm_provider="nvidia",
                    llm_wait_for_s=1.0, llm_sdk_timeout_s=2.0)
    defaults.update(kw)
    return Config(**defaults)


def _make_anthropic_response(text: str = '{"action":"BUY"}'):
    """Create a mock Anthropic response object."""
    block = SimpleNamespace(text=text)
    return SimpleNamespace(content=[block])


def _make_nvidia_response(text: str = '{"action":"BUY"}'):
    """Create a mock NVIDIA/OpenAI response object."""
    msg = SimpleNamespace(content=text)
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice])


def _make_client(cfg=None, responses=None, errors=None):
    """Create LlmClient with mocked Anthropic client (provider=anthropic)."""
    cfg = cfg or _cfg()
    client = LlmClient(cfg)
    mock_anthropic = MagicMock()

    if errors:
        mock_anthropic.messages.create = AsyncMock(side_effect=errors)
    elif responses:
        mock_anthropic.messages.create = AsyncMock(side_effect=responses)
    else:
        mock_anthropic.messages.create = AsyncMock(return_value=_make_anthropic_response())

    client._anthropic_client = mock_anthropic
    return client, mock_anthropic


def _make_nvidia_client(cfg=None, responses=None, errors=None):
    """Create LlmClient with mocked NVIDIA client."""
    cfg = cfg or _nvidia_cfg()
    client = LlmClient(cfg)
    mock_nvidia = MagicMock()

    if errors:
        mock_nvidia.chat.completions.create = AsyncMock(side_effect=errors)
    elif responses:
        mock_nvidia.chat.completions.create = AsyncMock(side_effect=responses)
    else:
        mock_nvidia.chat.completions.create = AsyncMock(return_value=_make_nvidia_response())

    client._nvidia_client = mock_nvidia
    return client, mock_nvidia


# ── Anthropic tests (backward compat) ──


@pytest.mark.asyncio
async def test_successful_call():
    """Happy path: single successful call returns text and latency."""
    client, mock = _make_client()
    text, latency_ms = await client.call("test prompt")
    assert text == '{"action":"BUY"}'
    assert latency_ms >= 0
    mock.messages.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_timeout_retries_then_raises():
    """TimeoutError on all attempts → LlmTimeoutError."""
    client, mock = _make_client(errors=[
        asyncio.TimeoutError(), asyncio.TimeoutError(), asyncio.TimeoutError(),
    ])
    with patch("kindshot.llm_client.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(LlmTimeoutError):
            await client.call("test", max_retries=3)
    assert mock.messages.create.await_count == 3


@pytest.mark.asyncio
async def test_timeout_recovers_on_retry():
    """First call times out, second succeeds."""
    client, mock = _make_client()
    mock.messages.create = AsyncMock(side_effect=[
        asyncio.TimeoutError(), _make_anthropic_response("recovered"),
    ])
    with patch("kindshot.llm_client.asyncio.sleep", new_callable=AsyncMock):
        text, _ = await client.call("test", max_retries=3)
    assert text == "recovered"
    assert mock.messages.create.await_count == 2


@pytest.mark.asyncio
async def test_call_error_retries_then_raises():
    """Non-timeout error on all attempts → LlmCallError."""
    client, mock = _make_client(errors=[
        RuntimeError("api down"), RuntimeError("api down"), RuntimeError("api down"),
    ])
    with patch("kindshot.llm_client.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(LlmCallError, match="api down"):
            await client.call("test", max_retries=3)
    assert mock.messages.create.await_count == 3


@pytest.mark.asyncio
async def test_rate_limit_uses_longer_backoff():
    """Rate limit errors (429) use longer delay (2^(attempt+1))."""
    client, mock = _make_client()
    mock.messages.create = AsyncMock(side_effect=[
        RuntimeError("rate limit 429"), _make_anthropic_response("ok"),
    ])
    sleep_mock = AsyncMock()
    with patch("kindshot.llm_client.asyncio.sleep", sleep_mock):
        text, _ = await client.call("test", max_retries=3)
    assert text == "ok"
    # Rate limit backoff: min(2^(0+1), 16) = 2 seconds
    sleep_mock.assert_awaited_once()
    assert sleep_mock.await_args[0][0] == 2


@pytest.mark.asyncio
async def test_empty_response_raises_call_error():
    """Response with empty content list → LlmCallError."""
    client, _ = _make_client()
    client._anthropic_client.messages.create = AsyncMock(
        return_value=SimpleNamespace(content=[])
    )
    with pytest.raises(LlmCallError, match="unexpected response structure"):
        await client.call("test")


@pytest.mark.asyncio
async def test_semaphore_limits_concurrency():
    """Semaphore limits concurrent LLM calls."""
    cfg = _cfg(llm_max_concurrency=1)
    client, mock = _make_client(cfg=cfg)

    call_order = []

    async def slow_create(**kwargs):
        call_order.append("start")
        await asyncio.sleep(0.05)
        call_order.append("end")
        return _make_anthropic_response()

    mock.messages.create = slow_create

    results = await asyncio.gather(
        client.call("a"), client.call("b"),
    )
    assert len(results) == 2
    assert call_order == ["start", "end", "start", "end"]


@pytest.mark.asyncio
async def test_lazy_client_init():
    """Client is lazily initialized on first call."""
    cfg = _cfg()
    client = LlmClient(cfg)
    assert client._anthropic_client is None
    mock_anthropic = MagicMock()
    mock_anthropic.messages.create = AsyncMock(return_value=_make_anthropic_response())
    client._anthropic_client = mock_anthropic
    await client.call("test")
    mock_anthropic.messages.create.assert_awaited_once()


# ── Circuit breaker tests (Anthropic) ──


@pytest.mark.asyncio
async def test_circuit_breaker_opens_on_credit_error():
    """크레딧 부족 에러 시 circuit breaker가 열리고 즉시 실패."""
    client, mock = _make_client(errors=[
        RuntimeError("Your credit balance is too low to access the Anthropic API"),
    ])
    with pytest.raises(LlmCallError, match="credit balance"):
        await client.call("test", max_retries=3)
    assert mock.messages.create.await_count == 1
    assert client.circuit_open


@pytest.mark.asyncio
async def test_circuit_breaker_fast_fails_subsequent():
    """Circuit open 상태에서 후속 호출은 API 호출 없이 즉시 실패."""
    client, mock = _make_client(errors=[
        RuntimeError("credit balance is too low"),
    ])
    with pytest.raises(LlmCallError):
        await client.call("first", max_retries=3)
    with pytest.raises(LlmCallError, match="circuit breaker open"):
        await client.call("second", max_retries=3)
    assert mock.messages.create.await_count == 1


@pytest.mark.asyncio
async def test_circuit_breaker_resets_on_success():
    """Circuit이 열려도 cooldown 후 성공하면 리셋."""
    client, mock = _make_client()
    client._circuit_open_until = 0.0
    client._circuit_reason = "test"
    text, _ = await client.call("test")
    assert text == '{"action":"BUY"}'
    assert not client.circuit_open


@pytest.mark.asyncio
async def test_circuit_breaker_ignores_transient_errors():
    """일반 에러(rate limit 등)는 circuit breaker를 열지 않음."""
    client, mock = _make_client(errors=[
        RuntimeError("api down"), RuntimeError("api down"), RuntimeError("api down"),
    ])
    with patch("kindshot.llm_client.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(LlmCallError):
            await client.call("test", max_retries=3)
    assert not client.circuit_open
    assert mock.messages.create.await_count == 3


# ── NVIDIA NIM tests ──


@pytest.mark.asyncio
async def test_nvidia_successful_call():
    """NVIDIA NIM happy path."""
    client, mock = _make_nvidia_client()
    text, latency_ms = await client.call("test prompt")
    assert text == '{"action":"BUY"}'
    assert latency_ms >= 0
    mock.chat.completions.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_nvidia_timeout_retries():
    """NVIDIA timeout retries then raises."""
    client, mock = _make_nvidia_client(cfg=_nvidia_cfg(anthropic_api_key=""), errors=[
        asyncio.TimeoutError(), asyncio.TimeoutError(), asyncio.TimeoutError(),
    ])
    with patch("kindshot.llm_client.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(LlmCallError, match="NVIDIA failed"):
            await client.call("test", max_retries=3)


@pytest.mark.asyncio
async def test_nvidia_fallback_to_anthropic():
    """NVIDIA 실패 시 Anthropic fallback."""
    cfg = _nvidia_cfg(anthropic_api_key="test-key")
    client = LlmClient(cfg)

    # NVIDIA fails
    mock_nvidia = MagicMock()
    mock_nvidia.chat.completions.create = AsyncMock(side_effect=RuntimeError("nvidia down"))
    client._nvidia_client = mock_nvidia

    # Anthropic succeeds
    mock_anthropic = MagicMock()
    mock_anthropic.messages.create = AsyncMock(return_value=_make_anthropic_response("fallback_ok"))
    client._anthropic_client = mock_anthropic

    with patch("kindshot.llm_client.asyncio.sleep", new_callable=AsyncMock):
        text, _ = await client.call("test", max_retries=1)
    assert text == "fallback_ok"
    mock_anthropic.messages.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_nvidia_no_fallback_without_anthropic_key():
    """NVIDIA 실패 + Anthropic key 없으면 에러."""
    cfg = _nvidia_cfg(anthropic_api_key="")
    client = LlmClient(cfg)

    mock_nvidia = MagicMock()
    mock_nvidia.chat.completions.create = AsyncMock(side_effect=RuntimeError("nvidia down"))
    client._nvidia_client = mock_nvidia

    with patch("kindshot.llm_client.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(LlmCallError, match="NVIDIA failed"):
            await client.call("test", max_retries=1)


@pytest.mark.asyncio
async def test_nvidia_circuit_breaker():
    """NVIDIA permanent error opens nvidia circuit, falls back to Anthropic."""
    cfg = _nvidia_cfg(anthropic_api_key="test-key")
    client = LlmClient(cfg)

    mock_nvidia = MagicMock()
    mock_nvidia.chat.completions.create = AsyncMock(side_effect=RuntimeError("unauthorized"))
    client._nvidia_client = mock_nvidia

    mock_anthropic = MagicMock()
    mock_anthropic.messages.create = AsyncMock(return_value=_make_anthropic_response("via_anthropic"))
    client._anthropic_client = mock_anthropic

    with patch("kindshot.llm_client.asyncio.sleep", new_callable=AsyncMock):
        text, _ = await client.call("test", max_retries=1)
    assert text == "via_anthropic"
    assert client.nvidia_circuit_open


@pytest.mark.asyncio
async def test_nvidia_circuit_skips_to_anthropic():
    """NVIDIA circuit open → Anthropic으로 바로 라우팅."""
    cfg = _nvidia_cfg(anthropic_api_key="test-key")
    client = LlmClient(cfg)
    # Manually open nvidia circuit
    import time
    client._nvidia_circuit_open_until = time.monotonic() + 999

    mock_nvidia = MagicMock()
    mock_nvidia.chat.completions.create = AsyncMock()
    client._nvidia_client = mock_nvidia

    mock_anthropic = MagicMock()
    mock_anthropic.messages.create = AsyncMock(return_value=_make_anthropic_response("direct_anthropic"))
    client._anthropic_client = mock_anthropic

    text, _ = await client.call("test")
    assert text == "direct_anthropic"
    # NVIDIA should NOT have been called
    mock_nvidia.chat.completions.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_provider_raises():
    """No NVIDIA key + no Anthropic key → error."""
    cfg = Config(nvidia_api_key="", anthropic_api_key="", llm_provider="nvidia")
    client = LlmClient(cfg)
    with pytest.raises(LlmCallError, match="No LLM provider available"):
        await client.call("test")
