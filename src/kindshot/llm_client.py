"""Shared LLM client with NVIDIA NIM (primary) and Anthropic (fallback).

NVIDIA NIM uses OpenAI-compatible API format, so we use the openai async client.
If NVIDIA fails, automatically falls back to Anthropic.
"""

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
    "unauthorized",
    "invalid_api_key",
)

# Circuit breaker 쿨다운: 영구 에러 감지 후 이 시간 동안 해당 provider 호출 차단
_CIRCUIT_BREAKER_COOLDOWN_S = 3600  # 1시간 (크레딧 부족 등 영구 에러는 짧은 쿨다운 무의미)


class LlmTimeoutError(Exception):
    """LLM call timed out after all retries."""


class LlmCallError(Exception):
    """LLM call failed (non-timeout)."""


class LlmClient:
    """Async LLM client: NVIDIA NIM (primary) → Anthropic (fallback)."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._nvidia_client: Optional[object] = None
        self._anthropic_client: Optional[object] = None
        self._semaphore = asyncio.Semaphore(max(1, config.llm_max_concurrency))
        # Circuit breaker per provider
        self._circuit_open_until: float = 0.0
        self._circuit_reason: str = ""
        self._nvidia_circuit_open_until: float = 0.0
        self._nvidia_circuit_reason: str = ""

    # --- Provider clients (lazy init) ---

    def _get_nvidia_client(self):
        if self._nvidia_client is None:
            from openai import AsyncOpenAI
            self._nvidia_client = AsyncOpenAI(
                api_key=self._config.nvidia_api_key,
                base_url=self._config.nvidia_base_url,
                timeout=self._config.llm_sdk_timeout_s,
            )
        return self._nvidia_client

    def _get_anthropic_client(self):
        if self._anthropic_client is None:
            from anthropic import AsyncAnthropic
            self._anthropic_client = AsyncAnthropic(
                api_key=self._config.anthropic_api_key,
                timeout=self._config.llm_sdk_timeout_s,
            )
        return self._anthropic_client

    # --- Legacy compatibility ---

    def _get_client(self):
        """Legacy accessor — returns Anthropic client for backward compat."""
        return self._get_anthropic_client()

    # --- Circuit breaker ---

    def _is_permanent_error(self, err: Exception) -> bool:
        """재시도 무의미한 영구 에러 여부."""
        msg = str(err).lower()
        return any(pat in msg for pat in _PERMANENT_ERROR_PATTERNS)

    def _open_circuit(self, reason: str) -> None:
        self._circuit_open_until = time.monotonic() + _CIRCUIT_BREAKER_COOLDOWN_S
        self._circuit_reason = reason
        logger.warning("LLM circuit breaker OPEN for %ds: %s", _CIRCUIT_BREAKER_COOLDOWN_S, reason)

    def _open_nvidia_circuit(self, reason: str) -> None:
        self._nvidia_circuit_open_until = time.monotonic() + _CIRCUIT_BREAKER_COOLDOWN_S
        self._nvidia_circuit_reason = reason
        logger.warning("NVIDIA circuit breaker OPEN for %ds: %s", _CIRCUIT_BREAKER_COOLDOWN_S, reason)

    @property
    def circuit_open(self) -> bool:
        return time.monotonic() < self._circuit_open_until

    @property
    def nvidia_circuit_open(self) -> bool:
        return time.monotonic() < self._nvidia_circuit_open_until

    # --- NVIDIA NIM call (OpenAI-compatible) ---

    async def _call_nvidia(
        self,
        prompt: str,
        *,
        max_tokens: int = 200,
        temperature: float = 0,
        max_retries: int = 2,
    ) -> tuple[str, int]:
        """Call NVIDIA NIM API. Returns (raw_text, latency_ms)."""
        client = self._get_nvidia_client()
        last_err: Exception | None = None
        t0 = time.monotonic()

        for attempt in range(max_retries):
            try:
                async with self._semaphore:
                    resp = await asyncio.wait_for(
                        client.chat.completions.create(
                            model=self._config.nvidia_model,
                            max_tokens=max_tokens,
                            temperature=temperature,
                            messages=[{"role": "user", "content": prompt}],
                        ),
                        timeout=self._config.llm_wait_for_s,
                    )
                latency_ms = int((time.monotonic() - t0) * 1000)
                # Reset nvidia circuit on success
                if self._nvidia_circuit_open_until > 0:
                    logger.info("NVIDIA circuit breaker CLOSED (call succeeded)")
                    self._nvidia_circuit_open_until = 0.0
                    self._nvidia_circuit_reason = ""
                raw_text = resp.choices[0].message.content
                return raw_text, latency_ms
            except asyncio.TimeoutError as e:
                last_err = e
                if attempt < max_retries - 1:
                    delay = min(2 ** attempt, 4)
                    logger.info("NVIDIA timeout (attempt %d/%d), retry in %ds", attempt + 1, max_retries, delay)
                    await asyncio.sleep(delay)
                    continue
            except Exception as e:
                last_err = e
                if self._is_permanent_error(e):
                    self._open_nvidia_circuit(str(e)[:200])
                    break
                if attempt < max_retries - 1:
                    delay = min(2 ** attempt, 4)
                    logger.info("NVIDIA error (attempt %d/%d): %s, retry in %ds", attempt + 1, max_retries, e, delay)
                    await asyncio.sleep(delay)
                    continue
                break

        raise LlmCallError(f"NVIDIA failed after {max_retries} attempts: {last_err}")

    # --- Anthropic call ---

    async def _call_anthropic(
        self,
        prompt: str,
        *,
        max_tokens: int = 200,
        temperature: float = 0,
        max_retries: int = 3,
    ) -> tuple[str, int]:
        """Call Anthropic API. Returns (raw_text, latency_ms)."""
        if self.circuit_open:
            raise LlmCallError(f"circuit breaker open: {self._circuit_reason}")

        client = self._get_anthropic_client()
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

    # --- Unified call with fallback ---

    async def call(
        self,
        prompt: str,
        *,
        max_tokens: int = 200,
        temperature: float = 0,
        max_retries: int = 3,
    ) -> tuple[str, int]:
        """Call LLM with fallback. Returns (raw_text, latency_ms).

        Primary: NVIDIA NIM (free tier)
        Fallback: Anthropic (if NVIDIA fails and Anthropic key is available)

        Raises LlmTimeoutError or LlmCallError on final failure.
        """
        use_nvidia = (
            self._config.llm_provider == "nvidia"
            and self._config.nvidia_api_key
            and not self.nvidia_circuit_open
        )

        if use_nvidia:
            logger.debug("LLM call: using NVIDIA NIM (%s)", self._config.nvidia_model)
            try:
                return await self._call_nvidia(
                    prompt, max_tokens=max_tokens, temperature=temperature, max_retries=max_retries,
                )
            except (LlmCallError, LlmTimeoutError) as nvidia_err:
                # Fallback to Anthropic if available
                if self._config.anthropic_api_key and not self.circuit_open:
                    logger.warning("NVIDIA failed (%s), falling back to Anthropic", nvidia_err)
                    return await self._call_anthropic(
                        prompt, max_tokens=max_tokens, temperature=temperature, max_retries=max_retries,
                    )
                # Anthropic도 불가하면 NVIDIA 에러를 그대로 raise
                logger.error("NVIDIA failed and Anthropic unavailable (circuit=%s, key=%s)",
                             self.circuit_open, bool(self._config.anthropic_api_key))
                raise
        elif self._config.llm_provider == "nvidia":
            skip_reason = "no_api_key" if not self._config.nvidia_api_key else f"circuit_open:{self._nvidia_circuit_reason[:80]}"
            logger.warning("NVIDIA skipped (%s), trying Anthropic", skip_reason)

        # Anthropic as primary (no NVIDIA key or circuit open)
        if self._config.anthropic_api_key:
            return await self._call_anthropic(
                prompt, max_tokens=max_tokens, temperature=temperature, max_retries=max_retries,
            )

        raise LlmCallError("No LLM provider available (NVIDIA and Anthropic both unconfigured)")
