"""Tests for llm_client.py — retry, timeout, rate limit, response parsing."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kindshot.config import Config
from kindshot.llm_client import LlmClient, LlmCallError, LlmTimeoutError


def _cfg(**kw) -> Config:
    return Config(anthropic_api_key="test-key", llm_wait_for_s=1.0, llm_sdk_timeout_s=2.0, **kw)


def _make_response(text: str = '{"action":"BUY"}'):
    """Create a mock Anthropic response object."""
    block = SimpleNamespace(text=text)
    return SimpleNamespace(content=[block])


def _make_client(cfg=None, responses=None, errors=None):
    """Create LlmClient with mocked Anthropic client."""
    cfg = cfg or _cfg()
    client = LlmClient(cfg)
    mock_anthropic = MagicMock()

    if errors:
        mock_anthropic.messages.create = AsyncMock(side_effect=errors)
    elif responses:
        mock_anthropic.messages.create = AsyncMock(side_effect=responses)
    else:
        mock_anthropic.messages.create = AsyncMock(return_value=_make_response())

    client._client = mock_anthropic
    return client, mock_anthropic


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
    client, mock = _make_client(errors=[
        asyncio.TimeoutError(), _make_response("ok"),
    ])
    # Override side_effect to return on second call
    mock.messages.create = AsyncMock(side_effect=[
        asyncio.TimeoutError(), _make_response("recovered"),
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
    client, mock = _make_client(errors=[
        RuntimeError("rate limit 429"), _make_response("ok"),
    ])
    mock.messages.create = AsyncMock(side_effect=[
        RuntimeError("rate limit 429"), _make_response("ok"),
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
    client._client.messages.create = AsyncMock(
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
        return _make_response()

    mock.messages.create = slow_create

    # Launch 2 concurrent calls
    results = await asyncio.gather(
        client.call("a"), client.call("b"),
    )
    assert len(results) == 2
    # With semaphore=1, calls should be serialized: start,end,start,end
    assert call_order == ["start", "end", "start", "end"]


@pytest.mark.asyncio
async def test_lazy_client_init():
    """Client is lazily initialized on first call."""
    cfg = _cfg()
    client = LlmClient(cfg)
    assert client._client is None
    # Mock _get_client to avoid real Anthropic import
    mock_anthropic = MagicMock()
    mock_anthropic.messages.create = AsyncMock(return_value=_make_response())
    client._client = mock_anthropic
    await client.call("test")
    mock_anthropic.messages.create.assert_awaited_once()
