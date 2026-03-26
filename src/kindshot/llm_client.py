"""Shared LLM client with exponential backoff retry and rate limit detection."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from kindshot.config import Config

logger = logging.getLogger(__name__)

# 재시도해도 무의미한 영구 에러 패턴 (크레딧 부족, 인증 실패 등)
_PERMANENT_ERROR_PATTERNS = (
    "credit balance is too low",
    "invalid x-api-key",
    "invalid api key",
    "authentication_error",
)

# Circuit breaker 쿨다운: 영구 에러 감지 후 이 시간 동안 LLM 호출 차단
_CIRCUIT_BREAKER_COOLDOWN_S = 300  # 5분


class LlmTimeoutError(Exception):
    """LLM call timed out after all retries."""


class LlmCallError(Exception):
    """LLM call failed (non-timeout)."""


class LlmClient:
    """Shared async Anthropic client with retry, semaphore, and rate limit handling."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._client: Optional[object] = None
        self._semaphore = asyncio.Semaphore(max(1, config.llm_max_concurrency))
        # Circuit breaker: 영구 에러 시 즉시 fail-fast
        self._circuit_open_until: float = 0.0
        self._circuit_reason: str = ""

    def _get_client(self):
        if self._client is None:
            from anthropic import AsyncAnthropic
            self._client = AsyncAnthropic(
                api_key=self._config.anthropic_api_key,
                timeout=self._config.llm_sdk_timeout_s,
            )
        return self._client

    def _is_permanent_error(self, err: Exception) -> bool:
        """재시도 무의미한 영구 에러 여부."""
        msg = str(err).lower()
        return any(pat in msg for pat in _PERMANENT_ERROR_PATTERNS)

    def _open_circuit(self, reason: str) -> None:
        self._circuit_open_until = time.monotonic() + _CIRCUIT_BREAKER_COOLDOWN_S
        self._circuit_reason = reason
        logger.warning("LLM circuit breaker OPEN for %ds: %s", _CIRCUIT_BREAKER_COOLDOWN_S, reason)

    @property
    def circuit_open(self) -> bool:
        return time.monotonic() < self._circuit_open_until

    async def call(
        self,
        prompt: str,
        *,
        max_tokens: int = 200,
        temperature: float = 0,
        max_retries: int = 3,
    ) -> tuple[str, int]:
        """Call LLM with retry. Returns (raw_text, latency_ms).

        Raises LlmTimeoutError or LlmCallError on final failure.
        """
        # Circuit breaker: 영구 에러 상태면 즉시 fail
        if self.circuit_open:
            raise LlmCallError(f"circuit breaker open: {self._circuit_reason}")

        client = self._get_client()
        last_err: Exception | None = None
        resp = None
        t0 = time.monotonic()

        for attempt in range(max_retries):
            try:
                async with self._semaphore:
                    resp = await asyncio.wait_for(
                        client.messages.create(
                            model=self._config.llm_model,
                            max_tokens=max_tokens,
                            temperature=temperature,
                            messages=[{"role": "user", "content": prompt}],
                        ),
                        timeout=self._config.llm_wait_for_s,
                    )
                # 성공 시 circuit breaker 리셋
                if self._circuit_open_until > 0:
                    logger.info("LLM circuit breaker CLOSED (call succeeded)")
                    self._circuit_open_until = 0.0
                    self._circuit_reason = ""
                break
            except asyncio.TimeoutError as e:
                last_err = e
                if attempt < max_retries - 1:
                    delay = min(2 ** attempt, 8)
                    logger.info("LLM timeout (attempt %d/%d), retry in %ds", attempt + 1, max_retries, delay)
                    await asyncio.sleep(delay)
                    continue
                logger.warning("LLM timeout (final after %d attempts)", max_retries)
                raise LlmTimeoutError(str(e)) from e
            except Exception as e:
                last_err = e
                # 영구 에러: 재시도 없이 즉시 circuit breaker 열고 실패
                if self._is_permanent_error(e):
                    self._open_circuit(str(e)[:200])
                    raise LlmCallError(str(e)) from e
                is_rate_limit = "rate" in str(e).lower() or "429" in str(e)
                if attempt < max_retries - 1:
                    delay = min(2 ** (attempt + 1), 16) if is_rate_limit else min(2 ** attempt, 8)
                    logger.info("LLM %s (attempt %d/%d), retry in %ds",
                                "rate limited" if is_rate_limit else "call error",
                                attempt + 1, max_retries, delay)
                    await asyncio.sleep(delay)
                    continue
                logger.warning("LLM call error (final after %d attempts): %s", max_retries, e)
                raise LlmCallError(str(e)) from e

        latency_ms = int((time.monotonic() - t0) * 1000)
        if resp is None:
            raise LlmCallError(f"LLM failed after {max_retries} retries: {last_err}")

        try:
            raw_text = resp.content[0].text
        except (IndexError, AttributeError) as e:
            raise LlmCallError(f"unexpected response structure: {e}") from e

        return raw_text, latency_ms
